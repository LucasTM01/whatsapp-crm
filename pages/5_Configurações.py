import streamlit as st

from core.notion_sync import (
    initialize_notion_databases,
    pull_from_notion,
    push_to_notion,
    validate_notion_credentials,
)
from db import get_conn
from db.queries import get_setting, set_setting

st.set_page_config(page_title="Configurações", page_icon="⚙️", layout="wide")
st.title("Configurações")

# ---------------------------------------------------------------------------
# Load current settings
# ---------------------------------------------------------------------------

conn = get_conn()
try:
    saved_token = get_setting(conn, "notion_token") or ""
    saved_parent = get_setting(conn, "notion_parent_page_id") or ""
    saved_clients_db = get_setting(conn, "notion_clients_db_id") or ""
    saved_meetings_db = get_setting(conn, "notion_meetings_db_id") or ""
finally:
    conn.close()


# ---------------------------------------------------------------------------
# Notion credentials
# ---------------------------------------------------------------------------

st.subheader("Notion")

with st.form("notion_settings_form"):
    token = st.text_input(
        "Token de integração",
        value=saved_token,
        type="password",
        help="Crie uma integração em https://www.notion.so/my-integrations",
    )
    parent_page_id = st.text_input(
        "ID da página pai",
        value=saved_parent,
        help="Página onde os databases serão criados (obrigatório se os IDs abaixo estiverem vazios)",
    )
    clients_db_id = st.text_input(
        "ID do database de Clientes",
        value=saved_clients_db,
        help="Deixe vazio para criar automaticamente",
    )
    meetings_db_id = st.text_input(
        "ID do database de Reuniões",
        value=saved_meetings_db,
        help="Deixe vazio para criar automaticamente",
    )

    submitted = st.form_submit_button("Salvar", type="primary")

if submitted:
    if not token.strip():
        st.error("Token é obrigatório.")
    else:
        token = token.strip()
        parent_page_id = parent_page_id.strip()
        clients_db_id = clients_db_id.strip()
        meetings_db_id = meetings_db_id.strip()

        # Validate token
        with st.spinner("Validando credenciais..."):
            valid = validate_notion_credentials(token)

        if not valid:
            st.error("Token inválido — verifique a integração no Notion.")
        else:
            st.success("Token válido.")

            # Save token and parent page ID
            conn = get_conn()
            try:
                set_setting(conn, "notion_token", token)
                set_setting(conn, "notion_parent_page_id", parent_page_id or None)
            finally:
                conn.close()

            # Initialize databases if needed
            needs_init = (not clients_db_id) or (not meetings_db_id)
            if needs_init and not parent_page_id:
                st.error("ID da página pai é obrigatório para criar databases automaticamente.")
            elif needs_init:
                with st.spinner("Inicializando databases no Notion..."):
                    try:
                        conn = get_conn()
                        try:
                            result = initialize_notion_databases(
                                conn,
                                token,
                                parent_page_id,
                                clients_db_id=clients_db_id or None,
                                meetings_db_id=meetings_db_id or None,
                            )
                        finally:
                            conn.close()
                        for w in result.get("warnings", []):
                            st.warning(w)
                        st.success(
                            f"Databases configurados.\n\n"
                            f"- Clientes: `{result['clients_db_id']}`\n"
                            f"- Reuniões: `{result['meetings_db_id']}`"
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Erro ao inicializar databases: {exc}")
            else:
                # Just save the provided IDs
                conn = get_conn()
                try:
                    set_setting(conn, "notion_clients_db_id", clients_db_id)
                    set_setting(conn, "notion_meetings_db_id", meetings_db_id)
                finally:
                    conn.close()
                st.success("Configurações salvas.")
                st.rerun()


# ---------------------------------------------------------------------------
# Connection status
# ---------------------------------------------------------------------------

if saved_token:
    st.divider()
    st.caption("Status da conexão")

    valid = validate_notion_credentials(saved_token)
    if valid:
        st.success("Conectado ao Notion")
    else:
        st.error("Token inválido ou expirado")

    if saved_clients_db:
        st.caption(f"Database de Clientes: `{saved_clients_db}`")
    if saved_meetings_db:
        st.caption(f"Database de Reuniões: `{saved_meetings_db}`")


# ---------------------------------------------------------------------------
# Sync actions
# ---------------------------------------------------------------------------

if saved_token and saved_clients_db:
    st.divider()
    st.subheader("Sincronização")

    col_pull, col_push = st.columns(2)

    with col_pull:
        if st.button("⬇️ Pull do Notion", use_container_width=True, type="primary"):
            with st.spinner("Puxando dados do Notion..."):
                conn = get_conn()
                try:
                    stats = pull_from_notion(conn, saved_token, saved_clients_db)
                finally:
                    conn.close()

            st.success(
                f"Pull concluído: **{stats['created']}** criados, "
                f"**{stats['updated']}** atualizados, "
                f"**{stats['skipped']}** ignorados."
            )
            if stats["errors"]:
                st.error(f"{len(stats['errors'])} erro(s) no pull:")
                for err in stats["errors"]:
                    st.text(err)

            st.cache_data.clear()

    with col_push:
        if st.button("⬆️ Push para Notion", use_container_width=True):
            with st.spinner("Enviando dados para o Notion..."):
                conn = get_conn()
                try:
                    stats = push_to_notion(conn, saved_token, saved_clients_db)
                finally:
                    conn.close()

            if stats["created"] > 0:
                st.success(
                    f"Push concluído: **{stats['created']}** criado(s) no Notion "
                    f"| {stats['skipped']} já sincronizado(s)."
                )
            elif not stats["errors"]:
                st.info(
                    f"Nenhum cliente novo para enviar. "
                    f"**{stats['skipped']}** de **{stats['total']}** já estão no Notion."
                )

            if stats["errors"]:
                st.error(f"{len(stats['errors'])} erro(s) no push:")
                for err in stats["errors"]:
                    st.text(err)

            st.cache_data.clear()


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

if saved_token:
    st.divider()
    st.caption("🔧 Manutenção de schema")

    col_reinit, col_clear = st.columns(2)

    with col_reinit:
        reinit_disabled = not saved_parent
        if st.button(
            "Reinicializar schemas no Notion",
            use_container_width=True,
            disabled=reinit_disabled,
            help="Requer ID da página pai" if reinit_disabled else "Adiciona colunas faltantes aos databases existentes",
        ):
            with st.spinner("Atualizando schemas..."):
                try:
                    conn = get_conn()
                    try:
                        result = initialize_notion_databases(
                            conn,
                            saved_token,
                            saved_parent,
                            clients_db_id=saved_clients_db or None,
                            meetings_db_id=saved_meetings_db or None,
                        )
                    finally:
                        conn.close()
                    for w in result.get("warnings", []):
                        st.warning(w)
                    st.success(
                        f"Schemas atualizados.\n\n"
                        f"- Clientes: `{result['clients_db_id']}`\n"
                        f"- Reuniões: `{result['meetings_db_id']}`"
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erro ao reinicializar schemas: {exc}")

    with col_clear:
        if st.button(
            "🗑️ Limpar IDs de databases salvos",
            use_container_width=True,
            help="Use isto se os databases foram deletados do Notion e você quer recriar do zero",
        ):
            conn = get_conn()
            try:
                set_setting(conn, "notion_clients_db_id", None)
                set_setting(conn, "notion_meetings_db_id", None)
            finally:
                conn.close()
            st.success("IDs limpos. Preencha a página pai e clique em Salvar para recriar os databases.")
            st.rerun()
