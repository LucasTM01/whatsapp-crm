from db import get_conn

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS clients (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nome          TEXT NOT NULL,
    whatsapp      TEXT NOT NULL UNIQUE,
    email         TEXT,
    empresa       TEXT,
    tickers       TEXT,
    tipo          TEXT,
    tier          INTEGER DEFAULT 2,
    freq_dias     INTEGER DEFAULT 30,
    notas         TEXT,
    ativo         INTEGER DEFAULT 1,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL UNIQUE,
    descricao   TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS client_list (
    client_id   INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    list_id     INTEGER REFERENCES lists(id) ON DELETE CASCADE,
    PRIMARY KEY (client_id, list_id)
);

CREATE TABLE IF NOT EXISTS message_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER REFERENCES clients(id),
    mensagem    TEXT NOT NULL,
    template    TEXT,
    status      TEXT DEFAULT 'sent',
    error_msg   TEXT,
    sent_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_message_log_client_id
    ON message_log(client_id);

CREATE INDEX IF NOT EXISTS idx_message_log_sent_at
    ON message_log(sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_clients_ativo
    ON clients(ativo);

CREATE INDEX IF NOT EXISTS idx_message_log_client_status
    ON message_log(client_id, status);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _add_column_if_missing(conn, table: str, column: str, col_type: str) -> None:
    """SQLite lacks IF NOT EXISTS for ALTER TABLE ADD COLUMN."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA_SQL)
    _add_column_if_missing(conn, "clients", "notion_page_id", "TEXT")
    conn.close()
