import sqlite3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rows(cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def _row(cursor) -> dict | None:
    row = cursor.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def get_all_clients(conn: sqlite3.Connection, ativo_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM clients"
    if ativo_only:
        sql += " WHERE ativo = 1"
    sql += " ORDER BY tier ASC, nome ASC"
    return _rows(conn.execute(sql))


def get_client_by_id(conn: sqlite3.Connection, client_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)))


def create_client(conn: sqlite3.Connection, data: dict) -> int:
    cols = ["nome", "whatsapp", "email", "empresa", "tickers", "tipo", "tier", "freq_dias", "notas", "notion_page_id"]
    fields = [c for c in cols if c in data]
    placeholders = ", ".join("?" for _ in fields)
    col_str = ", ".join(fields)
    values = [data[f] for f in fields]
    cur = conn.execute(
        f"INSERT INTO clients ({col_str}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return cur.lastrowid


def update_client(conn: sqlite3.Connection, client_id: int, data: dict) -> None:
    allowed = ["nome", "whatsapp", "email", "empresa", "tickers", "tipo", "tier", "freq_dias", "notas", "ativo", "notion_page_id"]
    fields = [k for k in allowed if k in data]
    if not fields:
        return
    set_clause = ", ".join(f"{f} = ?" for f in fields)
    values = [data[f] for f in fields] + [client_id]
    conn.execute(
        f"UPDATE clients SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
        values,
    )
    conn.commit()


def archive_client(conn: sqlite3.Connection, client_id: int) -> None:
    conn.execute(
        "UPDATE clients SET ativo = 0, updated_at = datetime('now') WHERE id = ?",
        (client_id,),
    )
    conn.commit()


def reset_clients_notion_page_ids(conn: sqlite3.Connection) -> int:
    """Clear notion_page_id from all clients.

    Use when the Clientes database is recreated in Notion — old page IDs are now
    invalid and must be cleared so push_to_notion re-creates the rows.
    Returns the number of rows updated.
    """
    cur = conn.execute(
        "UPDATE clients SET notion_page_id = NULL WHERE notion_page_id IS NOT NULL"
    )
    conn.commit()
    return cur.rowcount



def get_clients_by_list(conn: sqlite3.Connection, list_id: int) -> list[dict]:
    sql = """
        SELECT c.* FROM clients c
        JOIN client_list cl ON cl.client_id = c.id
        WHERE cl.list_id = ? AND c.ativo = 1
        ORDER BY c.tier ASC, c.nome ASC
    """
    return _rows(conn.execute(sql, (list_id,)))


def get_clients_by_filters(
    conn: sqlite3.Connection,
    tipo: str | None = None,
    tier: int | None = None,
    ticker: str | None = None,
    list_id: int | None = None,
    empresa: str | None = None,
) -> list[dict]:
    conditions = ["c.ativo = 1"]
    params: list = []

    if tipo:
        conditions.append("c.tipo = ?")
        params.append(tipo)
    if tier is not None:
        conditions.append("c.tier = ?")
        params.append(tier)
    if ticker:
        conditions.append("c.tickers LIKE '%' || ? || '%'")
        params.append(ticker.strip().upper())
    if list_id:
        conditions.append("EXISTS (SELECT 1 FROM client_list cl WHERE cl.client_id = c.id AND cl.list_id = ?)")
        params.append(list_id)
    if empresa:
        conditions.append("c.empresa = ?")
        params.append(empresa)

    where = " AND ".join(conditions)
    sql = f"SELECT c.* FROM clients c WHERE {where} ORDER BY c.tier ASC, c.nome ASC"
    return _rows(conn.execute(sql, params))


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------

def get_all_lists(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM lists ORDER BY nome ASC"))


def create_list(conn: sqlite3.Connection, nome: str, descricao: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO lists (nome, descricao) VALUES (?, ?)",
        (nome, descricao),
    )
    conn.commit()
    return cur.lastrowid


def rename_list(conn: sqlite3.Connection, list_id: int, nome: str) -> None:
    conn.execute("UPDATE lists SET nome = ? WHERE id = ?", (nome, list_id))
    conn.commit()


def delete_list(conn: sqlite3.Connection, list_id: int) -> None:
    conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))
    conn.commit()


def get_list_members(conn: sqlite3.Connection, list_id: int) -> list[dict]:
    return get_clients_by_list(conn, list_id)


def set_list_members(conn: sqlite3.Connection, list_id: int, client_ids: list[int]) -> None:
    with conn:
        conn.execute("DELETE FROM client_list WHERE list_id = ?", (list_id,))
        conn.executemany(
            "INSERT INTO client_list (client_id, list_id) VALUES (?, ?)",
            [(cid, list_id) for cid in client_ids],
        )


def get_list_member_counts(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute(
        "SELECT list_id, COUNT(*) as cnt FROM client_list GROUP BY list_id"
    ).fetchall()
    return {row["list_id"]: row["cnt"] for row in rows}


# ---------------------------------------------------------------------------
# Message log
# ---------------------------------------------------------------------------

def log_message(
    conn: sqlite3.Connection,
    client_id: int,
    mensagem: str,
    template: str,
    status: str,
    error_msg: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO message_log (client_id, mensagem, template, status, error_msg) VALUES (?, ?, ?, ?, ?)",
        (client_id, mensagem, template, status, error_msg),
    )
    conn.commit()
    return cur.lastrowid


def get_message_log(
    conn: sqlite3.Connection,
    client_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status: str | None = None,
) -> list[dict]:
    sql = """
        SELECT ml.*, c.nome as client_nome, c.empresa as client_empresa
        FROM message_log ml
        LEFT JOIN clients c ON c.id = ml.client_id
        WHERE 1=1
    """
    params: list = []

    if client_id is not None:
        sql += " AND ml.client_id = ?"
        params.append(client_id)
    if date_from:
        sql += " AND ml.sent_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND ml.sent_at <= ?"
        params.append(date_to + "T23:59:59")
    if status:
        sql += " AND ml.status = ?"
        params.append(status)

    sql += " ORDER BY ml.sent_at DESC"
    return _rows(conn.execute(sql, params))


def get_messages_this_month(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM message_log
        WHERE status = 'sent'
        AND strftime('%Y-%m', sent_at) = strftime('%Y-%m', 'now')
        """
    ).fetchone()
    return row["cnt"] if row else 0


def get_last_contact_per_client(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute(
        """
        SELECT client_id, MAX(sent_at) as last_sent
        FROM message_log
        WHERE status = 'sent'
        GROUP BY client_id
        """
    ).fetchall()
    return {row["client_id"]: row["last_sent"] for row in rows}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    else:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Client lookups for Notion sync
# ---------------------------------------------------------------------------

def get_client_by_whatsapp(conn: sqlite3.Connection, whatsapp: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM clients WHERE whatsapp = ?", (whatsapp,)))


def get_client_by_notion_page_id(conn: sqlite3.Connection, page_id: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM clients WHERE notion_page_id = ?", (page_id,)))
