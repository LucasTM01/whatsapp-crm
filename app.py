import platform
import shutil
import subprocess
import time
from pathlib import Path

import streamlit as st

# Project root — used so docker compose always finds docker-compose.yml
# regardless of the working directory Streamlit was launched from.
PROJECT_ROOT = Path(__file__).parent

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
    _auto_pull_notion()


def _auto_pull_notion():
    """Silently pull from Notion if credentials are configured."""
    from core.logger import get_logger
    log = get_logger(__name__)

    conn = get_conn()
    try:
        from db.queries import get_setting
        token = get_setting(conn, "notion_token")
        db_id = get_setting(conn, "notion_clients_db_id")
    finally:
        conn.close()

    if not token or not db_id:
        return

    try:
        from core.notion_sync import pull_from_notion
        conn = get_conn()
        try:
            stats = pull_from_notion(conn, token, db_id)
            log.info(
                "notion_auto_pull",
                created=stats["created"],
                updated=stats["updated"],
                skipped=stats["skipped"],
                errors=len(stats["errors"]),
            )
        finally:
            conn.close()
    except Exception as e:
        log.error("notion_auto_pull_failed", error=str(e))


initialize()


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def _check_docker_installed() -> bool:
    """Return True if the docker CLI binary is available in PATH."""
    return shutil.which("docker") is not None


def _check_docker_running() -> bool:
    """Return True if the Docker daemon responds to 'docker info'."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=8,
        )
        return result.returncode == 0
    except Exception:
        return False


def _start_docker() -> bool:
    """Attempt to launch Docker Desktop (Windows/macOS) or the daemon (Linux).

    Returns True if a launch command was successfully issued (daemon may still
    be starting — caller must poll _check_docker_running() to confirm).
    """
    system = platform.system()
    try:
        if system == "Windows":
            candidates = [
                Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe"),
                Path(r"C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe"),
                Path.home() / "AppData" / "Local" / "Docker" / "Docker Desktop.exe",
            ]
            for exe in candidates:
                if exe.exists():
                    subprocess.Popen(
                        [str(exe)],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    return True
            # Fallback: ask the Windows shell to find and launch Docker Desktop
            subprocess.Popen('start "" "Docker Desktop"', shell=True)
            return True
        elif system == "Darwin":  # macOS
            subprocess.Popen(["open", "-a", "Docker"])
            return True
        elif system == "Linux":
            result = subprocess.run(
                ["systemctl", "start", "docker"],
                capture_output=True,
                timeout=15,
            )
            return result.returncode == 0
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Sidebar — WAHA status + overdue badge
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## WhatsApp CRM")
    st.divider()

    # ── Docker health check ───────────────────────────────────────────────
    if not _check_docker_installed():
        st.error(
            "**Docker não encontrado.**\n\n"
            "Instale o Docker Desktop para usar o WhatsApp CRM:\n\n"
            "👉 [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)"
        )
        st.stop()

    if not _check_docker_running():
        if not st.session_state.get("_docker_start_attempted"):
            st.session_state["_docker_start_attempted"] = True
            with st.spinner("Docker não está rodando — tentando iniciar..."):
                launched = _start_docker()
                if launched:
                    # Wait up to 60 s for the daemon to become ready
                    for _ in range(60):
                        time.sleep(1)
                        if _check_docker_running():
                            break
            st.rerun()
        else:
            st.error(
                "**Docker Desktop não está rodando.**\n\n"
                "Abra o Docker Desktop manualmente, aguarde o ícone ficar estável "
                "e recarregue a página."
            )
            st.stop()
    else:
        # Docker is confirmed running — reset flag so we retry if it stops later
        st.session_state.pop("_docker_start_attempted", None)
    # ── end Docker check ──────────────────────────────────────────────────

    waha = check_waha_status()
    connected = waha.get("connected", False)
    status_label = waha.get("status", "UNKNOWN")

    if connected:
        st.success("WAHA Conectado")
        if st.button("Desligar WAHA", key="btn_stop_waha", use_container_width=True):
            subprocess.Popen(["docker", "compose", "stop", "waha"], cwd=str(PROJECT_ROOT))
            st.toast("Desligando WAHA...")
            time.sleep(2)
            st.rerun()
        if st.button("Resetar sessão", key="btn_reset_waha", use_container_width=True):
            st.session_state["_confirm_reset"] = True
        if st.session_state.get("_confirm_reset"):
            st.warning("Isso desconecta o WhatsApp e exige novo QR code.")
            col_yes, col_no = st.columns(2)
            if col_yes.button("Confirmar", key="btn_reset_yes", type="primary", use_container_width=True):
                st.session_state.pop("_confirm_reset", None)
                st.session_state.pop("_waha_autostarted", None)
                with st.spinner("Resetando sessão..."):
                    subprocess.run(["docker", "compose", "down", "-v"], capture_output=True, cwd=str(PROJECT_ROOT))
                    result = subprocess.run(
                        ["docker", "compose", "up", "-d"],
                        capture_output=True,
                        text=True,
                        cwd=str(PROJECT_ROOT),
                    )
                    if result.returncode != 0:
                        st.error("Falha ao reiniciar Docker:")
                        st.code(result.stderr or result.stdout)
                        st.stop()
                    ready_states = {"WORKING", "SCAN_QR_CODE", "STOPPED", "FAILED"}
                    for _ in range(30):
                        time.sleep(1)
                        if check_waha_status().get("status") in ready_states:
                            break
                st.rerun()
            if col_no.button("Cancelar", key="btn_reset_no", use_container_width=True):
                st.session_state.pop("_confirm_reset", None)
                st.rerun()
    else:
        # Auto-start Docker/WAHA once per session when unreachable
        if status_label == "UNREACHABLE" and not st.session_state.get("_waha_autostarted"):
            st.session_state["_waha_autostarted"] = True
            with st.spinner("Iniciando WAHA..."):
                result = subprocess.run(
                    ["docker", "compose", "up", "-d"],
                    capture_output=True,
                    text=True,
                    cwd=str(PROJECT_ROOT),
                )
                if result.returncode != 0:
                    st.error("Falha ao iniciar Docker:")
                    st.code(result.stderr or result.stdout or "Sem saída — verifique se o Docker Desktop está aberto.")
                else:
                    ready_states = {"WORKING", "SCAN_QR_CODE", "STOPPED", "FAILED"}
                    for _ in range(30):
                        time.sleep(1)
                        if check_waha_status().get("status") in ready_states:
                            break
            st.rerun()

        st.error(f"WAHA: {status_label}")
        if st.button("Ligar WAHA", key="btn_start_waha", type="primary", use_container_width=True):
            with st.spinner("Iniciando WAHA..."):
                # FAILED = container running but session crashed → restart it
                # UNREACHABLE/other = container not running → bring it up
                if status_label == "FAILED":
                    cmd = ["docker", "compose", "restart", "waha"]
                else:
                    cmd = ["docker", "compose", "up", "-d"]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=str(PROJECT_ROOT),
                )
                if result.returncode != 0:
                    st.error("Falha ao iniciar Docker:")
                    st.code(result.stderr or result.stdout or "Sem saída — verifique se o Docker Desktop está aberto.")
                    st.stop()
                # Wait up to 30s for WAHA to fully boot (WEBJS engine is slow)
                ready_states = {"WORKING", "SCAN_QR_CODE", "STOPPED", "FAILED"}
                for _ in range(30):
                    time.sleep(1)
                    if check_waha_status().get("status") in ready_states:
                        break
            st.rerun()

        if status_label == "SCAN_QR_CODE":
            qr = get_qr_code()
            if qr:
                st.markdown("**Escaneie para conectar:**")
                st.image(qr)
            else:
                st.info("Aguardando QR code...")
        elif status_label == "STARTING":
            st.info("WAHA iniciando...")

    st.divider()

    # Sidebar overdue badge — loaded here, reused in main body below
    conn = get_conn()
    try:
        overdue = get_overdue_clients(conn)
    finally:
        conn.close()
    st.session_state["_dashboard_overdue"] = overdue

    if overdue:
        st.warning(f"⚠️ **{len(overdue)}** contato(s) em atraso")
    else:
        st.success("Nenhum contato em atraso")


# ---------------------------------------------------------------------------
# Main — metrics + overdue table
# ---------------------------------------------------------------------------

st.title("Dashboard")

conn = get_conn()
try:
    all_clients = get_all_clients(conn)
    all_lists = get_all_lists(conn)
    msgs_month = get_messages_this_month(conn)
finally:
    conn.close()

# Reuse the overdue list computed in sidebar — avoids a second query
overdue = st.session_state.get("_dashboard_overdue", [])

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
            tier_map = {1: "★★★", 2: "★★", 3: "★", 4: "", 5: "", 6: ""}
            tier_str = tier_map.get(client.get("tier", 2), "★★")

            c1.markdown(f"**{client['nome']}** — {client.get('empresa') or '—'}")
            last = client.get("last_contact")
            c2.caption(f"Último contato: {last[:10] if last else 'Nunca'}")
            c3.caption(f"{tier_str} Tier {client.get('tier', 2)}")

            if c4.button("Enviar agora", key=f"send_{client['id']}"):
                st.session_state["composer_recipients"] = [client]
                st.switch_page("pages/3_Composer.py")
