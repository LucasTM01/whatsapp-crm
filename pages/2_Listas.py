import streamlit as st

from db import get_conn
from db.queries import (
    create_list,
    delete_list,
    get_all_clients,
    get_all_lists,
    get_list_member_counts,
    get_list_members,
    rename_list,
    set_list_members,
)

st.set_page_config(page_title="Listas", page_icon="📋", layout="wide")
st.title("Listas de Distribuição")


# ---------------------------------------------------------------------------
# Cached reads
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def _load_lists_with_counts():
    conn = get_conn()
    lists = get_all_lists(conn)
    counts = get_list_member_counts(conn)
    conn.close()
    return lists, counts


@st.cache_data(ttl=30)
def _load_all_clients():
    conn = get_conn()
    clients = get_all_clients(conn, ativo_only=True)
    conn.close()
    return clients


# ---------------------------------------------------------------------------
# Page state
# ---------------------------------------------------------------------------

if "selected_list_id" not in st.session_state:
    st.session_state.selected_list_id = None


# ---------------------------------------------------------------------------
# Layout — two columns
# ---------------------------------------------------------------------------

left, right = st.columns([1, 2])

lists, counts = _load_lists_with_counts()
all_clients = _load_all_clients()
client_map = {c["id"]: c for c in all_clients}
client_options = {c["id"]: f"{c['nome']} ({c.get('empresa') or '—'})" for c in all_clients}

with left:
    st.subheader("Listas")

    if not lists:
        st.info("Nenhuma lista criada ainda.")
    else:
        for lst in lists:
            cnt = counts.get(lst["id"], 0)
            is_selected = st.session_state.selected_list_id == lst["id"]
            label = f"{'▶ ' if is_selected else ''}{lst['nome']} ({cnt})"
            if st.button(label, key=f"sel_list_{lst['id']}", use_container_width=True):
                st.session_state.selected_list_id = lst["id"]
                st.rerun()

    st.divider()

    # Create new list
    st.markdown("**Nova lista**")
    with st.form("new_list_form", clear_on_submit=True):
        new_nome = st.text_input("Nome *", placeholder="ex: Cobertura Embraer")
        new_desc = st.text_input("Descrição", placeholder="Opcional")
        if st.form_submit_button("Criar lista", type="primary"):
            if not new_nome.strip():
                st.error("Nome é obrigatório.")
            else:
                conn = get_conn()
                try:
                    new_id = create_list(conn, new_nome.strip(), new_desc.strip())
                    st.session_state.selected_list_id = new_id
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    if "UNIQUE" in str(exc):
                        st.error(f"Já existe uma lista com o nome '{new_nome}'.")
                    else:
                        st.error(f"Erro: {exc}")
                finally:
                    conn.close()

with right:
    sel_id = st.session_state.selected_list_id

    if sel_id is None:
        st.info("Selecione uma lista à esquerda para gerenciar seus membros.")
    else:
        # Find selected list
        sel_list = next((l for l in lists if l["id"] == sel_id), None)
        if sel_list is None:
            st.session_state.selected_list_id = None
            st.rerun()

        st.subheader(sel_list["nome"])
        if sel_list.get("descricao"):
            st.caption(sel_list["descricao"])

        # Rename / Delete actions
        act1, act2 = st.columns(2)
        with act1:
            with st.popover("✏️ Renomear"):
                with st.form(f"rename_{sel_id}"):
                    new_name = st.text_input("Novo nome", value=sel_list["nome"])
                    if st.form_submit_button("Renomear"):
                        if new_name.strip() and new_name.strip() != sel_list["nome"]:
                            conn = get_conn()
                            try:
                                rename_list(conn, sel_id, new_name.strip())
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Erro: {exc}")
                            finally:
                                conn.close()

        with act2:
            with st.popover("🗑️ Excluir lista"):
                st.warning(f"Excluir **{sel_list['nome']}**? Esta ação não pode ser desfeita.")
                if st.button("Confirmar exclusão", type="primary", key=f"confirm_del_{sel_id}"):
                    conn = get_conn()
                    delete_list(conn, sel_id)
                    conn.close()
                    st.session_state.selected_list_id = None
                    st.cache_data.clear()
                    st.rerun()

        st.divider()

        # Members multiselect
        conn = get_conn()
        current_members = get_list_members(conn, sel_id)
        conn.close()
        current_ids = [c["id"] for c in current_members]

        st.markdown(f"**Membros** ({len(current_ids)} clientes)")

        selected_ids = st.multiselect(
            "Selecionar clientes",
            options=list(client_options.keys()),
            default=current_ids,
            format_func=lambda x: client_options.get(x, str(x)),
            key=f"members_{sel_id}",
        )

        if set(selected_ids) != set(current_ids):
            conn = get_conn()
            set_list_members(conn, sel_id, selected_ids)
            conn.close()
            st.cache_data.clear()
            st.rerun()

        # Show current members as tags
        if current_members:
            tags = " ".join(f"`{c['nome']}`" for c in current_members)
            st.markdown(tags)
        else:
            st.caption("Nenhum membro nesta lista.")
