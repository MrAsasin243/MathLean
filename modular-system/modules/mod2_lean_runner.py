# modular-system/modules/mod2_lean_runner.py
# Модуль 2: Движок верификации Lean 4.
# Иммутабельный: после написания не изменяется.
# Контракт: run_lean_verification(lean_source, task_library) -> dict
#           check_lean_installed() -> dict

import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

# ============================================================================
# Константы контракта (зафиксированы, не меняются)
# ============================================================================

# Корень репозитория: модуль лежит в modular-system/modules/,
# значит корень проекта — ../../ от этого файла.
_MODULE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _MODULE_DIR.parent.parent
_LEAN_PROJECT_DIR = _PROJECT_ROOT / "lean"
_TEMP_MODULE_NAME = "TempModularSolution"
_TEMP_MODULE_PATH = _LEAN_PROJECT_DIR / "MathProof" / f"{_TEMP_MODULE_NAME}.lean"

# Ограничения подпроцесса (согласно §7.3)
_LEAN_TIMEOUT_SEC = 20
_RLIMIT_CPU_SEC = 15
_RLIMIT_AS_BYTES = 1 * 1024 * 1024 * 1024  # 1 ГБ

# Очередь: не более 2 проверок одновременно (§7.3)
_VERIFICATION_SEMAPHORE = threading.Semaphore(2)

# Regex парсинга stderr (§7.5)
_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s*(?P<sev>warning|error):\s*(?P<msg>.+)$"
)

# Признак неполного доказательства: warning, содержащий "sorry" (§7.5)
_SORRY_MARKER = "sorry"


# ============================================================================
# Вспомогательные функции
# ============================================================================

def _resolve_lean_executable() -> Optional[str]:
    """Возвращает путь к `lake` из PATH или MP_LEAN_HOME, либо None."""
    env_home = os.environ.get("MP_LEAN_HOME")
    if env_home:
        candidate = Path(env_home) / "bin" / "lake"
        if candidate.exists():
            return str(candidate)
    # fallback: ищем в PATH
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / "lake"
        if candidate.exists():
            return str(candidate)
    return None


def check_lean_installed() -> dict:
    """
    Проверка наличия Lean ≥ 4.0 в окружении.
    Возвращает:
      {"installed": True,  "version": "Lean 4.x.y", "lake_path": "..."}
      {"installed": False, "reason": "...", "instructions": "..."}
    Используется сервером (mod4) для отдачи HTTP 503 при отсутствии Lean.
    """
    lake_path = _resolve_lean_executable()
    if lake_path is None:
        return {
            "installed": False,
            "reason": "lake не найден в PATH и MP_LEAN_HOME",
            "instructions": (
                "Установите elan: curl -sSfL "
                "https://github.com/leanprover/elan/releases/download/"
                "v3.1.1/elan-x86_64-unknown-linux-gnu.tar.gz | tar xz && "
                "./elan-init -y --default-toolchain stable. "
                "Или запустите: python tools/install_lean.py"
            ),
        }
    try:
        result = subprocess.run(
            [lake_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(_LEAN_PROJECT_DIR),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "installed": False,
            "reason": f"ошибка запуска lake: {exc}",
            "instructions": "Проверьте корректность установки elan/lean.",
        }

    output = (result.stdout or "") + (result.stderr or "")
    version_match = re.search(r"Lean\s+(v?\d+\.\d+\.\d+[^\s]*)", output)
    if not version_match:
        return {
            "installed": False,
            "reason": "не удалось определить версию Lean",
            "instructions": "Убедитесь, что установлен Lean ≥ 4.0.",
        }
    version_str = version_match.group(1).lstrip("v")
    major = int(version_str.split(".")[0])
    if major < 4:
        return {
            "installed": False,
            "reason": f"требуется Lean ≥ 4.0, найдено {version_str}",
            "instructions": "Обновите toolchain: elan default stable",
        }
    return {
        "installed": True,
        "version": f"Lean {version_str}",
        "lake_path": lake_path,
    }


def _build_lean_source(lean_source: str, task_library: list) -> str:
    """
    Собирает финальный Lean-источник: прелюдия импортов + тело пользователя.
    Imports: import MathProof.<lib> для каждого lib из task_library.
    """
    lines = ["-- AUTOMODULAR SOLUTION (generated)"]
    for lib in task_library or []:
        safe_lib = str(lib).strip()
        if safe_lib:
            lines.append(f"import MathProof.{safe_lib}")
    lines.append("")
    lines.append(lean_source.rstrip())
    lines.append("")
    return "\n".join(lines)


def _write_temp_module(full_source: str) -> Path:
    """Атомарная запись временного модуля в lean/MathProof/."""
    _TEMP_MODULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Запись через tmp-файл + rename для атомарности на случай параллельных проверок.
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{_TEMP_MODULE_NAME}.",
        suffix=".lean",
        dir=str(_TEMP_MODULE_PATH.parent),
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(full_source)
        os.replace(tmp_path, _TEMP_MODULE_PATH)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    return _TEMP_MODULE_PATH


def _cleanup_temp_module() -> None:
    """Удаление временного .lean (но не .olean — их убирает lake clean при необходимости)."""
    try:
        if _TEMP_MODULE_PATH.exists():
            _TEMP_MODULE_PATH.unlink()
    except OSError:
        pass


def _set_rlimits() -> None:
    """
    Установки resource limits для подпроцесса (только Unix).
    На Windows — no-op (ограничение только по таймауту).
    """
    try:
        import resource  # type: ignore
    except ImportError:
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (_RLIMIT_CPU_SEC, _RLIMIT_CPU_SEC))
        resource.setrlimit(resource.RLIMIT_AS, (_RLIMIT_AS_BYTES, _RLIMIT_AS_BYTES))
    except (ValueError, resource.error):
        # Если лимиты не применимы (например, в контейнере) — продолжаем без них.
        pass


def _parse_diagnostics(stderr_text: str) -> list:
    """
    Парсит stderr Lean по контракту §7.5.
    Возвращает список словарей:
      {line, col, severity, message, is_sorry}
    Warning, содержащий "sorry", помечается is_sorry=True
    и приравнивается к ошибке «неполное доказательство».
    """
    diagnostics = []
    for raw_line in stderr_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _DIAGNOSTIC_RE.match(line)
        if not match:
            continue
        sev = match.group("sev")
        msg = match.group("msg").strip()
        is_sorry = (sev == "warning") and (_SORRY_MARKER in msg.lower())
        diagnostics.append({
            "file": match.group("file"),
            "line": int(match.group("line")),
            "col": int(match.group("col")),
            "severity": "error" if is_sorry else sev,
            "message": (
                "неполное доказательство (обнаружен sorry)" if is_sorry else msg
            ),
            "is_sorry": is_sorry,
        })
    return diagnostics


# ============================================================================
# Главная функция контракта
# ============================================================================

def run_lean_verification(lean_source: str, task_library: list) -> dict:
    """
    Компиляция и проверка Lean 4 исходника.

    Параметры:
      lean_source  — тело доказательства (без импортов).
      task_library — список имён модулей из library задачи
                     (например: ["LinearAlgebra", "Analysis"]).

    Возвращает словарь:
      {
        "status":        "accepted" | "rejected" | "error",
        "diagnostics":   [...],          # список разобранных диагностик
        "lean_source":   "...",          # финальный источник (с импортами)
        "elapsed_ms":    int,            # время выполнения, мс
        "error_type":    str | None,     # "lean_missing" | "timeout" | "crash" | None
        "error_message": str | None,     # человекочитаемое описание для status="error"
      }

    Семантика status:
      - "accepted"  — нет error-диагностик (warning без sorry допустимы).
      - "rejected"  — есть хотя бы одна error-диагностика (включая sorry).
      - "error"     — инфраструктурная ошибка (Lean не установлен / таймаут /
                      сбой процесса). В этом поле diagnostics может быть пустым.
    """
    # 1. Проверка наличия Lean
    lean_info = check_lean_installed()
    if not lean_info["installed"]:
        return {
            "status": "error",
            "diagnostics": [],
            "lean_source": "",
            "elapsed_ms": 0,
            "error_type": "lean_missing",
            "error_message": lean_info["reason"] + " | " + lean_info["instructions"],
        }

    # 2. Подготовка исходника
    full_source = _build_lean_source(lean_source or "", task_library or [])

    # 3. Запись во временный модуль (под семафором, чтобы не было гонки
    #    записи в один и тот же файл при параллельных вызовах).
    _VERIFICATION_SEMAPHORE.acquire()
    started_at = time.monotonic()
    try:
        _write_temp_module(full_source)

        # 4. Запуск lake env lean
        cmd = ["lake", "env", "lean", f"MathProof/{_TEMP_MODULE_NAME}.lean"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_LEAN_TIMEOUT_SEC,
                cwd=str(_LEAN_PROJECT_DIR),
                preexec_fn=_set_rlimits,
                env={**os.environ},
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            return {
                "status": "error",
                "diagnostics": [],
                "lean_source": full_source,
                "elapsed_ms": elapsed_ms,
                "error_type": "timeout",
                "error_message": f"превышен таймаут {_LEAN_TIMEOUT_SEC} с",
            }
        except OSError as exc:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            return {
                "status": "error",
                "diagnostics": [],
                "lean_source": full_source,
                "elapsed_ms": elapsed_ms,
                "error_type": "crash",
                "error_message": f"сбой запуска подпроцесса: {exc}",
            }

        elapsed_ms = int((time.monotonic() - started_at) * 1000)

        # 5. Парсинг stderr
        diagnostics = _parse_diagnostics(proc.stderr or "")

        # 6. Определение статуса
        has_errors = any(d["severity"] == "error" for d in diagnostics)
        status = "rejected" if has_errors else "accepted"

        return {
            "status": status,
            "diagnostics": diagnostics,
            "lean_source": full_source,
            "elapsed_ms": elapsed_ms,
            "error_type": None,
            "error_message": None,
        }
    finally:
        _cleanup_temp_module()
        _VERIFICATION_SEMAPHORE.release()


# ============================================================================
# Самопроверка модуля (запускается только при прямом вызове)
# ============================================================================

if __name__ == "__main__":
    # Минимальный smoke-тест контракта без реального Lean:
    # проверяем, что при отсутствии Lean возвращается корректный error-пакет.
    info = check_lean_installed()
    print("[mod2] check_lean_installed ->", info)

    sample = run_lean_verification(
        lean_source = "example : 1 = 1 := by rfl",
        task_library = ["LinearAlgebra"],
    )
    print("[mod2] run_lean_verification ->", {
        "status": sample["status"],
        "error_type": sample["error_type"],
        "elapsed_ms": sample["elapsed_ms"],
        "diagnostics_count": len(sample["diagnostics"]),
    })