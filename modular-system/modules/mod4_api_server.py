"""
Модуль 4: FastAPI HTTP-сервер MATHPROOF.
Предоставляет REST API для работы с каталогом задач, проверки Lean-посылок
и получения учебной карты. Использует модули mod1_db (БД) и mod2_lean_runner
(движок верификации).

Контракт:
  - GET  /api/tasks      — каталог задач (фильтр ?category=...)
  - POST /api/verify     — проверка Lean-источника
  - GET  /api/curriculum — учебная карта (группировка по разделам)
  - Middleware: при отсутствии Lean все мутирующие/верифицирующие эндпоинты
    возвращают HTTP 503 с JSON-инструкцией инсталлера.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Импорт соседних модулей модульной системы.
# Интерфейсы зафиксированы в контрактах mod1_db и mod2_lean_runner.
from . import mod1_db as db
from . import mod2_lean_runner as lean_runner

log = logging.getLogger("mathproof.api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Состояние приложения
# ---------------------------------------------------------------------------

class AppState:
    """Кэш состояния сервера: наличие Lean, время старта."""
    lean_available: bool = False
    lean_version: str = ""
    lean_instructions: str = (
        "Lean не найден в PATH. Запустите `python tools/install_lean.py` "
        "для автоматической установки elan и stable toolchain, либо установите "
        "Lean 4 вручную и убедитесь, что `lean --version` возвращает версию >= 4.0."
    )


state = AppState()


# ---------------------------------------------------------------------------
# Проверка наличия Lean
# ---------------------------------------------------------------------------

def detect_lean() -> tuple[bool, str]:
    """
    Проверяет доступность `lean` и `lake` в PATH.
    Возвращает (available, version_string).
    """
    lean_bin = shutil.which("lean")
    lake_bin = shutil.which("lake")
    if not lean_bin or not lake_bin:
        return False, ""
    try:
        result = subprocess.run(
            [lean_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return False, ""
        version_line = (result.stdout or "").strip().splitlines()
        version = version_line[0] if version_line else "unknown"
        return True, version
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("Ошибка при вызове `lean --version`: %s", exc)
        return False, ""


# ---------------------------------------------------------------------------
# Middleware проверки Lean
# ---------------------------------------------------------------------------

async def lean_availability_middleware(request: Request, call_next):
    """
    Для эндпоинта /api/verify (и любых будущих мутирующих верификационных
    маршрутов) блокирует запрос HTTP 503, если Lean не установлен.
    Остальные маршруты (чтение каталога, учебной карты) работают без Lean.
    """
    if request.url.path == "/api/verify" and not state.lean_available:
        return JSONResponse(
            status_code=503,
            content={
                "error": "lean_missing",
                "instructions": state.lean_instructions,
            },
        )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Инициализация FastAPI
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Создаёт и конфигурирует экземпляр приложения."""
    app = FastAPI(
        title="MATHPROOF API",
        version="0.1.0",
        description=(
            "Платформа формальных математических доказательств. "
            "Верификация выполняется ядром Lean 4 на локальном сервере."
        ),
    )

    # CORS не требуется для локального хоста 127.0.0.1:8080, но добавляем
    # минимальную политику на случай разработки с других портов.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.middleware("http")(lean_availability_middleware)

    @app.on_event("startup")
    async def on_startup() -> None:
        """Инициализация БД и детектирование Lean при старте сервера."""
        try:
            db.init_db()
            log.info("База данных инициализирована.")
        except Exception as exc:
            log.exception("Не удалось инициализировать БД: %s", exc)
            raise

        state.lean_available, state.lean_version = detect_lean()
        if state.lean_available:
            log.info("Lean найден: %s", state.lean_version)
        else:
            log.warning(
                "Lean не обнаружен. Эндпоинт /api/verify будет возвращать 503. "
                "Инструкция: %s",
                state.lean_instructions,
            )

    return app


app = create_app()


# ---------------------------------------------------------------------------
# Эндпоинты
# ---------------------------------------------------------------------------

@app.get("/api/tasks")
def get_tasks(
    category: Optional[str] = Query(
        default=None,
        description="Фильтр по категории (linal / analysis / cattheory).",
    ),
    subcategory: Optional[str] = Query(
        default=None,
        description="Фильтр по подразделу.",
    ),
    difficulty: Optional[int] = Query(
        default=None,
        ge=1,
        le=5,
        description="Фильтр по уровню сложности (1–5).",
    ),
) -> dict[str, Any]:
    """
    Возвращает каталог опубликованных задач.
    Поддерживает фильтрацию по category, subcategory, difficulty.
    """
    try:
        rows = db.fetch_tasks(
            category=category,
            subcategory=subcategory,
            difficulty=difficulty,
            status="published",
        )
    except Exception as exc:
        log.exception("Ошибка выборки задач: %s", exc)
        raise HTTPException(status_code=500, detail="Ошибка чтения каталога задач.")

    # Приведение к JSON-совместимому виду.
    tasks = [
        {
            "id": row["id"],
            "slug": row["slug"],
            "title": row["title"],
            "difficulty": row["difficulty"],
            "xp": row["xp"],
            "category": row["category"],
            "subcategory": row["subcategory"],
            "tags": row["tags"],
            "statement_latex": row["statement_latex"],
            "library": row["library"],
            "prereqs": row["prereqs"],
        }
        for row in rows
    ]
    return {"count": len(tasks), "tasks": tasks}


@app.get("/api/curriculum")
def get_curriculum() -> dict[str, Any]:
    """
    Учебная карта: задачи, сгруппированные по (category, subcategory),
    с подсчётом количества задач и суммарного XP в подразделе.
    """
    try:
        rows = db.fetch_tasks(status="published")
    except Exception as exc:
        log.exception("Ошибка построения учебной карты: %s", exc)
        raise HTTPException(status_code=500, detail="Ошибка чтения учебной карты.")

    sections: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        cat = row["category"] or "uncategorized"
        sub = row["subcategory"] or "general"
        sections.setdefault(cat, {}).setdefault(sub, []).append(
            {
                "id": row["id"],
                "slug": row["slug"],
                "title": row["title"],
                "difficulty": row["difficulty"],
                "xp": row["xp"],
                "prereqs": row["prereqs"],
            }
        )

    curriculum = []
    for cat, subs in sections.items():
        subsections = []
        for sub, tasks in subs.items():
            subsections.append(
                {
                    "name": sub,
                    "task_count": len(tasks),
                    "total_xp": sum(t["xp"] or 0 for t in tasks),
                    "tasks": tasks,
                }
            )
        curriculum.append(
            {
                "category": cat,
                "subsection_count": len(subsections),
                "subsections": subsections,
            }
        )
    return {"sections": curriculum}


@app.post("/api/verify")
async def verify(request: Request) -> dict[str, Any]:
    """
    Принимает Lean-источник (или JSON-представление доски/построения),
    передаёт в движок верификации и возвращает результат компиляции.

    Ожидаемый JSON-тело:
      {
        "mode": "code" | "board" | "geo2d" | "geo3d",
        "task_id": int,
        "lean_source": str,            # для mode=code
        "board_json": {...},           # для mode=board
        "construction_json": {...}     # для mode=geo2d/geo3d
      }

    Ответ:
      {
        "status": "accepted" | "rejected" | "error",
        "diagnostics": [{"line": int, "col": int, "severity": str, "message": str}],
        "lean_source": str,
        "elapsed_ms": int,
        "preview": bool
      }
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Ожидалось JSON-тело запроса.")

    mode = payload.get("mode")
    task_id = payload.get("task_id")
    if mode not in ("code", "board", "geo2d", "geo3d"):
        raise HTTPException(status_code=400, detail="Недопустимое значение mode.")
    if not isinstance(task_id, int):
        raise HTTPException(status_code=400, detail="task_id должен быть целым числом.")

    # Извлечение исходника в зависимости от режима.
    lean_source: str
    if mode == "code":
        lean_source = payload.get("lean_source") or ""
        if not lean_source.strip():
            raise HTTPException(status_code=400, detail="lean_source пуст.")
    elif mode == "board":
        board_json = payload.get("board_json")
        if not board_json:
            raise HTTPException(status_code=400, detail="board_json отсутствует.")
        try:
            # Транспиляция доски в Lean-источник делегируется модулю 3
            # (mod3_transpiler), если он доступен; иначе — заглушка с явной ошибкой.
            from . import mod3_transpiler as transpiler  # type: ignore
            lean_source, _block_map = transpiler.board_to_lean(task_id, board_json)
        except ImportError:
            raise HTTPException(
                status_code=501,
                detail="Транспайлер доски (mod3_transpiler) ещё не подключён.",
            )
    else:  # geo2d / geo3d
        construction_json = payload.get("construction_json")
        if not construction_json:
            raise HTTPException(status_code=400, detail="construction_json отсутствует.")
        try:
            from . import mod3_transpiler as transpiler  # type: ignore
            lean_source, _construction_map = transpiler.construction_to_lean(
                task_id, construction_json, dim=2 if mode == "geo2d" else 3
            )
        except ImportError:
            raise HTTPException(
                status_code=501,
                detail="Транспайлер геометрии (mod3_transpiler) ещё не подключён.",
            )

    # Запуск верификации через модуль 2.
    started = time.perf_counter()
    try:
        result = lean_runner.run_lean_verification(
            task_id=task_id,
            lean_source=lean_source,
            mode=mode,
        )
    except Exception as exc:
        log.exception("Сбой в движке Lean: %s", exc)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка движка верификации.")
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    return {
        "status": result.get("status", "error"),
        "diagnostics": result.get("diagnostics", []),
        "lean_source": result.get("lean_source", lean_source),
        "block_map": result.get("block_map"),
        "construction_map": result.get("construction_map"),
        "elapsed_ms": elapsed_ms,
        "preview": bool(result.get("preview", False)),
    }


# ---------------------------------------------------------------------------
# Служебные маршруты
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, Any]:
    """Диагностический эндпоинт: статус БД и Lean."""
    return {
        "status": "ok",
        "lean_available": state.lean_available,
        "lean_version": state.lean_version,
    }


# ---------------------------------------------------------------------------
# Точка входа для `uvicorn modular-system.modules.mod4_api_server:app`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "modular-system.modules.mod4_api_server:app",
        host="127.0.0.1",
        port=8080,
        reload=False,
    )