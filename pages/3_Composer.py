import pandas as pd
import streamlit as st

from core.sender import check_waha_status, normalize_phone, send_bulk
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

TIER_DISPLAY = {
    1: "★★★ Tier 1", 2: "★★ Tier 2", 3: "★ Tier 3",
    4: "Tier 4", 5: "Tier 5", 6: "Tier 6",
}


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
    st.caption("Inserir variável:")
    var_cols = st.columns(len(VARIABLES))
    for i, var in enumerate(VARIABLES):
        if var_cols[i].button(var, key=f"var_btn_{i}", help=f"Clique para inserir {var} na mensagem", use_container_width=True):
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

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Por Lista", "Individual", "Por Filtro", "Exclusão", "Via Excel"])

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
            try:
                list_clients = get_clients_by_list(conn, sel_list_id)
            finally:
                conn.close()

            excluded_from_list = st.multiselect(
                "Excluir da lista (exceções)",
                options=list_clients,
                format_func=client_fmt,
                key="composer_list_exclude",
            )
            excluded_ids = {c["id"] for c in excluded_from_list}
            final_list_clients = [c for c in list_clients if c["id"] not in excluded_ids]
            st.caption(f"{len(final_list_clients)} de {len(list_clients)} clientes selecionados")

            if st.button("Usar esta lista", key="use_list_btn", type="primary"):
                st.session_state.composer_recipients = final_list_clients
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
            options=[None, 1, 2, 3, 4, 5, 6],
            format_func=lambda x: "Todos" if x is None else TIER_DISPLAY.get(x, str(x)),
            key="composer_f_tier",
        )
        f_tipo = st.text_input("Cargo", placeholder="ex: Analista, PM, Head...", key="composer_f_tipo")
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

    with tab4:
        st.caption(f"{len(all_clients)} clientes ativos no total")
        excluded_clients = st.multiselect(
            "Clientes a excluir",
            options=all_clients,
            format_func=client_fmt,
            key="composer_exclusao_sel",
        )
        excluded_ex_ids = {c["id"] for c in excluded_clients}
        final_exclusao = [c for c in all_clients if c["id"] not in excluded_ex_ids]
        st.caption(f"Serão enviados: {len(final_exclusao)} de {len(all_clients)} clientes")

        if st.button("Aplicar exclusão", key="apply_exclusao_btn", type="primary"):
            st.session_state.composer_recipients = final_exclusao
            st.rerun()

    with tab5:
        with st.expander("ℹ️ Como montar a planilha", expanded=False):
            st.markdown(
                """
**Formato esperado (.xlsx, uma aba):**

| nome | whatsapp |
|------|----------|
| João Silva | 5511999990001 |
| Maria Souza | 5521988880002 |

**Regras:**
- Os nomes das colunas podem estar em qualquer capitalização (`Nome`, `NOME`, `nome` — tudo funciona).
- **`nome`** — nome completo do contato. `{nome}` na mensagem usa o **primeiro nome** automaticamente.
- **`whatsapp`** — número completo no formato internacional, sem `+` ou espaços: `5511999999999`.
  Números brasileiros com 11 dígitos recebem `55` automaticamente se necessário.
- Outras colunas na planilha são ignoradas.
- Contatos com número inválido ou nome ausente são pulados e exibidos como aviso.
                """
            )

        st.caption("Faça upload de um .xlsx com colunas **nome** e **whatsapp**.")
        excel_file = st.file_uploader(
            "Selecionar planilha",
            type=["xlsx"],
            key="composer_excel_uploader",
        )

        if excel_file:
            # Cache the parsed DataFrame in session state to avoid re-parsing on every rerun
            xl_cache_key = f"_xl_parsed_{excel_file.name}_{excel_file.size}"
            if xl_cache_key not in st.session_state:
                df_raw_xl = pd.read_excel(excel_file, sheet_name=0, dtype=str)
                df_raw_xl = df_raw_xl.rename(columns={c: c.strip().lower().replace(" ", "_") for c in df_raw_xl.columns})
                st.session_state[xl_cache_key] = df_raw_xl
            df_xl = st.session_state[xl_cache_key]

            errors = []
            recipients_xl = []
            for i, row in df_xl.iterrows():
                def _v(field, _r=row):
                    val = _r.get(field)
                    if val is None or (isinstance(val, float) and pd.isna(val)):
                        return None
                    s = str(val).strip()
                    return s if s else None

                nome_xl = _v("nome")
                wa_xl = _v("whatsapp")

                if not nome_xl:
                    errors.append(f"Linha {i + 2}: nome ausente — pulado")
                    continue
                if not wa_xl:
                    errors.append(f"Linha {i + 2} ({nome_xl}): whatsapp ausente — pulado")
                    continue

                phone_xl = normalize_phone(wa_xl)
                if len(phone_xl) < 10 or len(phone_xl) > 13:
                    errors.append(f"Linha {i + 2} ({nome_xl}): número inválido '{phone_xl}' — pulado")
                    continue

                recipients_xl.append({
                    "id": None,           # not a DB client
                    "nome": nome_xl,
                    "whatsapp": phone_xl,
                    "empresa": None,
                    "tickers": None,
                    "tier": None,
                })

            st.caption(f"{len(recipients_xl)} contato(s) válido(s) encontrado(s).")
            if errors:
                st.warning("Avisos:\n" + "\n".join(f"• {e}" for e in errors))

            if recipients_xl:
                if st.button("Usar esta planilha", key="use_excel_btn", type="primary"):
                    st.session_state.composer_recipients = recipients_xl
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
    if send_disabled and not st.session_state.composer_sending:
        if len(st.session_state.composer_recipients) == 0:
            st.caption("⚠️ Adicione destinatários para enviar.")
        elif not st.session_state.composer_template.strip():
            st.caption("⚠️ Escreva uma mensagem para enviar.")
        elif not waha_ok and not dry_run:
            st.caption("⚠️ WAHA desconectado. Ative o Dry Run ou conecte o WAHA.")

if send_clicked:
    st.session_state.composer_sending = True
    st.session_state.composer_results = []
    raw_recipients = st.session_state.composer_recipients
    template = st.session_state.composer_template

    # Re-fetch DB-backed recipients so any name/field edits made in Clientes
    # are reflected here — session state holds stale snapshots from selection time.
    db_ids = [r["id"] for r in raw_recipients if r.get("id") is not None]
    if db_ids:
        conn_refresh = get_conn()
        try:
            placeholders = ",".join("?" for _ in db_ids)
            fresh_rows = conn_refresh.execute(
                f"SELECT * FROM clients WHERE id IN ({placeholders})",
                db_ids,
            ).fetchall()
            db_map = {row["id"]: dict(row) for row in fresh_rows}
        finally:
            conn_refresh.close()
        recipients = [
            db_map.get(r["id"], r) if r.get("id") is not None else r
            for r in raw_recipients
        ]
    else:
        recipients = raw_recipients  # all Excel-only recipients, no DB lookup needed

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

            # Log to DB (skip for dry_run and Excel-only recipients with no DB id)
            if not dry_run and result["client"].get("id") is not None:
                # send_bulk yields "ok" on success; store as "sent" to match DB conventions
                db_status = "sent" if result["status"] == "ok" else result["status"]
                log_message(
                    conn,
                    client_id=result["client"]["id"],
                    mensagem=result["message"],
                    template=template,
                    status=db_status,
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

    STATUS_DISPLAY = {"ok": "✅ enviado", "error": "❌ falha", "dry_run": "🧪 simulado"}
    rows = []
    for r in results:
        rows.append({
            "Nome": r["client"]["nome"],
            "Número": r["client"]["whatsapp"],
            "Status": STATUS_DISPLAY.get(r["status"], r["status"]),
            "Mensagem enviada": r["message"],
            "Erro": r.get("error", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
