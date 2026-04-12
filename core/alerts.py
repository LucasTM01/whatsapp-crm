import sqlite3

from core.logger import get_logger

_log = get_logger(__name__)


def get_overdue_clients(conn: sqlite3.Connection) -> list[dict]:
    """Return active clients whose last contact exceeds their contact frequency.

    Considers a client overdue when:
      - They have been contacted before AND days since last contact > freq_dias
      - OR they have never been contacted AND days since created_at > freq_dias

    Only counts messages with status='sent' as real contacts.
    Results ordered by most overdue first.
    """
    sql = """
        SELECT
            c.*,
            MAX(m.sent_at) AS last_contact,
            CAST(
                julianday('now') - julianday(COALESCE(MAX(m.sent_at), c.created_at))
            AS INTEGER) AS dias_sem_contato
        FROM clients c
        LEFT JOIN message_log m
            ON m.client_id = c.id AND m.status = 'sent'
        WHERE c.ativo = 1
          AND c.freq_dias IS NOT NULL
        GROUP BY c.id
        HAVING (julianday('now') - julianday(COALESCE(MAX(m.sent_at), c.created_at))) > c.freq_dias
        ORDER BY dias_sem_contato DESC
    """
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:
        _log.error("overdue_query_error", error=str(exc))
        return []
