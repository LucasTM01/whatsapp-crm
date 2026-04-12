import pandas as pd
import streamlit as st

from core.sender import check_waha_status, send_bulk
from core.templates import VARIABLES, get_preview
from db import get_conn
from db.queries import (
    get_all_clients,
    get_all_lists,
    get_clients_by_filters,
    get_clients_by_list,
    log_message,
)

st.set_page_config(page_title="Composer", page_icon="✉️", layout="wide")
st.title("Composer")

TIER_DISPLAY = {1: "★★★ Tier 1", 2: "★★ Tier 2", 3: "★ Tier 3"}
TIPO_OPTIONS = ["buy-side", "family office", "hedge fund", "private bank", "other"]


# ---------------------------------------------------------------------------
# Cached reads
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def _load_lists():
    conn = get_conn()
    rows = get_all_lists(conn)
    conn.close()
    return rows


@st.cache_data(ttl=30)
def _load_clients():
    conn = get_conn()
    rows = get_all_clients(conn, ativo_only=True)
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------

defaults = {
    "composer_template": "",
    "composer_recipients": [],
    "composer_results": [],
    "composer_sending": False,
    "composer_dry_run": False,
}
for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val


# ---------------------------------------------------------------------------
# Layout — left (template) | right (recipients)
# ---------------------------------------------------------------------------

left, right = st.columns([1, 1], gap="large")

# ---- LEFT: Template editor ------------------------------------------------

with left:
    st.subheader("Mensagem")

    # Variable insertion buttons — MUST come before text_area to allow
    # appending to session_state.composer_template before rendering the widget.
    var_cols = st.columns(len(VARIABLES))
    for i, var in enumerate(VARIABLES):
        if var_cols[i].button(var, key=f"var_btn_{i}", help=f"Inserir {var}"):
            st.session_state.composer_template += var
            st.rerun()

    # text_area intentionally has NO key= — value= is the sole source of truth.
    # Using key= would lock the widget to its own cache and ignore value= after first render.
    template_input = st.text_area(
        "Template",
        value=st.session_state.composer_template,
        height=220,
        placeholder=(
            "Olá {nome}, tudo bem?\n\n"
            "Passando para compartilhar nossa última nota sobre {ticker}...\n\n"
            "Variáveis disponíveis: {nome}, {nome_completo}, {empresa}, {ticker}"
        ),
        label_visibility="collapsed",
    )
    st.session_state.composer_template = template_input

    # Live preview
    if st.session_state.composer_recipients and st.session_state.composer_template.strip():
        st.markdown("**Preview** (1º destinatário):")
        preview = get_preview(
            st.session_state.composer_template,
            st.session_state.composer_recipients,
        )
        st.info(preview)
    elif not st.session_state.composer_template.strip():
        st.caption("Digite uma mensagem para ver o preview.")
    else:
        st.caption("Selecione destinatários para ver o preview.")


# ---- RIGHT: Recipients ----------------------------------------------------

with right:
    st.subheader("Destinatários")

    lists = _load_lists()
    all_clients = _load_clients()
    client_fmt = lambda c: f"{c['nome']} — {c.get('empresa') or '—'}"

    tab1, tab2, tab3 = st.tabs(["Por Lista", "Individual", "Por Filtro"])

    with tab1:
        if not lists:
            st.info("Nenhuma lista criada. Vá para a página Listas.")
        else:
            list_options = {lst["nome"]: lst["id"] for lst in lists}
            sel_list_name = st.selectbox(
                "Selecionar lista",
                options=list(list_options.keys()),
                key="composer_list_sel",
            )
            sel_list_id = list_options[sel_list_name]

            conn = get_conn()
            list_clients = get_clients_by_list(conn, sel_list_id)
            conn.close()

            st.caption(f"{len(list_clients)} clientes nesta lista")

            if st.button("Usar esta lista", key="use_list_btn", type="primary"):
                st.session_state.composer_recipients = list_clients
                st.rerun()

    with tab2:
        sel_individual = st.multiselect(
            "Clientes",
            options=all_clients,
            format_func=client_fmt,
            key="composer_individual_sel",
        )
        if st.button("Adicionar selecionados", key="add_individual_btn"):
            existing_ids = {c["id"] for c in st.session_state.composer_recipients}
            new_ones = [c for c in sel_individual if c["id"] not in existing_ids]
            st.session_state.composer_recipients.extend(new_ones)
            st.rerun()

    with tab3:
        f_tier = st.selectbox(
            "Tier",
            options=[None, 1, 2, 3],
            format_func=lambda x: "Todos" if x is None else TIER_DISPLAY[x],
            key="composer_f_tier",
        )
        f_tipo = st.selectbox(
            "Tipo",
            options=[""] + TIPO_OPTIONS,
            format_func=lambda x: "Todos" if x == "" else x,
            key="composer_f_tipo",
        )
        f_ticker = st.text_input("Ticker", placeholder="ex: WEGE3", key="composer_f_ticker")

        if st.button("Aplicar filtro", key="apply_filter_btn"):
            conn = get_conn()
            filtered = get_clients_by_filters(
                conn,
                tipo=f_tipo or None,
                tier=f_tier,
                ticker=f_ticker.strip() or None,
            )
            conn.close()
            st.session_state.composer_recipients = filtered
            st.rerun()

    st.divider()

    # Current recipients summary
    recipients = st.session_state.composer_recipients
    n = len(recipients)
    st.metric("Total de destinatários", n)

    if recipients:
        with st.expander(f"Ver lista ({n})"):
            for c in recipients:
                tier_str = TIER_DISPLAY.get(c.get("tier", 2), "★★")
                st.caption(f"{c['nome']} — {c.get('empresa') or '—'} — {tier_str}")

        if st.button("Limpar destinatários", key="clear_recipients"):
            st.session_state.composer_recipients = []
            st.rerun()


# ---------------------------------------------------------------------------
# Send controls
# ---------------------------------------------------------------------------

st.divider()

waha_status = check_waha_status()
waha_ok = waha_status.get("connected", False)

ctrl1, ctrl2, ctrl3 = st.columns([1, 1, 2])

dry_run = ctrl1.toggle(
    "Dry Run",
    value=st.session_state.composer_dry_run,
    help="Simula o envio sem realmente enviar mensagens",
    key="dry_run_toggle",
)
st.session_state.composer_dry_run = dry_run

with ctrl2:
    if not waha_ok:
        st.error("WAHA desconectado")
    else:
        st.success("WAHA conectado")

send_disabled = (
    st.session_state.composer_sending
    or len(st.session_state.composer_recipients) == 0
    or not st.session_state.composer_template.strip()
    or (not waha_ok and not dry_run)
)

send_label = "Enviar mensagens" if not dry_run else "Simular envio"

with ctrl3:
    send_clicked = st.button(
        send_label,
        type="primary",
        disabled=send_disabled,
        key="send_button",
        use_container_width=True,
    )

if send_clicked:
    st.session_state.composer_sending = True
    st.session_state.composer_results = []
    recipients = st.session_state.composer_recipients
    template = st.session_state.composer_template

    progress_bar = st.progress(0, text="Iniciando...")
    status_placeholder = st.empty()

    conn = get_conn()
    try:
        for result in send_bulk(recipients, template, dry_run=dry_run):
            st.session_state.composer_results.append(result)
            progress = (result["index"] + 1) / result["total"]
            progress_bar.progress(progress, text=f"Enviando para {result['client']['nome']}...")
            status_placeholder.caption(
                f"{result['index'] + 1}/{result['total']} — {result['client']['nome']} — {result['status']}"
            )

            # Log to DB (skip for dry_run)
            if not dry_run:
                log_message(
                    conn,
                    client_id=result["client"]["id"],
                    mensagem=result["message"],
                    template=template,
                    status=result["status"],
                    error_msg=result.get("error"),
                )
    finally:
        conn.close()

    st.session_state.composer_sending = False
    progress_bar.progress(1.0, text="Concluído.")
    st.cache_data.clear()
    st.rerun()


# ---------------------------------------------------------------------------
# Results table (persists after send)
# ---------------------------------------------------------------------------

if st.session_state.composer_results:
    results = st.session_state.composer_results
    ok = sum(1 for r in results if r["status"] == "ok")
    err = sum(1 for r in results if r["status"] == "error")
    dry = sum(1 for r in results if r["status"] == "dry_run")

    st.subheader("Resultado do envio")
    m1, m2, m3 = st.columns(3)
    if dry:
        m1.metric("Simulados", dry)
    else:
        m1.metric("Enviados", ok)
        m2.metric("Falhas", err)

    rows = []
    for r in results:
        rows.append({
            "Nome": r["client"]["nome"],
            "Número": r["client"]["whatsapp"],
            "Status": r["status"],
            "Mensagem enviada": r["message"][:80] + ("…" if len(r["message"]) > 80 else ""),
            "Erro": r.get("error", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
