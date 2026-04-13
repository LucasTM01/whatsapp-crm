from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from db import get_conn
from db.queries import get_all_clients, get_message_log

st.set_page_config(page_title="Histórico", page_icon="📜", layout="wide")
st.title("Histórico de Mensagens")

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def to_local(utc_str: str) -> str:
    """Convert UTC ISO string to America/Sao_Paulo display string."""
    try:
        dt = datetime.fromisoformat(utc_str).replace(tzinfo=timezone.utc)
        local = dt.astimezone(SAO_PAULO)
        return local.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return utc_str


# ---------------------------------------------------------------------------
# Cached reads
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def _load_clients():
    conn = get_conn()
    rows = get_all_clients(conn, ativo_only=False)
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

with st.expander("Filtros", expanded=True):
    f1, f2, f3, f4 = st.columns([2, 2, 2, 1])

    all_clients = _load_clients()
    client_name_map = {"": "Todos os clientes"}
    client_name_map.update({c["nome"]: c["id"] for c in all_clients})

    f_client_name = f1.selectbox(
        "Cliente",
        options=list(client_name_map.keys()),
        format_func=lambda x: "Todos os clientes" if x == "" else x,
        key="hist_client",
    )
    f_client_id = client_name_map.get(f_client_name) if f_client_name else None
    if isinstance(f_client_id, str):  # "Todos os clientes" placeholder
        f_client_id = None

    date_range = f2.date_input(
        "Período",
        value=[],
        key="hist_dates",
    )
    f_date_from = str(date_range[0]) if len(date_range) >= 1 else None
    f_date_to = str(date_range[1]) if len(date_range) == 2 else f_date_from

    f_status = f3.selectbox(
        "Status",
        options=["", "sent", "failed", "pending", "dry_run"],
        format_func=lambda x: "Todos" if x == "" else x,
        key="hist_status",
    )

    if f4.button("Limpar filtros", key="clear_hist_filters"):
        for k in ["hist_client", "hist_dates", "hist_status"]:
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()


# ---------------------------------------------------------------------------
# Load log
# ---------------------------------------------------------------------------

conn = get_conn()
log_rows = get_message_log(
    conn,
    client_id=f_client_id,
    date_from=f_date_from,
    date_to=f_date_to,
    status=f_status or None,
)
conn.close()

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

total = len(log_rows)
sent = sum(1 for r in log_rows if r["status"] == "sent")
failed = sum(1 for r in log_rows if r["status"] == "failed")

m1, m2, m3 = st.columns(3)
m1.metric("Total de registros", total)
m2.metric("Enviados", sent)
m3.metric("Falhas", failed)

st.divider()

# ---------------------------------------------------------------------------
# Main table
# ---------------------------------------------------------------------------

if not log_rows:
    st.info("Nenhum registro encontrado com os filtros selecionados.")
else:
    display_rows = []
    for r in log_rows:
        display_rows.append({
            "Data/Hora": to_local(r["sent_at"]) if r.get("sent_at") else "—",
            "Cliente": r.get("client_nome") or "—",
            "Empresa": r.get("client_empresa") or "—",
            "Status": r["status"],
            "Mensagem": r["mensagem"][:120] + ("…" if len(r["mensagem"]) > 120 else ""),
            "Erro": r.get("error_msg") or "",
            "_id": r["id"],
            "_client_id": r.get("client_id"),
        })

    df = pd.DataFrame(display_rows)
    visible_cols = ["Data/Hora", "Cliente", "Empresa", "Status", "Mensagem", "Erro"]

    st.dataframe(
        df[visible_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Status": st.column_config.TextColumn("Status"),
            "Mensagem": st.column_config.TextColumn("Mensagem"),
        },
    )

    # CSV export — utf-8-sig for Excel compatibility with Portuguese accents on Windows
    csv_bytes = df[visible_cols].to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="Exportar CSV",
        data=csv_bytes,
        file_name="historico_mensagens.csv",
        mime="text/csv",
        key="export_csv",
    )

    # ---------------------------------------------------------------------------
    # Per-client timeline
    # ---------------------------------------------------------------------------

    if f_client_id:
        st.divider()
        client_logs = [r for r in log_rows if r.get("client_id") == f_client_id]
        if client_logs:
            client_name = client_logs[0].get("client_nome", "Cliente")
            st.subheader(f"Timeline — {client_name}")

            for entry in sorted(client_logs, key=lambda x: x.get("sent_at", ""), reverse=False):
                sent_at = to_local(entry["sent_at"]) if entry.get("sent_at") else "—"
                status = entry["status"]
                icon = {"sent": "✅", "failed": "❌", "pending": "⏳"}.get(status, "📨")

                with st.container():
                    h1, h2 = st.columns([1, 4])
                    h1.caption(f"{icon} {sent_at}")
                    h2.markdown(entry["mensagem"])
                    if entry.get("error_msg"):
                        h2.error(entry["error_msg"])
                st.divider()
