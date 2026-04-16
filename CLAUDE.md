# WhatsApp CRM

## Setup
1. `uv sync`
2. `docker compose up -d`
3. `uv run streamlit run app.py`
4. On first run: open http://localhost:8501, go to sidebar, scan QR code

## Package management
Always use `uv add <package>` to add dependencies.

## Project structure
```
app.py              # Dashboard + sidebar (WAHA status, Docker health, overdue alerts)
pages/
  1_Clientes.py     # Client table (data_editor), filters, add form, Excel import
  2_Listas.py       # Mailing lists management
  3_Composer.py     # Message composer + bulk send
  4_Histórico.py    # Message history log
core/
  sender.py         # WAHA API client, send_message(), send_bulk(), check_waha_status()
  alerts.py         # get_overdue_clients() — SQL query, no side effects
  templates.py      # render() — plain str.replace(), NO string.Template (breaks on {+5%})
  logger.py         # structlog setup
db/
  schema.py         # init_db(), SCHEMA_SQL — tables: clients, lists, client_list, message_log
  queries.py        # All SQL helpers
  __init__.py       # get_conn() — returns sqlite3.Connection with row_factory=Row
```

## Database
SQLite file at `./crm.db`. Initialize with `uv run python -c "from db.schema import init_db; init_db()"`.
Never hard-delete records — use `ativo=0` for clients, keep all message logs.

### Schema overview
- `clients`: id, nome, whatsapp (UNIQUE), email, empresa, tickers, tipo (cargo), tier (1-6), freq_dias, notas, ativo, created_at, updated_at
- `lists`: id, nome (UNIQUE), descricao, created_at
- `client_list`: client_id, list_id (M2M)
- `message_log`: id, client_id, mensagem, template, status ('sent'/'error'), error_msg, sent_at

## WhatsApp numbers
Always store and send as full international format without + or spaces: `5511999999999`.
Strip non-digit characters on input. Brazilian mobile numbers (10 or 11 digits) get `55` prepended.
`normalize_phone()` in `core/sender.py` handles this.

## WAHA API
Base URL: `http://localhost:3000`
Session name: `default`
API key: `whatsapp-crm-local-key` (set via WAHA_API_KEY in docker-compose.yml)
All requests must include header: `X-Api-Key: whatsapp-crm-local-key`
Docs: http://localhost:3000/dashboard
chatId format: `{phone}@c.us` (e.g. `5511999999999@c.us`)
Auto-start session: `WHATSAPP_START_SESSION=default` env var in docker-compose.yml

### WAHA status values
- `WORKING` → connected
- `SCAN_QR_CODE` → needs QR scan
- `STARTING` → still booting (404 or RemoteProtocolError from httpx)
- `UNREACHABLE` → container not running (ConnectError)
- `FAILED` → container running, session crashed → use `docker compose restart waha`

## Template variables
`{nome}` → first name only (split on space, take first)
`{nome_completo}` → full name
`{empresa}` → institution name
`{ticker}` → first ticker in the comma-separated tickers field

Template rendering uses plain `str.replace()` — never switch to `string.Template` or `.format()`.
Financial text often contains braces (e.g. `{+5.2%}`) which would break format-based approaches.

## Tier system
Tiers 1–6 are supported. Display in UI:
- 1 = ★★★ Tier 1
- 2 = ★★ Tier 2
- 3 = ★ Tier 3
- 4-6 = Tier 4 / 5 / 6 (no stars)

Default tier = 2, default freq_dias = 30.

## Overdue logic
A client is overdue when days since `COALESCE(last sent message, created_at) > freq_dias`.
Only messages with `status='sent'` count as real contacts.
Query lives in `core/alerts.py:get_overdue_clients()`.

## Bulk send
`core/sender.send_bulk()` is a generator — yields progress dicts per recipient.
Sleeps `random.uniform(3, 8)` seconds between sends (WhatsApp spam prevention).
Supports `dry_run=True` for preview without sending.

## Cache invalidation
After every DB write, call `st.cache_data.clear()` + `st.rerun()`.
Do NOT cache: `check_waha_status()`, `get_overdue_clients()`, anything inside send loops.
Cached reads use `@st.cache_data(ttl=30)`.

## Timestamps
Stored as UTC ISO strings (`datetime('now')` in SQLite is UTC).
Displayed in `America/Sao_Paulo` timezone using `zoneinfo.ZoneInfo` (requires `tzdata` on Windows).

## Excel import
Accepted in `1_Clientes.py`. Column headers are normalised (lowercase, spaces → underscore).
Required columns: `nome`, `whatsapp`. All others optional.
`tickers` values are uppercased on import.
