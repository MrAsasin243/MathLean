"""
mod3_transpiler.py — Модуль 3 системы MATHPROOF (Immutable Micro-Module).

КОНТРАКТ (не изменять после публикации):
    board_to_lean(board_json: str) -> tuple[str, dict]
        Граф доски (§9) -> Lean-источник теоремы + block_map:
        {block_id: [start_line, end_line]} (строки 1-based, включительно).
    construction_to_lean(construction_json: str) -> tuple[str, dict]
        Геометрическое построение (§10) -> координатный Lean-источник +
        construction_map: {object_id | step_id: [start_line, end_line]}.

Границы ответственности:
    - Модуль НЕ добавляет import-прелюдию и скелет задачи: это делает
      mod4_api_server при записи временного модуля (§7.2).
    - Модуль НЕ проверяет истинность утверждений: численная проверка —
      geo_interp (preview), формальная — ядро Lean (§7).
    - Ошибки входа (некорректный JSON, цикл графа, отсутствие goal и т.п.)
      поднимаются как TranspilerError с текстом на русском языке.

Зависимости: только стандартная библиотека Python 3.11+.
"""

from __future__ import annotations

import json
import re
from fractions import Fraction

__all__ = ["board_to_lean", "construction_to_lean", "TranspilerError"]

_CONTRACT_VERSION = "1.0.0"


class TranspilerError(ValueError):
    """Ошибка входных данных транспайлера (текст — на русском)."""


# ---------------------------------------------------------------------------
# Общие вспомогательные средства
# ---------------------------------------------------------------------------

_IDENT_START = r"[^\W\d]"          # буква (в т.ч. греческая) или '_'
_IDENT_CONT = r"[\w']*"
_IDENT_RE = re.compile(_IDENT_START + _IDENT_CONT + r"$")


def _load_json(payload: str, what: str) -> dict:
    """Разбирает JSON-строку входа; объект верхнего уровня обязан быть dict."""
    if not isinstance(payload, str):
        raise TranspilerError(f"вход {what}: ожидалась JSON-строка")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TranspilerError(f"вход {what}: некорректный JSON ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise TranspilerError(f"вход {what}: верхний уровень должен быть объектом")
    return data


def _id_key(block_id: str):
    """Детерминированный порядок: числовой суффикс раньше, затем лексикографика."""
    m = re.fullmatch(r"([A-Za-zА-я_]*)(\d+)", block_id)
    if m:
        return (0, int(m.group(2)), m.group(1))
    return (1, 0, block_id)


def _hyp_name(raw_id) -> str:
    """Имя гипотезы Lean для блока/шага: b3 -> h_b3, h1 -> h1, s2 -> h_s2."""
    s = re.sub(r"[^\w']", "_", str(raw_id))
    if re.fullmatch(r"h\d+", s):
        return s
    return "h_" + s


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Делит строку по разделителю вне скобок ()[]{}⟨⟩."""
    parts, depth, cur = [], 0, []
    opening, closing = "([{⟨", ")]}⟩"
    for ch in text:
        if ch in opening:
            depth += 1
        elif ch in closing:
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts]


def _split_relation(text: str) -> tuple[str, str]:
    """Отделяет выражение до первого relation-оператора верхнего уровня."""
    depth, i = 0, 0
    opening, closing = "([{⟨", ")]}⟩"
    while i < len(text):
        ch = text[i]
        if ch in opening:
            depth += 1
        elif ch in closing:
            depth -= 1
        elif depth == 0 and ch in "=≠≤≥<>↔→":
            return text[:i].strip(), text[i:]
        i += 1
    return text.strip(), ""


class _Emitter:
    """Накопитель строк с нумерацией 1-based для построения карт."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, text: str = "") -> int:
        self.lines.append(text)
        return len(self.lines)

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


# ---------------------------------------------------------------------------
# Линейный математический синтаксис (§9.1) -> Lean-терм
# ---------------------------------------------------------------------------

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "omicron": "ο",
    "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_GREEK_RE = re.compile(
    r"\b(" + "|".join(sorted(_GREEK, key=len, reverse=True)) + r")\b"
)

_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ", "0123456789n")
_SUBSCRIPT = str.maketrans("₀₁₂₃₄₅₆₇₈₉ₙ", "0123456789n")

_SYMBOLS = {
    "·": "*", "⋅": "*", "∗": "*", "×": "*",
    "⇒": "→", "⇔": "↔", "−": "-", "–": "-",
}

_INLINE_REF_RE = re.compile(r"\[(" + _IDENT_START + _IDENT_CONT + r")\]")
_CALL_RE = re.compile(r"(" + _IDENT_START + _IDENT_CONT + r")\(([^()]*)\)")
_MAT_TYPE_RE = re.compile(r"\bmat\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*\)")
_INT_RE = re.compile(
    r"\bint_(\{[^{}]*\}|[^\s^{}]+)\^(\{[^{}]*\}|[^\s{}]+)\s+(.+?)\s+d("
    + _IDENT_START + r")"
)
_LIM_RE = re.compile(
    r"\blim_\{\s*(" + _IDENT_START + _IDENT_CONT + r")\s*->\s*([^{}]*)\}"
)
_SUM_RE = re.compile(
    r"\bsum_\{\s*(" + _IDENT_START + _IDENT_CONT + r")\s*=\s*([^{}]*)\}"
    r"\^(\{[^{}]*\}|[^\s{}]+)"
)


def _read_braced(s: str, i: int) -> tuple[str, int]:
    """Читает balanced-группу {…}, начиная с s[i] == '{'. Возвращает (тело, конец)."""
    depth, j = 0, i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise TranspilerError("незакрытая фигурная скобка в выражении: " + s)


def _expand_frac_sqrt(s: str) -> str:
    """frac{a}{b} -> ((a) / (b)); sqrt{x} -> (Real.sqrt (x)). Вложенность — циклом."""
    while True:
        m = re.search(r"\b(frac|sqrt)\{", s)
        if not m:
            return s
        head, tail = s[:m.start()], s[m.end() - 1:]
        first, rest_i = _read_braced(tail, 0)
        if m.group(1) == "frac":
            rest = tail[rest_i:].lstrip()
            if not rest.startswith("{"):
                raise TranspilerError("frac ожидает две группы {…}{…}: " + s)
            second, rest_i2 = _read_braced(rest, 0)
            repl = f"(({first}) / ({second}))"
            s = head + repl + rest[rest_i2:]
        else:
            repl = f"(Real.sqrt ({first}))"
            s = head + repl + tail[rest_i:]


def _read_bracketed(s: str, i: int) -> tuple[str, int]:
    """Читает balanced-группу […], начиная с s[i] == '['."""
    depth, j = 0, i
    while j < len(s):
        if s[j] == "[":
            depth += 1
        elif s[j] == "]":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise TranspilerError("незакрытая квадратная скобка в выражении: " + s)


def _expand_mat_literal(s: str) -> str:
    """mat[[1,2],[3,4]] -> !![1, 2; 3, 4] (Mathlib-совместимая нотация)."""
    while True:
        m = re.search(r"\bmat\[", s)
        if not m:
            return s
        body, end_i = _read_bracketed(s, m.end() - 1)
        rows = []
        for row in _split_top_level(body):
            if not (row.startswith("[") and row.endswith("]")):
                raise TranspilerError("mat-литерал ожидает строки вида [a,b]: " + row)
            cells = _split_top_level(row[1:-1])
            if not cells or cells == [""]:
                raise TranspilerError("пустая строка mat-литерала")
            rows.append(", ".join(cells))
        s = s[:m.start()] + "!![" + "; ".join(rows) + "]" + s[end_i:]


def _strip_braces(token: str) -> str:
    token = token.strip()
    if token.startswith("{") and token.endswith("}"):
        return token[1:-1].strip()
    return token


def _expand_int_lim_sum(s: str) -> str:
    """int_a^b f dx, lim_{x->a} f, sum_{k=1}^n f -> нотации Lean/Mathlib."""
    def int_repl(m: re.Match) -> str:
        lo, hi = _strip_braces(m.group(1)), _strip_braces(m.group(2))
        return f"∫ {m.group(4)} in {lo}..{hi}, {m.group(3).strip()}"

    s = _INT_RE.sub(int_repl, s)

    while True:
        m = _LIM_RE.search(s)
        if not m:
            break
        head, rest = _split_relation(s[m.end():])
        repl = f"lim (fun {m.group(1)} => {head}) {m.group(2).strip()}"
        s = s[:m.start()] + repl + rest

    while True:
        m = _SUM_RE.search(s)
        if not m:
            break
        head, rest = _split_relation(s[m.end():])
        lo, hi = m.group(2).strip(), _strip_braces(m.group(3))
        repl = f"∑ {m.group(1)} in Finset.Icc {lo} {hi}, {head}"
        s = s[:m.start()] + repl + rest
    return s


def _expand_calls(s: str) -> str:
    """f(a,b) -> f (a) (b): каррирование, внутренние вызовы первыми."""
    while True:
        m = _CALL_RE.search(s)
        if not m:
            return s
        args = [a for a in _split_top_level(m.group(2)) if a != ""]
        repl = m.group(1) if not args else m.group(1) + " " + " ".join(
            f"({a})" for a in args
        )
        s = s[:m.start()] + repl + s[m.end():]


def _linear_to_lean(text: str) -> tuple[str, list[str]]:
    """
    Линейный мат-синтаксис доски -> Lean-текст.
    Возвращает (текст, inline_refs): ссылки [bN] извлекаются как имена
    используемых блоков и удаляются из текста утверждения (§9.3).
    """
    inline_refs: list[str] = []

    def ref_repl(m: re.Match) -> str:
        inline_refs.append(m.group(1))
        return " "

    s = _INLINE_REF_RE.sub(ref_repl, text)
    s = _expand_frac_sqrt(s)
    s = _expand_mat_literal(s)
    s = _expand_int_lim_sum(s)
    s = _MAT_TYPE_RE.sub(r"Matrix \1 \2", s)
    s = _expand_calls(s)
    s = _GREEK_RE.sub(lambda m: _GREEK[m.group(1)], s)
    s = re.sub(r"\b(pi|I|O)\b",
               lambda m: {"pi": "Real.pi", "I": "1", "O": "0"}[m.group(1)], s)
    s = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ]+",
               lambda m: "^" + m.group(0).translate(_SUPERSCRIPT), s)
    s = re.sub(r"[₀₁₂₃₄₅₆₇₈₉ₙ]+",
               lambda m: "_" + m.group(0).translate(_SUBSCRIPT), s)
    for old, new in _SYMBOLS.items():
        s = s.replace(old, new)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s, inline_refs


# ---------------------------------------------------------------------------
# Доска -> Lean (§9.5)
# ---------------------------------------------------------------------------

_EDGE_KINDS = {
    "sufficient": ("⇒", "достаточно"), "⇒": ("⇒", "достаточно"),
    "=>": ("⇒", "достаточно"),
    "necessary": ("⇐", "необходимо"), "⇐": ("⇐", "необходимо"),
    "<=": ("⇐", "необходимо"),
    "iff": ("⇔", "необходимо и достаточно"), "⇔": ("⇔", "необходимо и достаточно"),
    "<=>": ("⇔", "необходимо и достаточно"),
}


def _edge_order(edge: dict, ids: set[str]) -> tuple[str, str]:
    """Пара (раньше, позже) для топсорта. iff для порядка = sufficient."""
    src, dst = str(edge.get("from", "")), str(edge.get("to", ""))
    if src not in ids or dst not in ids:
        raise TranspilerError(f"ребро ссылается на несуществующий блок: {src} -> {dst}")
    kind = str(edge.get("kind", "sufficient"))
    if kind not in _EDGE_KINDS:
        raise TranspilerError(f"неизвестный тип соединения: {kind}")
    if kind in ("necessary", "⇐", "<="):
        return dst, src
    return src, dst


def _toposort(ids: list[str], deps: dict[str, set[str]]) -> list[str]:
    """Кан с детерминированным tie-break по _id_key; цикл -> ошибка."""
    indeg = {i: 0 for i in ids}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for later, pres in deps.items():
        for earlier in pres:
            if earlier in indeg and later in indeg:
                adj[earlier].append(later)
                indeg[later] += 1
    ready = sorted((i for i in ids if indeg[i] == 0), key=_id_key)
    out: list[str] = []
    while ready:
        node = ready.pop(0)
        out.append(node)
        for nxt in sorted(adj[node], key=_id_key):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
                ready.sort(key=_id_key)
    if len(out) != len(ids):
        rest = sorted(set(ids) - set(out), key=_id_key)
        raise TranspilerError("цикл в графе доски; блоки вне порядка: " + ", ".join(rest))
    return out


def _just_parts(block: dict, inline_refs: list[str]) -> tuple[str, list[str]]:
    """(правило-терм, список имён гипотез-ссылок) из just и inline [bN]."""
    just = block.get("just") or {}
    rule = str(just.get("rule") or "").strip()
    refs: list[str] = []
    for r in list(just.get("refs") or []) + inline_refs:
        name = _hyp_name(r)
        if name not in refs:
            refs.append(name)
    return rule, refs


def board_to_lean(board_json: str) -> tuple[str, dict]:
    """
    Граф доски -> (Lean-источник теоремы board_proof, block_map).
    Формат входа: {"blocks": [{id,x,y,type,content,just}], "edges": [{from,to,kind}]}.
    """
    data = _load_json(board_json, "доски")
    raw_blocks = data.get("blocks")
    raw_edges = data.get("edges", [])
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise TranspilerError("доска: список blocks пуст или отсутствует")
    if not isinstance(raw_edges, list):
        raise TranspilerError("доска: edges должен быть списком")

    blocks: dict[str, dict] = {}
    for b in raw_blocks:
        bid = str(b.get("id", ""))
        if not bid or bid in blocks:
            raise TranspilerError(f"доска: пустой или повторяющийся id блока: {bid!r}")
        btype = b.get("type")
        if btype not in ("statement", "definition", "given", "goal"):
            raise TranspilerError(f"доска: неизвестный тип блока {bid}: {btype}")
        blocks[bid] = b

    goals = [i for i, b in blocks.items() if b["type"] == "goal"]
    if len(goals) != 1:
        raise TranspilerError("доска: требуется ровно один блок goal (§9.1)")
    goal_id = goals[0]
    given_ids = sorted((i for i, b in blocks.items() if b["type"] == "given"), key=_id_key)
    body_ids = [i for i, b in blocks.items() if b["type"] in ("statement", "definition")]

    deps: dict[str, set[str]] = {i: set() for i in body_ids}
    for e in raw_edges:
        earlier, later = _edge_order(e, set(blocks))
        if later in deps and earlier in deps:
            deps[later].add(earlier)
    order = _toposort(body_ids, deps)

    em = _Emitter()
    block_map: dict[str, list[int]] = {}
    em.add("-- Сгенерировано mod3_transpiler: режим «Доска»")
    em.add("theorem board_proof")

    # given -> параметры/гипотезы теоремы; неявные размерности из mat(...).
    declared: set[str] = set()
    parsed_given: list[tuple[str, str, str]] = []
    dims: set[str] = set()
    for gid in given_ids:
        content = str(blocks[gid].get("content", ""))
        m = re.match(r"^\s*(" + _IDENT_START + _IDENT_CONT + r")\s*:\s*(.+)$",
                     content, re.S)
        if m:
            name, typ = m.group(1), _linear_to_lean(m.group(2))[0]
        else:
            name, typ = _hyp_name(gid), _linear_to_lean(content)[0]
        declared.add(name)
        parsed_given.append((gid, name, typ))
        for dm in re.finditer(r"\bmat\(([^)]*)\)", content):
            for tok in _split_top_level(dm.group(1)):
                if _IDENT_RE.fullmatch(tok) and not tok.isdigit():
                    dims.add(tok)
    for d in sorted(dims - declared):
        em.add(f"    {{{d} : ℕ}}")
    for gid, name, typ in parsed_given:
        line = em.add(f"    ({name} : {typ})")
        block_map[gid] = [line, line]

    goal_stmt, goal_inline = _linear_to_lean(str(blocks[goal_id].get("content", "")))
    if not goal_stmt:
        raise TranspilerError("доска: пустое утверждение goal")
    goal_start = em.add(f"    : {goal_stmt} := by")

    # Тело: топсортированный порядок statement/definition.
    for bid in order:
        b = blocks[bid]
        start = em.add(f"-- {bid}")
        incoming = []
        for e in raw_edges:
            if str(e.get("to")) == bid:
                sym = _EDGE_KINDS[str(e.get("kind", "sufficient"))][0]
                incoming.append(f"{e.get('from')} {sym} {bid}")
        if incoming:
            em.add("-- связи: " + ", ".join(sorted(incoming)))
        content = str(b.get("content", ""))
        if b["type"] == "definition":
            m = re.match(r"^\s*(" + _IDENT_START + _IDENT_CONT + r")\s*(?::|=)\s*(.+)$",
                         content, re.S)
            if m:
                name, term = m.group(1), _linear_to_lean(m.group(2))[0]
            else:
                name, term = "d_" + bid, _linear_to_lean(content)[0]
            end = em.add(f"  let {name} := {term}")
        else:
            stmt, inline = _linear_to_lean(content)
            rule, refs = _just_parts(b, inline)
            h = _hyp_name(bid)
            if rule and refs:
                end = em.add(f"  have {h} : {stmt} := {rule} {' '.join(refs)}")
            elif rule:
                end = em.add(f"  have {h} : {stmt} := {rule}")
            elif refs:
                end = em.add(f"  have {h} : {stmt} := by rw [{', '.join(refs)}]")
            else:
                end = em.add(f"  have {h} : {stmt} := by sorry")
        block_map[bid] = [start, end]

    # goal -> exact / rw-цепочка; без обоснования — sorry (даст диагностику).
    rule, refs = _just_parts(blocks[goal_id], goal_inline)
    if rule and refs:
        goal_end = em.add(f"  exact {rule} {' '.join(refs)}")
    elif rule:
        goal_end = em.add(f"  exact {rule}")
    elif refs:
        goal_end = em.add(f"  rw [{', '.join(refs)}]")
    else:
        goal_end = em.add("  sorry")
    block_map[goal_id] = [goal_start, goal_end]

    # Псевдокод связок графа (§9.2) — комментариями, контракт его не возвращает.
    if raw_edges:
        em.add("-- Псевдокод графа:")
        for e in sorted(raw_edges, key=lambda x: (str(x.get("from")), str(x.get("to")))):
            kind = str(e.get("kind", "sufficient"))
            sym, label = _EDGE_KINDS[kind]
            em.add(f"-- {e.get('from')} {sym} {e.get('to')}  ({label})")
    return em.text(), block_map


# ---------------------------------------------------------------------------
# Геометрическое построение -> координатный Lean (§10.6)
# ---------------------------------------------------------------------------

_GEO_PREDICATES = ("dist", "ang", "dot", "mid", "midpoint",
                   "perp", "perp_plane", "in_plane")
_ANGLE_RULES = {"base_angles", "angle_split", "triangle_sum", "angle_chase"}


def _fmt_coord(value) -> str:
    """Точное рациональное из десятичной записи: 0.5 -> (1 / 2), 3 -> 3."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TranspilerError(f"координата должна быть числом: {value!r}")
    f = Fraction(str(value))
    if f.denominator == 1:
        return str(f.numerator)
    return f"({f.numerator} / {f.denominator})"


def _parse_script_text(text: str) -> list[dict]:
    """Lean-подобный текст скрипта (§10.4): given / have / goal / exact."""
    steps: list[dict] = []
    n = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        n += 1
        m = re.match(r"^given\s+(.+)$", line)
        if m:
            steps.append({"id": f"s{n}", "kind": "given", "stmt": m.group(1)})
            continue
        m = re.match(r"^have\s+(" + _IDENT_START + _IDENT_CONT +
                     r")\s*:\s*(.+?)\s*:=\s*by\s+(.+)$", line)
        if m:
            tail = m.group(3).split()
            steps.append({"id": m.group(1), "kind": "have", "stmt": m.group(2),
                          "rule": tail[0] if tail else "",
                          "refs": [t.rstrip(",") for t in tail[1:]]})
            continue
        m = re.match(r"^goal\s+(.+)$", line)
        if m:
            steps.append({"id": f"s{n}", "kind": "goal", "stmt": m.group(1)})
            continue
        m = re.match(r"^exact\s+(.+)$", line)
        if m:
            tail = m.group(1).split()
            steps.append({"id": f"s{n}", "kind": "exact", "rule": tail[0],
                          "refs": [t.rstrip(",") for t in tail[1:]]})
            continue
        raise TranspilerError("нераспознанная строка скрипта: " + line)
    return steps


def _geo_tactic(stmt_lean: str, rule: str, point_ids: list[str]) -> str:
    """
    Координатный сертификат: разворачиваем определения упомянутых точек и
    предикатов, затем ring (для углов/деления — field_simp; ring).
    """
    mentioned = sorted(
        (p for p in point_ids
         if re.search(r"\b" + re.escape(p) + r"\b", stmt_lean)),
        key=lambda p: stmt_lean.index(p),
    )
    preds = [p for p in _GEO_PREDICATES
             if re.search(r"\b" + p + r"\b", stmt_lean)]
    names = mentioned + preds
    angle = (rule in _ANGLE_RULES
             or re.search(r"\bang\b", stmt_lean) is not None
             or "/" in stmt_lean)
    head = f"simp only [{', '.join(names)}]; " if names else ""
    return head + ("field_simp; ring" if angle else "ring")


def construction_to_lean(construction_json: str) -> tuple[str, dict]:
    """
    Построение -> (координатный Lean-источник теоремы geo_proof, construction_map).
    Формат входа: {"dim": 2|3, "objects": [{id,kind,coords|pos,...}],
                   "script": [шаги] | "текст скрипта"}.
    Шаг: {id, kind: given|have|exact|goal, stmt?, rule?, refs?}.
    """
    data = _load_json(construction_json, "построения")
    dim = data.get("dim", 2)
    if dim not in (2, 3):
        raise TranspilerError("построение: dim должен быть 2 или 3")
    objects = data.get("objects", [])
    script = data.get("script", [])
    if not isinstance(objects, list):
        raise TranspilerError("построение: objects должен быть списком")
    if isinstance(script, str):
        script = _parse_script_text(script)
    if not isinstance(script, list) or not script:
        raise TranspilerError("построение: скрипт пуст")

    steps: list[dict] = []
    seen_ids: set[str] = set()
    for s in script:
        sid = str(s.get("id", ""))
        kind = s.get("kind")
        if not sid or sid in seen_ids:
            raise TranspilerError(f"построение: пустой или повторяющийся id шага: {sid!r}")
        if kind not in ("given", "have", "exact", "goal"):
            raise TranspilerError(f"построение: неизвестный вид шага {sid}: {kind}")
        seen_ids.add(sid)
        steps.append(s)
    goals = [s for s in steps if s["kind"] == "goal"]
    if len(goals) != 1 or steps[-1]["kind"] != "goal":
        raise TranspilerError("построение: требуется ровно один goal, и он должен быть последним")
    goal = goals[0]

    em = _Emitter()
    cmap: dict[str, list[int]] = {}
    em.add(f"-- Сгенерировано mod3_transpiler: режим «Геометрия» ({dim}D)")

    # Объекты: точки -> координатные def; прочие объекты — комментарии-реестр.
    point_ids: list[str] = []
    for obj in objects:
        oid = str(obj.get("id", ""))
        if not oid:
            raise TranspilerError("построение: объект без id")
        coords = obj.get("coords", obj.get("pos"))
        kind = str(obj.get("kind", "object"))
        if isinstance(coords, (list, tuple)) and len(coords) == dim:
            if not _IDENT_RE.fullmatch(oid):
                raise TranspilerError(f"построение: недопустимое имя точки Lean: {oid!r}")
            body = ", ".join(_fmt_coord(c) for c in coords)
            line = em.add(f"noncomputable def {oid} : Point{dim} := ⟨{body}⟩")
            point_ids.append(oid)
        else:
            deps = ""
            for key in ("through", "deps", "points", "from"):
                if isinstance(obj.get(key), (list, tuple)) and obj[key]:
                    deps = " (от " + ", ".join(str(x) for x in obj[key]) + ")"
                    break
            note = "" if coords is not None else " — координаты не заданы"
            line = em.add(f"-- объект {oid} : {kind}{deps}{note}")
        cmap[oid] = [line, line]
    em.add("")

    em.add("theorem geo_proof")
    for s in steps:
        if s["kind"] != "given":
            continue
        stmt, _ = _linear_to_lean(str(s.get("stmt", "")))
        line = em.add(f"    ({_hyp_name(s['id'])} : {stmt})")
        cmap[str(s["id"])] = [line, line]

    goal_stmt, _ = _linear_to_lean(str(goal.get("stmt", "")))
    if not goal_stmt:
        raise TranspilerError("построение: пустое утверждение goal")
    goal_start = em.add(f"    : {goal_stmt} := by")

    for s in steps:
        kind, sid = s["kind"], str(s["id"])
        if kind in ("given", "goal"):
            continue
        rule = str(s.get("rule") or "").strip()
        refs = [_hyp_name(r) for r in (s.get("refs") or [])]
        start = em.add(f"-- шаг {sid} ({rule or '—'})")
        if kind == "have":
            stmt, _ = _linear_to_lean(str(s.get("stmt", "")))
            tactic = _geo_tactic(stmt, rule, point_ids)
            end = em.add(f"  have {_hyp_name(sid)} : {stmt} := by {tactic}")
        else:  # exact
            if rule:
                term = rule + (" " + " ".join(refs) if refs else "")
            elif refs:
                term = refs[0]
            else:
                raise TranspilerError(f"построение: шаг exact {sid} без правила и ссылок")
            end = em.add(f"  exact {term}")
        cmap[sid] = [start, end]

    em.add("-- цель: координатный сертификат")
    goal_end = em.add("  " + _geo_tactic(goal_stmt, str(goal.get("rule") or ""), point_ids))
    cmap[str(goal["id"])] = [goal_start, goal_end]
    return em.text(), cmap


# ---------------------------------------------------------------------------
# Детерминированный самотест (запуск: python -m modular-system.modules.mod3_transpiler)
# ---------------------------------------------------------------------------

def _self_test() -> None:
    board = {
        "blocks": [
            {"id": "b1", "type": "given", "content": "A:mat(n,n)"},
            {"id": "b2", "type": "given", "content": "B:mat(n,n)"},
            {"id": "b3", "type": "goal", "content": "(A*B)⁻¹ = B⁻¹*A⁻¹",
             "just": {"kind": "rule", "rule": "mul_inv A B"}},
        ],
        "edges": [],
    }
    lean, bmap = board_to_lean(json.dumps(board))
    assert "theorem board_proof" in lean and "{n : ℕ}" in lean
    assert bmap["b3"][0] <= bmap["b3"][1] and "exact mul_inv A B" in lean

    geo = {
        "dim": 2,
        "objects": [
            {"id": "A", "kind": "point", "coords": [0, 0]},
            {"id": "B", "kind": "point", "coords": [1, 0]},
            {"id": "C", "kind": "point", "coords": [1, 1]},
            {"id": "D", "kind": "point", "coords": [0, 1]},
        ],
        "script": [
            {"id": "s1", "kind": "have", "stmt": "dist(A,B) = dist(C,D)",
             "rule": "construction"},
            {"id": "s2", "kind": "goal", "stmt": "dist(A,C) = dist(B,D)",
             "rule": "square_diag"},
        ],
    }
    lean, cmap = construction_to_lean(json.dumps(geo))
    assert "noncomputable def A : Point2 := ⟨0, 0⟩" in lean
    assert "s2" in cmap and "ring" in lean
    print("mod3_transpiler: самотест пройден (контракт " + _CONTRACT_VERSION + ")")


if __name__ == "__main__":
    _self_test()