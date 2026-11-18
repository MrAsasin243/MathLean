-- ============================================================================
-- MATHPROOF — Схема базы данных SQLite (контракт)
-- Источник: §5 документа "иструкция.md"
-- Модуль: mod1_db.py обязан воспроизвести эту схему дословно (идемпотентно).
-- Никаких внешних ключей с ON DELETE CASCADE — все связи логические.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Пользователи и аутентификация
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    email         TEXT    UNIQUE,                              -- NULL допускается
    pass_hash     TEXT    NOT NULL,
    salt          TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('user', 'admin')) DEFAULT 'user',
    status        TEXT    NOT NULL CHECK (status IN ('active', 'banned')) DEFAULT 'active',
    created_at    TEXT    NOT NULL,                            -- ISO-8601
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT    PRIMARY KEY,                           -- opaque hex(32)
    user_id     INTEGER NOT NULL,                              -- FK → users.id
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    ua          TEXT                                           -- User-Agent
);

-- ----------------------------------------------------------------------------
-- Каталог задач
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT    NOT NULL UNIQUE,
    title               TEXT    NOT NULL,
    difficulty          INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    xp                  INTEGER NOT NULL,
    statement_latex     TEXT    NOT NULL,
    library             TEXT    NOT NULL,                      -- JSON-массив имён лемм
    skeleton_lean       TEXT,
    solution_lean       TEXT,
    solution_board_json TEXT,
    category            TEXT,                                  -- linal|analysis|cattheory
    subcategory         TEXT,                                  -- spaces|maps|...
    tags                TEXT,                                  -- JSON-массив
    prereqs             TEXT,                                  -- JSON-массив slug'ов
    status              TEXT    NOT NULL CHECK (status IN ('draft', 'published', 'archived')) DEFAULT 'draft',
    wave                INTEGER NOT NULL DEFAULT 0
);

-- ----------------------------------------------------------------------------
-- Множественные доказательства (§11)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS proofs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id           INTEGER NOT NULL,                        -- FK → tasks.id
    title             TEXT    NOT NULL,
    method            TEXT    NOT NULL,                        -- algebraic|geometric|inductive|contradiction|combinatorial|probabilistic
    tags              TEXT,                                    -- JSON: elegant|visual|elementary|advanced|constructive|surprising
    lean_source       TEXT,
    board_json        TEXT,
    construction_json TEXT,
    figure_json       TEXT,                                    -- декларативный чертёж
    author_id         INTEGER,                                 -- FK → users.id
    created_at        TEXT    NOT NULL
);

-- ----------------------------------------------------------------------------
-- Посылки пользователей
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS submissions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,                         -- FK → users.id
    task_id          INTEGER NOT NULL,                         -- FK → tasks.id
    mode             TEXT    NOT NULL CHECK (mode IN ('board', 'code', 'geo2d', 'geo3d')),
    payload_json     TEXT,
    lean_source      TEXT,
    status           TEXT    NOT NULL CHECK (status IN ('accepted', 'rejected', 'error')),
    diagnostics_json TEXT,
    preview          INTEGER NOT NULL DEFAULT 0,               -- 1 = принято preview-слоем без Lean
    created_at       TEXT    NOT NULL
);

-- ----------------------------------------------------------------------------
-- Прогресс пользователя по задачам (суммарный XP)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS progress (
    user_id    INTEGER NOT NULL,                               -- FK → users.id
    task_id    INTEGER NOT NULL,                               -- FK → tasks.id
    solved     INTEGER NOT NULL DEFAULT 0,
    xp         INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (user_id, task_id)
);

-- ----------------------------------------------------------------------------
-- Автосохранение досок и построений (офлайн-кэш на сервере)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS boards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,                             -- FK → users.id
    task_id      INTEGER NOT NULL,                             -- FK → tasks.id
    graph_json   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE (user_id, task_id)
);

CREATE TABLE IF NOT EXISTS constructions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,                             -- FK → users.id
    task_id      INTEGER NOT NULL,                             -- FK → tasks.id
    dim          INTEGER NOT NULL CHECK (dim IN (2, 3)),
    objects_json TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    UNIQUE (user_id, task_id, dim)
);

-- ----------------------------------------------------------------------------
-- Аудит действий администраторов (§14)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id   INTEGER NOT NULL,                               -- FK → users.id
    action     TEXT    NOT NULL,
    target     TEXT    NOT NULL,
    details    TEXT,
    created_at TEXT    NOT NULL
);

-- ----------------------------------------------------------------------------
-- Глобальные настройки сайта
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ----------------------------------------------------------------------------
-- Голоса за доказательства и комментарии (§11.5)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS votes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    proof_id  INTEGER NOT NULL,                                -- FK → proofs.id
    user_id   INTEGER NOT NULL,                                -- FK → users.id
    kind      TEXT    NOT NULL CHECK (kind IN ('elegant', 'clarity', 'visual')),
    UNIQUE (proof_id, user_id, kind)
);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    proof_id   INTEGER NOT NULL,                               -- FK → proofs.id
    user_id    INTEGER NOT NULL,                               -- FK → users.id
    block_ref  TEXT,                                           -- опциональная привязка к блоку
    body       TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

-- ============================================================================
-- Индексы для типичных запросов API (§6)
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_sessions_user     ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires  ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_tasks_category    ON tasks(category, subcategory);
CREATE INDEX IF NOT EXISTS idx_tasks_status      ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_wave        ON tasks(wave);
CREATE INDEX IF NOT EXISTS idx_proofs_task       ON proofs(task_id);
CREATE INDEX IF NOT EXISTS idx_submissions_user  ON submissions(user_id);
CREATE INDEX IF NOT EXISTS idx_submissions_task  ON submissions(task_id);
CREATE INDEX IF NOT EXISTS idx_progress_user     ON progress(user_id);
CREATE INDEX IF NOT EXISTS idx_boards_user_task  ON boards(user_id, task_id);
CREATE INDEX IF NOT EXISTS idx_constructions_utd ON constructions(user_id, task_id, dim);
CREATE INDEX IF NOT EXISTS idx_audit_admin       ON audit(admin_id);
CREATE INDEX IF NOT EXISTS idx_votes_proof       ON votes(proof_id);
CREATE INDEX IF NOT EXISTS idx_comments_proof    ON comments(proof_id);