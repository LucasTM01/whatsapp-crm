# WhatsApp CRM

## Setup
1. `uv sync`
2. `docker compose up -d`
3. `uv run streamlit run app.py`
4. On first run: open http://localhost:8501, go to sidebar, scan QR code

## Package management
Always use `uv add <package>` to add dependencies.

## Database
SQLite file at `./crm.db`. Initialize with `uv run python -c "from db.schema import init_db; init_db()"`.
Never hard-delete records — use ativo=0 for clients, keep all message logs.

## WhatsApp numbers
Always store and send as full international format without + or spaces: `5511999999999`.
Strip any non-digit characters on input. Brazilian mobile numbers (11 digits) get `55` prepended.

## WAHA API
Base URL: http://localhost:3000
Session name: "default"
API key: `whatsapp-crm-local-key` (set via WAHA_API_KEY in docker-compose.yml)
All requests must include header: `X-Api-Key: whatsapp-crm-local-key`
Docs: http://localhost:3000/dashboard (username: admin, check container logs for password)
chatId format for individuals: `{phone}@c.us` (e.g. `5511999999999@c.us`)
Auto-start session: `WHATSAPP_START_SESSION=default` env var in docker-compose.yml

## Template variables
{nome} → first name only (split on space, take first word)
{nome_completo} → full name
{empresa} → institution name
{ticker} → first ticker in the client's comma-separated tickers field

## Tier display
1 = ★★★ Tier 1 (top priority)
2 = ★★ Tier 2 (standard)
3 = ★ Tier 3 (low touch)

## Cache invalidation
After every DB write, call st.cache_data.clear() + st.rerun().
Do NOT cache: check_waha_status(), get_overdue_clients(), anything inside send loops.

## Timestamps
Stored as UTC ISO strings (SQLite datetime('now') is UTC).
Displayed in America/Sao_Paulo timezone using zoneinfo.ZoneInfo (requires tzdata on Windows).
