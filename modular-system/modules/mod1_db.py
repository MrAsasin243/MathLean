"""
MATHPROOF — Модуль 1: База данных
===================================
Иммутабельный модуль инициализации и доступа к SQLite БД.
Контракт: contracts/database_schema.sql (§5 основного документа)

Публичный API:
    - get_db() -> sqlite3.Connection
    - init_db() -> None
    - init_database (алиас init_db)

Зависимости: только stdlib (sqlite3, os)
"""

import os
import sqlite3
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------
# Путь к БД: переменная окружения MP_DB_PATH или дефолт data/mathproof.db
# относительно корня проекта (два уровня вверх от modules/)
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "mathproof.db",
)
DB_PATH = os.environ.get("MP_DB_PATH", _DEFAULT_DB_PATH)

# ---------------------------------------------------------------------------
# Схема БД (§5 основного документа)
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
-- Пользователи
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE,
    pass_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','admin')) DEFAULT 'user',
    status TEXT NOT NULL CHECK(status IN ('active','banned')) DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

-- Сессии
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ua TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Задачи
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    difficulty INTEGER NOT NULL CHECK(difficulty BETWEEN 1 AND 5),
    xp INTEGER NOT NULL,
    statement_latex TEXT NOT NULL,
    library TEXT,
    skeleton_lean TEXT,
    solution_lean TEXT,
    solution_board_json TEXT,
    category TEXT,
    subcategory TEXT,
    tags TEXT,
    prereqs TEXT,
    status TEXT NOT NULL CHECK(status IN ('draft','published','archived')) DEFAULT 'draft',
    wave INTEGER DEFAULT 0
);

-- Доказательства (множественность, §11)
CREATE TABLE IF NOT EXISTS proofs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    method TEXT NOT NULL,
    tags TEXT,
    lean_source TEXT,
    board_json TEXT,
    construction_json TEXT,
    figure_json TEXT,
    author_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Посылки пользователей
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('board','code','geo2d','geo3d')),
    payload_json TEXT,
    lean_source TEXT,
    status TEXT NOT NULL CHECK(status IN ('accepted','rejected','error')),
    diagnostics_json TEXT,
    preview INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Прогресс пользователя по задачам
CREATE TABLE IF NOT EXISTS progress (
    user_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    solved INTEGER NOT NULL DEFAULT 0,
    xp INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, task_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Доски (автосохранение, §9.4)
CREATE TABLE IF NOT EXISTS boards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    graph_json TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, task_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Построения (2D/3D, §10)
CREATE TABLE IF NOT EXISTS constructions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    dim INTEGER NOT NULL CHECK(dim IN (2,3)),
    objects_json TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, task_id, dim),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- Журнал аудита (§14)
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE SET NULL
);

-- Настройки системы (§14)
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Голоса за доказательства (§11.5)
CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proof_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('elegant','clarity','visual')),
    UNIQUE(proof_id, user_id, kind),
    FOREIGN KEY (proof_id) REFERENCES proofs(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Комментарии к доказательствам (§11.5)
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proof_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    block_ref TEXT,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (proof_id) REFERENCES proofs(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Индексы для производительности
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_tasks_category ON tasks(category);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_difficulty ON tasks(difficulty);
CREATE INDEX IF NOT EXISTS idx_proofs_task_id ON proofs(task_id);
CREATE INDEX IF NOT EXISTS idx_submissions_user_id ON submissions(user_id);
CREATE INDEX IF NOT EXISTS idx_submissions_task_id ON submissions(task_id);
CREATE INDEX IF NOT EXISTS idx_progress_user_id ON progress(user_id);
CREATE INDEX IF NOT EXISTS idx_boards_user_task ON boards(user_id, task_id);
CREATE INDEX IF NOT EXISTS idx_constructions_user_task ON constructions(user_id, task_id);
CREATE INDEX IF NOT EXISTS idx_audit_admin_id ON audit(admin_id);
CREATE INDEX IF NOT EXISTS idx_votes_proof_id ON votes(proof_id);
CREATE INDEX IF NOT EXISTS idx_comments_proof_id ON comments(proof_id);
"""

# ---------------------------------------------------------------------------
# Функции
# ---------------------------------------------------------------------------

def init_db() -> None:
    """
    Инициализация базы данных: создание директории и всех таблиц.
    Идемпотентна — безопасна для повторного вызова.
    """
    # Создаём директорию data/ если её нет
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    
    # Подключаемся и выполняем схему
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


# Алиас для совместимости
init_database = init_db


@contextmanager
def get_db():
    """
    Контекстный менеджер для получения соединения с БД.
    Автоматически закрывает соединение после использования.
    
    Использование:
        with get_db() as db:
            db.execute("SELECT ...")
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def get_db_connection() -> sqlite3.Connection:
    """
    Прямое получение соединения (без контекстного менеджера).
    Для случаев, когда нужно управлять соединением вручную.
    
    ВАЖНО: вызывающий код обязан закрыть соединение!
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Автоинициализация при импорте (опционально)
# ---------------------------------------------------------------------------
# Раскомментируйте, если нужно автоматическое создание БД при первом импорте:
# init_db()


# ---------------------------------------------------------------------------
# Тестовый запуск
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"Инициализация БД: {DB_PATH}")
    init_db()
    print("✓ База данных успешно создана/обновлена")
    
    # Проверка: выводим список таблиц
    with get_db() as db:
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        print(f"✓ Таблицы ({len(tables)}): {', '.join(tables)}")