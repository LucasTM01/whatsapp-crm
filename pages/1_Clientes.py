import re

import pandas as pd
import streamlit as st

from core.sender import normalize_phone
from db import get_conn
from db.queries import (
    archive_client,
    create_client,
    get_all_clients,
    get_all_lists,
    get_clients_by_filters,
    update_client,
)

st.set_page_config(page_title="Clientes", page_icon="👥", layout="wide")
st.title("Clientes")

TIER_DISPLAY = {
    1: "★★★ Tier 1",
    2: "★★ Tier 2",
    3: "★ Tier 3",
    4: "Tier 4",
    5: "Tier 5",
    6: "Tier 6",
}
TIER_OPTIONS = [1, 2, 3, 4, 5, 6]
TIPO_OPTIONS = ["", "buy-side", "family office", "hedge fund", "private bank", "other"]


def _vals_differ(a, b) -> bool:
    """NaN-safe value comparison for data_editor change detection."""
    if pd.isna(a) and pd.isna(b):
        return False
    try:
        return bool(a != b)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Cached reads
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def _load_clients():
    conn = get_conn()
    rows = get_all_clients(conn, ativo_only=True)
    conn.close()
    return rows


@st.cache_data(ttl=30)
def _load_lists():
    conn = get_conn()
    rows = get_all_lists(conn)
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Filtros")
    f_tier = st.selectbox("Tier", options=[None] + TIER_OPTIONS, format_func=lambda x: "Todos" if x is None else TIER_DISPLAY.get(x, str(x)))
    f_tipo = st.selectbox("Tipo", options=[""] + TIPO_OPTIONS[1:], format_func=lambda x: "Todos" if x == "" else x)
    f_ticker = st.text_input("Ticker", placeholder="ex: EMBR3")
    lists = _load_lists()
    list_map = {lst["nome"]: lst["id"] for lst in lists}
    f_list_name = st.selectbox("Lista", options=[""] + list(list_map.keys()), format_func=lambda x: "Todas" if x == "" else x)
    f_list_id = list_map.get(f_list_name) if f_list_name else None
    empresas = sorted({c["empresa"] for c in _load_clients() if c.get("empresa")})
    f_empresa = st.selectbox("Empresa", options=[""] + empresas, format_func=lambda x: "Todas" if x == "" else x)


# ---------------------------------------------------------------------------
# Load + filter clients
# ---------------------------------------------------------------------------

any_filter = f_tier or f_tipo or f_ticker or f_list_id or f_empresa
if any_filter:
    conn = get_conn()
    clients = get_clients_by_filters(
        conn,
        tipo=f_tipo or None,
        tier=f_tier,
        ticker=f_ticker or None,
        list_id=f_list_id,
        empresa=f_empresa or None,
    )
    conn.close()
else:
    clients = _load_clients()


# ---------------------------------------------------------------------------
# Clients table (data_editor)
# ---------------------------------------------------------------------------

if not clients:
    if any_filter:
        st.warning("Nenhum cliente encontrado para os filtros aplicados.")
    else:
        st.info("Nenhum cliente cadastrado ainda. Use o formulário abaixo para adicionar.")
else:
    df = pd.DataFrame(clients)

    # Sanitize integer columns — coerce any corrupt/non-numeric values to None
    # so the data_editor never receives a stray bytes/string in a NumberColumn.
    for int_col in ("freq_dias", "tier"):
        if int_col in df.columns:
            df[int_col] = pd.to_numeric(df[int_col], errors="coerce")

    # Columns to show in the editor — single Tier column (no redundant star display)
    editable_cols = ["nome", "whatsapp", "email", "empresa", "tickers", "tipo", "tier", "freq_dias", "notas"]

    edited_df = st.data_editor(
        df[editable_cols],
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "nome": st.column_config.TextColumn("Nome", required=True),
            "whatsapp": st.column_config.TextColumn("WhatsApp", help="Formato: 5511999999999"),
            "email": st.column_config.TextColumn("Email"),
            "empresa": st.column_config.TextColumn("Empresa"),
            "tickers": st.column_config.TextColumn("Tickers", help="Separados por vírgula: EMBR3,WEGE3"),
            "tipo": st.column_config.SelectboxColumn("Tipo", options=TIPO_OPTIONS[1:]),
            "tier": st.column_config.SelectboxColumn("Tier", options=TIER_OPTIONS),
            "freq_dias": st.column_config.NumberColumn("Freq. (dias)", min_value=1, max_value=365),
            "notas": st.column_config.TextColumn("Notas"),
        },
        key="clients_editor",
    )

    # Detect and persist edits
    orig_df = df[editable_cols].reset_index(drop=True)
    new_df = edited_df.reset_index(drop=True)

    if not new_df.equals(orig_df):
        conn = get_conn()
        for idx in range(len(new_df)):
            row_orig = orig_df.iloc[idx]
            row_new = new_df.iloc[idx]
            if not row_new.equals(row_orig):
                client_id = int(df.iloc[idx]["id"])
                changed = {
                    k: (None if pd.isna(row_new[k]) else row_new[k])
                    for k in editable_cols
                    if _vals_differ(row_new[k], row_orig.get(k))
                }
                if changed:
                    if "whatsapp" in changed:
                        changed["whatsapp"] = normalize_phone(str(changed["whatsapp"]))
                    # Coerce integer fields so pandas floats/objects never reach SQLite
                    for int_col in ("freq_dias", "tier"):
                        if int_col in changed and changed[int_col] is not None:
                            try:
                                changed[int_col] = int(changed[int_col])
                            except (ValueError, TypeError):
                                changed[int_col] = None
                    update_client(conn, client_id, changed)
        conn.close()
        st.cache_data.clear()
        st.rerun()

    # Archive section — clean per-row layout inside an expander
    with st.expander("🗄️ Arquivar clientes"):
        st.caption("Arquivar remove o cliente das listas e do dashboard, mas não apaga seus dados.")
        for client in clients:
            a1, a2 = st.columns([5, 1])
            a1.markdown(f"**{client['nome']}** — {client.get('empresa') or '—'} &nbsp; {TIER_DISPLAY.get(client.get('tier', 2), '')}")
            if a2.button("Arquivar", key=f"archive_{client['id']}", use_container_width=True):
                conn = get_conn()
                archive_client(conn, client["id"])
                conn.close()
                st.cache_data.clear()
                st.success(f"{client['nome']} arquivado.")
                st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Add new client
# ---------------------------------------------------------------------------

with st.expander("+ Novo cliente"):
    with st.form("new_client_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        nome = c1.text_input("Nome *", placeholder="João Silva")
        whatsapp = c2.text_input("WhatsApp *", placeholder="5511999999999")
        email = c1.text_input("Email")
        empresa = c2.text_input("Empresa")
        tickers = c1.text_input("Tickers", placeholder="EMBR3,WEGE3")
        tipo = c2.selectbox("Tipo", options=TIPO_OPTIONS[1:])
        tier = c1.selectbox("Tier", options=TIER_OPTIONS, format_func=lambda x: TIER_DISPLAY.get(x, str(x)), index=1)
        freq_dias = c2.number_input("Frequência (dias)", min_value=1, max_value=365, value=30)
        notas = st.text_area("Notas")

        submitted = st.form_submit_button("Adicionar cliente", type="primary")
        if submitted:
            if not nome.strip():
                st.error("Nome é obrigatório.")
            elif not whatsapp.strip():
                st.error("WhatsApp é obrigatório.")
            else:
                phone = normalize_phone(whatsapp)
                if len(phone) < 10 or len(phone) > 13:
                    st.error(f"Número inválido: '{phone}'. Deve ter 10-13 dígitos.")
                else:
                    conn = get_conn()
                    try:
                        create_client(conn, {
                            "nome": nome.strip(),
                            "whatsapp": phone,
                            "email": email.strip() or None,
                            "empresa": empresa.strip() or None,
                            "tickers": tickers.strip().upper() or None,
                            "tipo": tipo or None,
                            "tier": tier,
                            "freq_dias": freq_dias,
                            "notas": notas.strip() or None,
                        })
                        st.cache_data.clear()
                        st.success(f"Cliente '{nome}' adicionado.")
                        st.rerun()
                    except Exception as exc:
                        if "UNIQUE" in str(exc):
                            st.error(f"Número {phone} já cadastrado.")
                        else:
                            st.error(f"Erro: {exc}")
                    finally:
                        conn.close()


# ---------------------------------------------------------------------------
# Import clients from Excel
# ---------------------------------------------------------------------------

with st.expander("📥 Importar via Excel"):
    st.caption(
        "Faça upload de um arquivo .xlsx com uma aba. "
        "Os nomes das colunas são reconhecidos sem distinção de maiúsculas/minúsculas. "
        "Colunas ausentes são preenchidas com vazio. "
        "Colunas obrigatórias: **nome**, **whatsapp**."
    )
    uploaded = st.file_uploader("Selecione o arquivo .xlsx", type=["xlsx"], key="excel_uploader")
    if uploaded:
        df_raw = pd.read_excel(uploaded, sheet_name=0, dtype=str)

        # Normalise column headers: strip, lowercase, spaces → underscore
        rename_map = {}
        for col in df_raw.columns:
            normalised = col.strip().lower().replace(" ", "_")
            rename_map[col] = normalised
        df_raw = df_raw.rename(columns=rename_map)

        st.caption(f"{len(df_raw)} linha(s) encontrada(s). Preview (5 primeiras):")
        st.dataframe(df_raw.head(), use_container_width=True, hide_index=True)

        if st.button("Importar clientes", key="import_excel_btn", type="primary"):
            imported, skipped = 0, []
            conn = get_conn()
            try:
                for i, row in df_raw.iterrows():
                    def _get(field, _row=row):
                        val = _row.get(field, None)
                        if val is None or (isinstance(val, float) and pd.isna(val)):
                            return None
                        s = str(val).strip()
                        return s if s else None

                    nome_val = _get("nome")
                    whatsapp_val = _get("whatsapp")

                    if not nome_val:
                        skipped.append(f"Linha {i + 2}: nome ausente")
                        continue
                    if not whatsapp_val:
                        skipped.append(f"Linha {i + 2} ({nome_val}): whatsapp ausente")
                        continue

                    phone = normalize_phone(whatsapp_val)
                    if len(phone) < 10 or len(phone) > 13:
                        skipped.append(f"Linha {i + 2} ({nome_val}): número inválido '{phone}'")
                        continue

                    # tier: int 1-6, default 2
                    tier_raw = _get("tier")
                    try:
                        tier_int = int(float(tier_raw)) if tier_raw else 2
                        if tier_int not in range(1, 7):
                            tier_int = 2
                    except (ValueError, TypeError):
                        tier_int = 2

                    # freq_dias: int, default 30
                    freq_raw = _get("freq_dias")
                    try:
                        freq_int = int(float(freq_raw)) if freq_raw else 30
                    except (ValueError, TypeError):
                        freq_int = 30

                    # tipo: validate against known options
                    tipo_raw = _get("tipo")
                    valid_tipos = [t.lower() for t in TIPO_OPTIONS if t]
                    tipo_val = tipo_raw if tipo_raw and tipo_raw.lower() in valid_tipos else None

                    tickers_raw = _get("tickers")
                    tickers_val = tickers_raw.upper() if tickers_raw else None

                    try:
                        create_client(conn, {
                            "nome": nome_val,
                            "whatsapp": phone,
                            "email": _get("email"),
                            "empresa": _get("empresa"),
                            "tickers": tickers_val,
                            "tipo": tipo_val,
                            "tier": tier_int,
                            "freq_dias": freq_int,
                            "notas": _get("notas"),
                        })
                        imported += 1
                    except Exception as exc:
                        if "UNIQUE" in str(exc):
                            skipped.append(f"Linha {i + 2} ({nome_val}): número duplicado")
                        else:
                            skipped.append(f"Linha {i + 2} ({nome_val}): {exc}")
            finally:
                conn.close()

            st.cache_data.clear()
            st.success(f"{imported} cliente(s) importado(s) com sucesso.")
            if skipped:
                st.warning("Linhas ignoradas:\n" + "\n".join(f"• {s}" for s in skipped))
            if imported:
                st.rerun()
