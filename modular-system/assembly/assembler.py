#!/usr/bin/env python3
"""Сборочный скрипт MATHPROOF Modular System.

Проверяет целостность контрактов и модулей, инициализирует базу данных
через mod1_db.init_db() и выводит инструкции по запуску сервера.
Аварийно завершается с кодом 1 при отсутствии любого обязательного файла.
"""

import sys
import importlib.util
from pathlib import Path

# Корень modular-system/ (на два уровня выше assembly/assembler.py)
BASE_DIR = Path(__file__).resolve().parent.parent

# Обязательные файлы, сгруппированные по назначению
REQUIRED_FILES = {
    "contracts": [
        "contracts/database_schema.sql",
        "contracts/api_contract.yaml",
    ],
    "modules": [
        "modules/mod1_db.py",
        "modules/mod2_lean_runner.py",
        "modules/mod3_transpiler.py",
        "modules/mod4_api_server.py",
        "modules/mod5_styles.css",
        "modules/mod6_frontend_spa.html",
    ],
}


def check_files() -> bool:
    """Проверяет наличие всех требуемых контрактов и модулей."""
    missing = []
    for group, files in REQUIRED_FILES.items():
        for rel in files:
            if not (BASE_DIR / rel).exists():
                missing.append(rel)

    if missing:
        print("⊥ Отсутствуют обязательные файлы:")
        for m in missing:
            print(f"   ✗ {m}")
        return False

    print("∎ Все контракты и модули на месте:")
    for group, files in REQUIRED_FILES.items():
        for rel in files:
            print(f"   ✓ {rel}")
    return True


def init_database() -> bool:
    """Динамически загружает mod1_db и вызывает init_db()."""
    mod_path = BASE_DIR / "modules" / "mod1_db.py"
    spec = importlib.util.spec_from_file_location("mod1_db", mod_path)
    if spec is None or spec.loader is None:
        print("⊥ Не удалось загрузить mod1_db.py")
        return False

    mod = importlib.util.module_from_spec(spec)
    sys.modules["mod1_db"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"⊥ Ошибка выполнения mod1_db.py: {e}")
        return False

    if not hasattr(mod, "init_db"):
        print("⊥ В mod1_db.py отсутствует функция init_db()")
        return False

    try:
        mod.init_db()
        print("∎ База данных инициализирована")
        return True
    except Exception as e:
        print(f"⊥ Ошибка при вызове init_db(): {e}")
        return False


def print_run_instructions() -> None:
    """Выводит инструкции по запуску сервера."""
    print()
    print("─" * 60)
    print("∎ Сборка завершена успешно")
    print("─" * 60)
    print()
    print("Для запуска сервера выполните:")
    print()
    print("  uvicorn modular-system.modules.mod4_api_server:app --reload")
    print()
    print("Сервер будет доступен по адресу: http://127.0.0.1:8000")
    print()
    print("Перед первым запуском убедитесь, что:")
    print("  • Lean 4 установлен:  python tools/install_lean.py")
    print("  • KaTeX загружен:     python tools/install_assets.py")
    print("  • lake build выполнен в каталоге lean/")
    print()


def main() -> int:
    print("∎ MATHPROOF Modular System — сборочный скрипт")
    print("─" * 60)
    print()

    print("[1/2] Проверка целостности файлов...")
    if not check_files():
        return 1
    print()

    print("[2/2] Инициализация базы данных...")
    if not init_database():
        return 1
    print()

    print_run_instructions()
    return 0


if __name__ == "__main__":
    sys.exit(main())