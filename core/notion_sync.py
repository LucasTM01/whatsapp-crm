"""Notion <-> SQLite sync logic for clients and meetings databases."""

import sqlite3
import time

from notion_client import Client
from notion_client.errors import APIResponseError

from core.logger import get_logger
from db.queries import (
    create_client,
    get_all_clients,
    get_client_by_notion_page_id,
    get_client_by_whatsapp,
    get_setting,
    reset_clients_notion_page_ids,
    set_setting,
    update_client,
)

_log = get_logger(__name__)

MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# Property mapping: SQLite column -> (Notion property name, Notion type)
# ---------------------------------------------------------------------------

PROPERTY_MAP = {
    "nome":      ("Name",       "title"),
    "whatsapp":  ("WhatsApp",   "rich_text"),
    "email":     ("Email",      "email"),
    "empresa":   ("Empresa",    "rich_text"),
    "tickers":   ("Tickers",    "rich_text"),
    "tipo":      ("Cargo",      "rich_text"),
    "tier":      ("Tier",       "number"),
    "freq_dias": ("Frequência", "number"),
    "notas":     ("Notas",      "rich_text"),
}

# Notion property schema definitions for Clientes database
# "Name" is the default title property created by databases.create — we keep it.
_PROPERTY_SCHEMAS = {
    "Name":       {"title": {}},
    "WhatsApp":   {"rich_text": {}},
    "Email":      {"email": {}},
    "Empresa":    {"rich_text": {}},
    "Tickers":    {"rich_text": {}},
    "Cargo":      {"rich_text": {}},
    "Tier":       {"number": {}},
    "Frequência": {"number": {}},
    "Notas":      {"rich_text": {}},
}

# Notion property schema for Reuniões database (for reference only).
# "Contatos" is a RELATION to the Clientes DB — the actual database_id is injected
# dynamically in initialize_notion_databases since it depends on clients_db_id.
# "Título" is the title/index field.
_MEETINGS_NON_RELATION_SCHEMAS = {
    "Data":           {"date": {}},          # Atr2
    "Empresas":       {"select": {}},        # Atr3 — select (single choice)
    "Tipo":           {"select": {}},        # Atr4 — select (single choice)
    "Tags":           {"multi_select": {}},  # Atr5 — multi-select
    "Bloco de notas": {"rich_text": {}},     # Atr6 — free-text block
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _retry_on_429(fn, *args, **kwargs):
    """Execute fn with exponential backoff on 429 rate-limit errors."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except APIResponseError as e:
            if e.status == 429 and attempt < MAX_RETRIES:
                wait = 2 ** (attempt + 1)
                _log.warning("rate_limited", retry_in=wait)
                time.sleep(wait)
            else:
                raise


def _get_client(token: str) -> Client:
    return Client(auth=token)


def _extract_datasource_id(db_obj: dict) -> str | None:
    """Extract the primary data_source_id from a databases.create/retrieve response.

    In Notion API v3, databases embed a 'data_sources' array where each element
    has its own 'id'. This id is what must be passed to all data_sources.*
    endpoints — it is DIFFERENT from the database's own 'id'.
    """
    sources = db_obj.get("data_sources", [])
    return sources[0]["id"] if sources else None


def _get_datasource_id(client: Client, database_id: str) -> str:
    """Retrieve a database by its ID and return its primary data_source_id."""
    db = _retry_on_429(client.databases.retrieve, database_id=database_id)
    ds_id = _extract_datasource_id(db)
    if not ds_id:
        raise ValueError(f"No data_sources found for database {database_id}")
    return ds_id


def _update_db_properties(
    client: Client,
    database_id: str,
    properties: dict,
    db_obj: dict | None = None,
) -> None:
    """Add/update properties on a database via data_sources.update.

    In notion-client >= 3.0, databases.update no longer accepts 'properties'.
    Schema changes must go through data_sources.update with the data_source_id
    (which is DIFFERENT from the database_id).

    Pass db_obj to reuse an already-retrieved database response and avoid an
    extra API call.
    """
    if db_obj is None:
        db_obj = _retry_on_429(client.databases.retrieve, database_id=database_id)
    ds_id = _extract_datasource_id(db_obj)
    if not ds_id:
        raise ValueError(f"No data_sources found for database {database_id}")
    _retry_on_429(
        client.data_sources.update,
        data_source_id=ds_id,
        properties=properties,
    )


def _build_notion_properties(client_row: dict) -> dict:
    """Convert a SQLite client dict into Notion page properties."""
    props = {}
    for col, (notion_name, prop_type) in PROPERTY_MAP.items():
        val = client_row.get(col)
        if val is None:
            continue
        if prop_type == "title":
            props[notion_name] = {"title": [{"text": {"content": str(val)}}]}
        elif prop_type == "rich_text":
            props[notion_name] = {"rich_text": [{"text": {"content": str(val)}}]}
        elif prop_type == "email":
            props[notion_name] = {"email": str(val)}
        elif prop_type == "number":
            try:
                props[notion_name] = {"number": int(val)}
            except (ValueError, TypeError):
                pass
    return props


def _parse_notion_properties(properties: dict) -> dict:
    """Extract SQLite column values from a Notion page's properties dict."""
    data = {}
    for col, (notion_name, prop_type) in PROPERTY_MAP.items():
        prop = properties.get(notion_name)
        if not prop:
            continue
        if prop_type == "title":
            items = prop.get("title", [])
            data[col] = items[0]["plain_text"] if items else None
        elif prop_type == "rich_text":
            items = prop.get("rich_text", [])
            data[col] = items[0]["plain_text"] if items else None
        elif prop_type == "email":
            data[col] = prop.get("email")
        elif prop_type == "number":
            data[col] = prop.get("number")
    return data


# ---------------------------------------------------------------------------
# Credential validation
# ---------------------------------------------------------------------------

def validate_notion_credentials(token: str) -> bool:
    """Test if a Notion token is valid by calling users.me()."""
    try:
        client = _get_client(token)
        _retry_on_429(client.users.me)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Database initialization
# ---------------------------------------------------------------------------

def ensure_database_schema(token: str, db_id: str) -> tuple[bool, list[str]]:
    """Check that the Notion database has the expected properties.

    Returns (ok, list_of_missing_properties).
    """
    client = _get_client(token)
    db = _retry_on_429(client.databases.retrieve, database_id=db_id)
    existing_props = set(db.get("properties", {}).keys())
    required = {notion_name for _, (notion_name, _) in PROPERTY_MAP.items()}
    missing = required - existing_props
    return (len(missing) == 0, sorted(missing))


def initialize_notion_databases(
    conn: sqlite3.Connection,
    token: str,
    parent_page_id: str,
    clients_db_id: str | None = None,
    meetings_db_id: str | None = None,
) -> dict:
    """Check/create Clients and Meetings databases in Notion.

    In notion-client >= 3.0:
    - databases.create() no longer accepts 'properties' (silently dropped).
    - Schema must be applied via data_sources.update(data_source_id=...) where
      data_source_id comes from db_obj["data_sources"][0]["id"], NOT the database id.

    Stale/deleted DB IDs are caught and replaced with freshly created databases.

    Returns {"clients_db_id": str, "meetings_db_id": str, "warnings": list[str]}.
    """
    client = _get_client(token)
    warnings: list[str] = []
    clients_datasource_id: str | None = None  # needed for the Contatos relation

    # --- Clients database ---
    if clients_db_id:
        try:
            db = _retry_on_429(client.databases.retrieve, database_id=clients_db_id)
            clients_datasource_id = _extract_datasource_id(db)
            existing = set(db.get("properties", {}).keys())
            missing_props = {k: v for k, v in _PROPERTY_SCHEMAS.items() if k not in existing}
            if missing_props:
                _update_db_properties(client, clients_db_id, missing_props, db_obj=db)
                _log.info("notion_clients_db_updated", added=list(missing_props.keys()))
            else:
                _log.info("notion_clients_db_validated", db_id=clients_db_id)
        except APIResponseError:
            _log.warning("notion_clients_db_stale", db_id=clients_db_id)
            warnings.append(
                f"Database de Clientes antigo não encontrado ({clients_db_id}) — "
                "um novo foi criado automaticamente."
            )
            clients_db_id = None  # fall through to creation

    if not clients_db_id:
        # Step 1: create bare database (SDK v3 drops 'properties' kwarg silently)
        new_db = _retry_on_429(
            client.databases.create,
            parent={"type": "page_id", "page_id": parent_page_id},
            title=[{"type": "text", "text": {"content": "Clientes"}}],
        )
        clients_db_id = new_db["id"]
        clients_datasource_id = _extract_datasource_id(new_db)
        # Save ID immediately — so it's never lost even if schema update fails below
        set_setting(conn, "notion_clients_db_id", clients_db_id)
        # New DB → old notion_page_id values in SQLite are now invalid; clear them
        # so push_to_notion picks up all clients for re-upload.
        reset_count = reset_clients_notion_page_ids(conn)
        if reset_count:
            warnings.append(
                f"Estado de sincronização resetado: {reset_count} cliente(s) "
                "marcado(s) para re-envio ao Notion."
            )
        _log.info("notion_clients_sync_reset", count=reset_count)
        # Step 2: apply full schema via data_sources.update
        non_title = {k: v for k, v in _PROPERTY_SCHEMAS.items() if "title" not in v}
        _update_db_properties(client, clients_db_id, non_title, db_obj=new_db)
        _log.info("notion_clients_db_created", db_id=clients_db_id)
    else:
        set_setting(conn, "notion_clients_db_id", clients_db_id)

    # Ensure we always have the data_source_id for the relation (fallback retrieve)
    if not clients_datasource_id:
        clients_datasource_id = _get_datasource_id(client, clients_db_id)

    # In Notion API v3, relation properties require data_source_id (NOT database_id)
    # AND must specify either single_property (one-way) or dual_property (two-way).
    # single_property: Reuniões → Clientes only (no reverse column on Clientes).
    _clients_relation = {
        "relation": {
            "data_source_id": clients_datasource_id,
            "single_property": {},
        }
    }

    # --- Meetings database ---
    if meetings_db_id:
        try:
            db = _retry_on_429(client.databases.retrieve, database_id=meetings_db_id)
            existing_props = db.get("properties", {})
            existing = set(existing_props.keys())

            # Pass 1 — Rename the current title property to "Título" if needed.
            # The title could be "Name" (default), "Contatos" (old fix), or already "Título".
            # We must rename BEFORE adding the new "Contatos" relation because JSON keys
            # are unique — we can't rename "Contatos" and add a new "Contatos" in one call.
            current_title = next(
                (name for name, prop in existing_props.items() if prop.get("type") == "title"),
                None,
            )
            if current_title and current_title != "Título":
                _update_db_properties(
                    client, meetings_db_id,
                    {current_title: {"name": "Título", "title": {}}},
                    db_obj=db,
                )
                existing.discard(current_title)
                existing.add("Título")
                _log.info("notion_meetings_title_renamed", old=current_title, new="Título")

            # Pass 2 — Add missing or wrong-type columns.
            # "Contatos" must be a relation; "Empresas" must be select (not rich_text).
            # Notion does not allow changing a property type in-place — to fix a wrong
            # type we must delete the old property (set it to null) and re-add it.
            add_props: dict = {}
            delete_props: dict = {}

            contatos_prop = existing_props.get("Contatos", {})
            if contatos_prop.get("type") != "relation":
                add_props["Contatos"] = _clients_relation

            for col, schema in _MEETINGS_NON_RELATION_SCHEMAS.items():
                existing_prop = existing_props.get(col)
                if existing_prop is None:
                    # Missing — add it
                    add_props[col] = schema
                else:
                    expected_type = next(iter(schema))  # e.g. "select", "multi_select"
                    if existing_prop.get("type") != expected_type:
                        # Wrong type — delete old, then re-add with correct type.
                        # Deletion and addition must be separate API calls.
                        delete_props[col] = None
                        add_props[col] = schema

            if delete_props:
                _update_db_properties(client, meetings_db_id, delete_props, db_obj=db)
                _log.info("notion_meetings_props_deleted", deleted=list(delete_props.keys()))
            if add_props:
                _update_db_properties(client, meetings_db_id, add_props, db_obj=db)
                _log.info("notion_meetings_db_updated", added=list(add_props.keys()))
            if not delete_props and not add_props:
                _log.info("notion_meetings_db_validated", db_id=meetings_db_id)

        except APIResponseError:
            _log.warning("notion_meetings_db_stale", db_id=meetings_db_id)
            warnings.append(
                f"Database de Reuniões antigo não encontrado ({meetings_db_id}) — "
                "um novo foi criado automaticamente."
            )
            meetings_db_id = None  # fall through to creation

    if not meetings_db_id:
        # Step 1: create bare database (gets default "Name" title)
        new_db = _retry_on_429(
            client.databases.create,
            parent={"type": "page_id", "page_id": parent_page_id},
            title=[{"type": "text", "text": {"content": "Reuniões"}}],
        )
        meetings_db_id = new_db["id"]
        # Save ID immediately — so it's never lost even if schema update fails below
        set_setting(conn, "notion_meetings_db_id", meetings_db_id)
        # Step 2: rename "Name" → "Título" AND add Contatos relation + other columns.
        # "Name" ≠ "Contatos" so this can be done in a single call (no duplicate keys).
        meetings_update = {
            "Name":           {"name": "Título", "title": {}},  # rename default title
            "Contatos":       _clients_relation,                 # relation to Clientes
            "Data":           {"date": {}},
            "Empresas":       {"select": {}},
            "Tipo":           {"select": {}},
            "Tags":           {"multi_select": {}},
            "Bloco de notas": {"rich_text": {}},
        }
        _update_db_properties(client, meetings_db_id, meetings_update, db_obj=new_db)
        _log.info("notion_meetings_db_created", db_id=meetings_db_id)
    else:
        set_setting(conn, "notion_meetings_db_id", meetings_db_id)

    return {
        "clients_db_id": clients_db_id,
        "meetings_db_id": meetings_db_id,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Pull: Notion -> SQLite
# ---------------------------------------------------------------------------

def pull_from_notion(
    conn: sqlite3.Connection,
    token: str,
    db_id: str,
) -> dict:
    """Pull all pages from a Notion clients database into SQLite.

    Matching: notion_page_id first, then whatsapp fallback.
    Conflict: Notion always overwrites SQLite.
    """
    client = _get_client(token)
    stats = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

    # In notion-client >= 3.0, databases.query was removed.
    # Queries now go through data_sources.query(data_source_id=...).
    # The data_source_id is DIFFERENT from the database_id — extract it first.
    try:
        datasource_id = _get_datasource_id(client, db_id)
    except (APIResponseError, ValueError) as e:
        stats["errors"].append(
            f"Não foi possível acessar o database de Clientes: {e}. "
            "Verifique se o ID está correto e se a integração tem acesso."
        )
        return stats

    start_cursor = None

    while True:
        kwargs: dict = {"data_source_id": datasource_id, "page_size": 100}
        if start_cursor:
            kwargs["start_cursor"] = start_cursor

        response = _retry_on_429(client.data_sources.query, **kwargs)

        for page in response["results"]:
            page_id = page["id"]
            try:
                data = _parse_notion_properties(page["properties"])

                if not data.get("nome"):
                    stats["skipped"] += 1
                    continue

                # Match existing client
                existing = get_client_by_notion_page_id(conn, page_id)
                if not existing and data.get("whatsapp"):
                    existing = get_client_by_whatsapp(conn, data["whatsapp"])

                if existing:
                    data["notion_page_id"] = page_id
                    update_client(conn, existing["id"], data)
                    stats["updated"] += 1
                else:
                    if not data.get("whatsapp"):
                        stats["skipped"] += 1
                        _log.warning("notion_pull_skip", reason="no_whatsapp", page_id=page_id)
                        continue
                    data["notion_page_id"] = page_id
                    create_client(conn, data)
                    stats["created"] += 1
            except Exception as e:
                error_msg = f"Page {page_id}: {e}"
                stats["errors"].append(error_msg)
                _log.error("notion_pull_error", page_id=page_id, error=str(e))

        if not response.get("has_more"):
            break
        start_cursor = response.get("next_cursor")

    _log.info(
        "notion_pull_complete",
        created=stats["created"],
        updated=stats["updated"],
        skipped=stats["skipped"],
        errors=len(stats["errors"]),
    )
    return stats


# ---------------------------------------------------------------------------
# Push: SQLite -> Notion
# ---------------------------------------------------------------------------

def push_to_notion(
    conn: sqlite3.Connection,
    token: str,
    db_id: str,
) -> dict:
    """Push SQLite clients without notion_page_id to Notion.

    Additive only — never overwrites existing Notion records.
    Uses pages.create(parent={"database_id": db_id}) which still accepts the
    standard database_id (not data_source_id) in notion-client v3.

    Returns stats dict with created, skipped, errors, total keys.
    """
    client = _get_client(token)
    all_clients = get_all_clients(conn, ativo_only=False)
    already_synced = [c for c in all_clients if c.get("notion_page_id")]
    to_push = [c for c in all_clients if not c.get("notion_page_id")]

    stats = {
        "created": 0,
        "skipped": len(already_synced),
        "total": len(all_clients),
        "errors": [],
    }

    _log.info(
        "notion_push_start",
        total=stats["total"],
        to_push=len(to_push),
        already_synced=stats["skipped"],
    )

    for row in to_push:
        try:
            props = _build_notion_properties(row)
            if not props:
                error_msg = f"Client {row['id']} ({row.get('nome', '?')}): no properties to push"
                stats["errors"].append(error_msg)
                _log.warning("notion_push_skip_empty", client_id=row["id"])
                continue
            page = _retry_on_429(
                client.pages.create,
                parent={"database_id": db_id},
                properties=props,
            )
            update_client(conn, row["id"], {"notion_page_id": page["id"]})
            stats["created"] += 1
            _log.info("notion_push_ok", client_id=row["id"], page_id=page["id"])
        except Exception as e:
            error_msg = f"Client {row['id']} ({row.get('nome', '?')}): {e}"
            stats["errors"].append(error_msg)
            _log.error("notion_push_error", client_id=row["id"], error=str(e))

    _log.info("notion_push_complete", created=stats["created"], errors=len(stats["errors"]))
    return stats
