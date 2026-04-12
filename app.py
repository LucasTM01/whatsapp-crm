import base64
import subprocess
import time

import streamlit as st

from core.alerts import get_overdue_clients
from core.logger import setup_logging
from core.sender import check_waha_status, get_qr_code
from db import get_conn
from db.queries import get_all_clients, get_all_lists, get_messages_this_month
from db.schema import init_db

st.set_page_config(
    page_title="WhatsApp CRM",
    page_icon="💬",
    layout="wide",
)


@st.cache_resource
def initialize():
    setup_logging()
    init_db()


initialize()


# ---------------------------------------------------------------------------
# Sidebar — WAHA status + overdue badge
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## WhatsApp CRM")
    st.divider()

    waha = check_waha_status()
    connected = waha.get("connected", False)
    status_label = waha.get("status", "UNKNOWN")

    if connected:
        st.success("WAHA Conectado")
        if st.button("Desligar WAHA", key="btn_stop_waha", use_container_width=True):
            subprocess.Popen(["docker", "compose", "stop", "waha"])
            st.toast("Desligando WAHA...")
            time.sleep(2)
            st.rerun()
    else:
        st.error(f"WAHA: {status_label}")
        if st.button("Ligar WAHA", key="btn_start_waha", type="primary", use_container_width=True):
            with st.spinner("Iniciando WAHA..."):
                subprocess.run(["docker", "compose", "up", "-d"], capture_output=True)
                # Wait up to 15s for WAHA to become reachable
                for _ in range(15):
                    time.sleep(1)
                    if check_waha_status().get("status") != "UNREACHABLE":
                        break
            st.rerun()

        if status_label not in ("UNREACHABLE", "UNKNOWN"):
            qr = get_qr_code()
            if qr:
                st.markdown("**Escaneie para conectar:**")
                st.image(base64.b64decode(qr), use_container_width=True)

    st.divider()

    conn = get_conn()
    overdue = get_overdue_clients(conn)
    conn.close()

    if overdue:
        st.warning(f"⚠️ **{len(overdue)}** contato(s) em atraso")
    else:
        st.success("Nenhum contato em atraso")


# ---------------------------------------------------------------------------
# Main — metrics + overdue table
# ---------------------------------------------------------------------------

st.title("Dashboard")

conn = get_conn()
all_clients = get_all_clients(conn)
all_lists = get_all_lists(conn)
msgs_month = get_messages_this_month(conn)
overdue = get_overdue_clients(conn)
conn.close()

col1, col2, col3 = st.columns(3)
col1.metric("Clientes Ativos", len(all_clients))
col2.metric("Mensagens este mês", msgs_month)
col3.metric("Listas", len(all_lists))

st.divider()

if not overdue:
    st.info("Todos os contatos estão em dia.")
else:
    st.subheader(f"Contatos em Atraso ({len(overdue)})")
    st.caption("Clientes que não recebem mensagem há mais dias do que a frequência configurada.")

    for client in overdue:
        with st.container():
            c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
            tier_map = {1: "★★★", 2: "★★", 3: "★"}
            tier_str = tier_map.get(client.get("tier", 2), "★★")

            c1.markdown(f"**{client['nome']}** — {client.get('empresa') or '—'}")
            last = client.get("last_contact")
            c2.caption(f"Último contato: {last[:10] if last else 'Nunca'}")
            c3.caption(f"{tier_str} Tier {client.get('tier', 2)}")

            if c4.button("Enviar agora", key=f"send_{client['id']}"):
                st.session_state["composer_recipients"] = [client]
                st.switch_page("pages/3_Composer.py")
