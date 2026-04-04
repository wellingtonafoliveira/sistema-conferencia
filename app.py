import base64
import json
import os
from copy import deepcopy
from datetime import datetime
from io import BytesIO

import pandas as pd
import plotly.express as px
import resend
import streamlit as st
from filelock import FileLock
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


# =========================================================
# CONFIG
# =========================================================
st.set_page_config(page_title="Sistema de Conferência", layout="wide")

DATA_FILE = "data_store.json"
LOCK_FILE = "data_store.lock"
LOGO_FILE = "Nadir_Branco_Laranja.png"

REQUIRED_VL06_COLUMNS = [
    "Nº transporte",
    "Remessa",
    "Documento referência",
    "Material",
    "Nome agente de frete",
    "Denominação de item",
    "Qtd.remessa",
    "Nome do emissor da ordem",
    "Peso total",
    "Peso líquido",
    "Volume",
    "Data agenda",
    "Hora agenda",
    "Perfil de carregamento",
    "Tipo de carga",
]

resend.api_key = st.secrets["resend"]["api_key"]
EMAIL_FROM = st.secrets["email"]["from_email"]
EMAIL_TO = st.secrets["email"]["to_email"]


# =========================================================
# HELPERS UI
# =========================================================
def show_logo_main(width=220):
    if os.path.exists(LOGO_FILE):
        st.image(LOGO_FILE, width=width)


def show_logo_sidebar():
    if os.path.exists(LOGO_FILE):
        st.sidebar.image(LOGO_FILE, use_container_width=True)


# =========================================================
# STORAGE JSON
# =========================================================
def default_store():
    return {
        "base_vl06": [],
        "sku_base": [],
        "conferencias": {},
    }


def load_store():
    with FileLock(LOCK_FILE):
        if not os.path.exists(DATA_FILE):
            return default_store()
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)


def save_store(data):
    with FileLock(LOCK_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# =========================================================
# AUTH
# =========================================================
def login_screen():
    col_logo, col_title = st.columns([1, 3])

    with col_logo:
        show_logo_main(width=180)

    with col_title:
        st.title("Acesso ao Sistema")

    usuario = st.text_input("Usuário")
    senha = st.text_input("Senha", type="password")

    if st.button("Entrar", use_container_width=True):
        users = st.secrets["users"]
        if usuario in users:
            stored = users[usuario]
            senha_ok, perfil = stored.split("|")
            if senha == senha_ok:
                st.session_state["auth_ok"] = True
                st.session_state["usuario"] = usuario
                st.session_state["perfil"] = perfil
                st.rerun()

        st.error("Usuário ou senha inválidos.")


def logout():
    for k in ["auth_ok", "usuario", "perfil"]:
        if k in st.session_state:
            del st.session_state[k]
    st.rerun()


def allowed_sections():
    perfil = st.session_state.get("perfil", "")
    if perfil == "assistente":
        return ["Assistente", "Conferência"]
    if perfil == "conferente":
        return ["Conferência"]
    if perfil == "gestao":
        return ["Gestão", "Conferência"]
    return []


# =========================================================
# UTILS
# =========================================================
def clean_str(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def clean_id(value):
    text = clean_str(value)
    if not text:
        return ""
    try:
        num = float(text.replace(",", "."))
        if num.is_integer():
            return str(int(num))
    except Exception:
        pass
    return text


def to_int_qty(value):
    if pd.isna(value):
        return 0
    text = str(value).strip()
    if not text:
        return 0
    text = text.replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return int(float(text))
    except Exception:
        return 0


def to_float_qty(value):
    if pd.isna(value):
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    text = text.replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except Exception:
        return 0.0


def format_date_only(value):
    if pd.isna(value):
        return ""
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        return clean_str(value)
    return dt.strftime("%d/%m/%Y")


def format_time_only(value):
    if pd.isna(value):
        return ""
    text = clean_str(value)
    if not text:
        return ""
    dt = pd.to_datetime(value, errors="coerce")
    if pd.notna(dt):
        return dt.strftime("%H:%M")
    return text


def compute_item_status(qtd_conferida, qtd_solicitada):
    if int(qtd_conferida) == 0:
        return "PENDENTE"
    if int(qtd_conferida) == int(qtd_solicitada):
        return "OK"
    return "DIVERGENTE"


def apply_statuses(df):
    out = df.copy()
    out["qtd_conferida"] = out["qtd_conferida"].fillna(0).astype(int)
    out["qtd_solicitada"] = out["qtd_solicitada"].fillna(0).astype(int)
    out["status_item"] = out.apply(
        lambda row: compute_item_status(row["qtd_conferida"], row["qtd_solicitada"]),
        axis=1,
    )
    return out


# =========================================================
# BASES
# =========================================================
def normalize_vl06(df_raw):
    df = df_raw.copy()
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_VL06_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Colunas ausentes na VL06: {missing}")

    out = pd.DataFrame({
        "dt": df["Nº transporte"].apply(clean_id),
        "remessa": df["Remessa"].apply(clean_id),
        "doc_referencia": df["Documento referência"].apply(clean_id),
        "material": df["Material"].apply(clean_id),
        "transportadora": df["Nome agente de frete"].apply(clean_str),
        "descricao": df["Denominação de item"].apply(clean_str),
        "qtd_solicitada": df["Qtd.remessa"].apply(to_int_qty),
        "cliente": df["Nome do emissor da ordem"].apply(clean_str),
        "peso_total": df["Peso total"].apply(to_float_qty),
        "peso_liquido": df["Peso líquido"].apply(to_float_qty),
        "volume": df["Volume"].apply(to_float_qty),
        "data_agenda": df["Data agenda"].apply(format_date_only),
        "hora_agenda": df["Hora agenda"].apply(format_time_only),
        "perfil_carregamento": df["Perfil de carregamento"].apply(clean_str),
        "tipo_carga": df["Tipo de carga"].apply(clean_str),
        "metragem_cubica": df["Volume"].apply(to_float_qty) / 1000.0,
    })

    out = out[
        (out["dt"] != "") &
        (out["material"] != "") &
        (out["qtd_solicitada"] > 0)
    ].copy()

    out = out.drop_duplicates(
        subset=["dt", "remessa", "doc_referencia", "material", "descricao", "qtd_solicitada"]
    ).reset_index(drop=True)

    out["qtd_conferida"] = 0
    out["status_item"] = "PENDENTE"
    return out


def normalize_sku_base(df_raw):
    df = df_raw.copy()
    df.columns = [str(c).strip() for c in df.columns]
    lower_map = {c.lower(): c for c in df.columns}

    def find_col(options):
        for opt in options:
            if opt in lower_map:
                return lower_map[opt]
        return None

    sku_col = find_col(["sku", "material", "codigo", "código", "item"])
    desc_col = find_col(["descricao", "descrição", "denominação", "descricao item"])
    qtd_col = find_col(["qtd_palete", "qtd por palete", "quantidade por palete", "quantidade", "qtd"])

    if not sku_col or not qtd_col:
        raise ValueError("A base SKU precisa ter ao menos as colunas SKU e quantidade por palete.")

    out = pd.DataFrame({
        "sku": df[sku_col].apply(clean_id),
        "descricao": df[desc_col].apply(clean_str) if desc_col else "",
        "qtd_palete": df[qtd_col].apply(to_int_qty),
    })

    out = out[(out["sku"] != "") & (out["qtd_palete"] > 0)].drop_duplicates(subset=["sku"]).reset_index(drop=True)
    return out


# =========================================================
# STORE HELPERS
# =========================================================
def get_base_vl06_df():
    store = load_store()
    rows = store.get("base_vl06", [])
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def save_base_vl06_df(df):
    store = load_store()
    store["base_vl06"] = df.to_dict(orient="records")
    save_store(store)


def get_sku_df():
    store = load_store()
    rows = store.get("sku_base", [])
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def save_sku_df(df):
    store = load_store()
    store["sku_base"] = df.to_dict(orient="records")
    save_store(store)


def get_conferencias():
    store = load_store()
    return store.get("conferencias", {})


def save_conferencias(confs):
    store = load_store()
    store["conferencias"] = confs
    save_store(store)


def get_dt_list():
    base = get_base_vl06_df()
    if base.empty:
        return []
    return sorted(base["dt"].astype(str).unique().tolist())


def get_dt_snapshot(dt):
    confs = get_conferencias()
    if dt in confs:
        return confs[dt]

    base = get_base_vl06_df()
    df_dt = base[base["dt"] == dt].copy()
    if df_dt.empty:
        return None

    snapshot = {
        "meta": {
            "dt": dt,
            "conferente": "",
            "turno": "Manhã",
            "inicio": "",
            "fim": "",
            "status_dt": "PENDENTE",
            "cliente": clean_str(df_dt["cliente"].iloc[0]),
            "transportadora": clean_str(df_dt["transportadora"].iloc[0]),
            "qtd_remessas": int(df_dt["remessa"].nunique()),
            "data_agenda": clean_str(df_dt["data_agenda"].iloc[0]),
            "hora_agenda": clean_str(df_dt["hora_agenda"].iloc[0]),
            "perfil_carregamento": clean_str(df_dt["perfil_carregamento"].iloc[0]),
            "tipo_carga": clean_str(df_dt["tipo_carga"].iloc[0]),
            "total_caixas": int(df_dt["qtd_solicitada"].fillna(0).sum()),
            "metragem_cubica": float(df_dt["metragem_cubica"].fillna(0).sum()),
        },
        "items": apply_statuses(df_dt).to_dict(orient="records"),
    }

    confs[dt] = snapshot
    save_conferencias(confs)
    return snapshot


def save_dt_snapshot(dt, snapshot):
    confs = get_conferencias()
    confs[dt] = snapshot
    save_conferencias(confs)


def snapshot_to_df(snapshot):
    return pd.DataFrame(snapshot["items"])


def update_snapshot_items(dt, df_items):
    snapshot = deepcopy(get_dt_snapshot(dt))
    snapshot["items"] = apply_statuses(df_items).to_dict(orient="records")
    save_dt_snapshot(dt, snapshot)


def dt_locked(snapshot):
    return snapshot["meta"].get("status_dt") == "FINALIZADO"


def dt_can_reopen(snapshot):
    return snapshot["meta"].get("status_dt") == "DIVERGENTE"


def mark_dt_started(dt, conferente, turno):
    snapshot = deepcopy(get_dt_snapshot(dt))
    if not snapshot["meta"].get("inicio"):
        snapshot["meta"]["inicio"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot["meta"]["conferente"] = conferente
    snapshot["meta"]["turno"] = turno
    if snapshot["meta"]["status_dt"] == "PENDENTE":
        snapshot["meta"]["status_dt"] = "EM_ANDAMENTO"
    save_dt_snapshot(dt, snapshot)


def finalize_dt(dt, final_status, conferente, turno):
    snapshot = deepcopy(get_dt_snapshot(dt))
    snapshot["meta"]["conferente"] = conferente
    snapshot["meta"]["turno"] = turno
    if not snapshot["meta"].get("inicio"):
        snapshot["meta"]["inicio"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot["meta"]["fim"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot["meta"]["status_dt"] = final_status
    save_dt_snapshot(dt, snapshot)


def reopen_dt(dt):
    snapshot = deepcopy(get_dt_snapshot(dt))
    if snapshot["meta"]["status_dt"] == "DIVERGENTE":
        snapshot["meta"]["status_dt"] = "EM_ANDAMENTO"
        snapshot["meta"]["fim"] = ""
        save_dt_snapshot(dt, snapshot)


# =========================================================
# PDF + EMAIL
# =========================================================
def generate_pdf_bytes(snapshot):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=18,
        rightMargin=18,
        topMargin=18,
        bottomMargin=18,
    )
    styles = getSampleStyleSheet()
    story = []

    meta = snapshot["meta"]
    items_df = snapshot_to_df(snapshot).copy().sort_values(by=["remessa", "material"]).reset_index(drop=True)

    story.append(Paragraph(f"Espelho de Conferência - DT {meta.get('dt', '')}", styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Status: {meta.get('status_dt', '')}", styles["Normal"]))
    story.append(Paragraph(f"Cliente: {meta.get('cliente', '')}", styles["Normal"]))
    story.append(Paragraph(f"Transportadora: {meta.get('transportadora', '')}", styles["Normal"]))
    story.append(Paragraph(f"Conferente: {meta.get('conferente', '')}", styles["Normal"]))
    story.append(Paragraph(f"Turno: {meta.get('turno', '')}", styles["Normal"]))
    story.append(Paragraph(f"Início: {meta.get('inicio', '')}", styles["Normal"]))
    story.append(Paragraph(f"Fim: {meta.get('fim', '')}", styles["Normal"]))
    story.append(Paragraph(f"Data agenda: {meta.get('data_agenda', '')}", styles["Normal"]))
    story.append(Paragraph(f"Hora agenda: {meta.get('hora_agenda', '')}", styles["Normal"]))
    story.append(Paragraph(f"Perfil de carregamento: {meta.get('perfil_carregamento', '')}", styles["Normal"]))
    story.append(Paragraph(f"Tipo de carga: {meta.get('tipo_carga', '')}", styles["Normal"]))
    story.append(Paragraph(f"Quantidade de remessas: {meta.get('qtd_remessas', 0)}", styles["Normal"]))
    story.append(Paragraph(f"Total de caixas: {meta.get('total_caixas', 0)}", styles["Normal"]))
    story.append(Paragraph(f"Metragem cúbica: {meta.get('metragem_cubica', 0):.3f} m³", styles["Normal"]))
    story.append(Spacer(1, 10))

    table_data = [[
        "Remessa", "Doc. Ref.", "Material", "Descrição",
        "Qtd Solicitada", "Qtd Conferida", "Status"
    ]]

    for _, row in items_df.iterrows():
        table_data.append([
            str(row["remessa"]),
            str(row["doc_referencia"]),
            str(row["material"]),
            str(row["descricao"]),
            str(row["qtd_solicitada"]),
            str(row["qtd_conferida"]),
            str(row["status_item"]),
        ])

    table = Table(table_data, repeatRows=1, colWidths=[70, 80, 90, 280, 80, 80, 80])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E2F3")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 10),
    ]))

    story.append(table)
    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def send_pdf_email(pdf_bytes, filename, dt, status_dt):
    attachment_b64 = base64.b64encode(pdf_bytes).decode("utf-8")

    params = {
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": f"Conferência DT {dt} - {status_dt}",
        "html": f"""
            <strong>Conferência finalizada</strong>
            <p>DT: {dt}</p>
            <p>Status: {status_dt}</p>
        """,
        "attachments": [
            {
                "filename": filename,
                "content": attachment_b64,
            }
        ],
    }

    return resend.Emails.send(params)


# =========================================================
# GESTÃO
# =========================================================
def build_management_df():
    base = get_base_vl06_df()
    if base.empty:
        return pd.DataFrame()

    rows = []
    for dt in get_dt_list():
        snapshot = get_dt_snapshot(dt)
        items_df = snapshot_to_df(snapshot)
        meta = snapshot["meta"]

        rows.append({
            "DT": dt,
            "Cliente": meta.get("cliente", ""),
            "Transportadora": meta.get("transportadora", ""),
            "Tipo de carga": meta.get("tipo_carga", ""),
            "Perfil carregamento": meta.get("perfil_carregamento", ""),
            "Data agenda": meta.get("data_agenda", ""),
            "Hora agenda": meta.get("hora_agenda", ""),
            "Remessas": int(items_df["remessa"].nunique()) if not items_df.empty else 0,
            "Conferente": meta.get("conferente", ""),
            "Turno": meta.get("turno", ""),
            "Início": meta.get("inicio", ""),
            "Fim": meta.get("fim", ""),
            "Status DT": meta.get("status_dt", "PENDENTE"),
            "Itens": len(items_df),
            "SKU únicos": items_df["material"].astype(str).nunique() if not items_df.empty else 0,
            "Total caixas": int(items_df["qtd_solicitada"].fillna(0).sum()) if not items_df.empty else 0,
            "M³": round(float(items_df["metragem_cubica"].fillna(0).sum()), 3) if "metragem_cubica" in items_df.columns else 0,
            "OK": int((items_df["status_item"] == "OK").sum()) if not items_df.empty else 0,
            "Divergentes": int((items_df["status_item"] == "DIVERGENTE").sum()) if not items_df.empty else 0,
            "Pendentes": int((items_df["status_item"] == "PENDENTE").sum()) if not items_df.empty else 0,
        })

    return pd.DataFrame(rows)


# =========================================================
# PÁGINAS
# =========================================================
def page_assistente():
    show_logo_main()
    st.title("Assistente de Logística")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Carregar VL06")
        vl06_file = st.file_uploader("Selecione o arquivo VL06", type=["xlsx", "xls"], key="vl06_file")
        replace = st.checkbox("Substituir base atual", value=True)

        if vl06_file is not None:
            try:
                raw = pd.read_excel(vl06_file)
                normalized = normalize_vl06(raw)

                if st.button("Processar VL06", key="process_vl06"):
                    if replace or get_base_vl06_df().empty:
                        save_base_vl06_df(normalized)
                        save_conferencias({})
                    else:
                        combined = pd.concat([get_base_vl06_df(), normalized], ignore_index=True)
                        combined = combined.drop_duplicates(
                            subset=["dt", "remessa", "doc_referencia", "material"]
                        ).reset_index(drop=True)
                        save_base_vl06_df(combined)
                        save_conferencias({})

                    st.success(f"VL06 carregada com {len(normalized)} linhas válidas.")
                    st.dataframe(normalized.head(20), use_container_width=True)
            except Exception as e:
                st.error(f"Erro ao processar a VL06: {e}")

    with col2:
        st.subheader("Carregar base SKU por palete")
        sku_file = st.file_uploader("Base SKU (xlsx/csv)", type=["xlsx", "xls", "csv"], key="sku_file")

        if sku_file is not None:
            try:
                raw = pd.read_csv(sku_file) if sku_file.name.lower().endswith(".csv") else pd.read_excel(sku_file)
                normalized = normalize_sku_base(raw)

                if st.button("Processar base SKU", key="process_sku"):
                    save_sku_df(normalized)
                    st.success(f"Base SKU carregada com {len(normalized)} itens.")
                    st.dataframe(normalized.head(20), use_container_width=True)
            except Exception as e:
                st.error(f"Erro ao processar base SKU: {e}")

    st.divider()

    base = get_base_vl06_df()
    if not base.empty:
        a, b, c = st.columns(3)
        a.metric("Linhas válidas", len(base))
        b.metric("DTs", base["dt"].nunique())
        c.metric("Remessas", base["remessa"].nunique())
        st.dataframe(base.head(50), use_container_width=True)

    st.divider()
    st.subheader("Refazer conferência de DT divergente")

    mgmt = build_management_df()
    if not mgmt.empty:
        divergentes = mgmt[mgmt["Status DT"] == "DIVERGENTE"]["DT"].tolist()
        if divergentes:
            dt_refazer = st.selectbox("Selecione a DT divergente", divergentes, key="dt_refazer_assistente")
            if st.button("Refazer conferência", key="btn_refazer_assistente"):
                snapshot = get_dt_snapshot(dt_refazer)
                if dt_can_reopen(snapshot):
                    reopen_dt(dt_refazer)
                    st.success(f"DT {dt_refazer} liberada para refazer conferência.")
                    st.rerun()
                else:
                    st.error("Apenas DTs divergentes podem ser refeitas.")
        else:
            st.info("Não há DTs divergentes para refazer.")


def page_conferencia():
    show_logo_main()
    st.title("Conferência")

    base = get_base_vl06_df()
    if base.empty:
        st.info("Primeiro carregue a VL06 na área do Assistente.")
        return

    dt_list = get_dt_list()
    search = st.text_input("Pesquisar DT")
    filtered = [d for d in dt_list if search.strip() in d] if search.strip() else dt_list

    if not filtered:
        st.warning("Nenhuma DT encontrada.")
        return

    dt = st.selectbox("Selecione a DT", filtered)
    snapshot = get_dt_snapshot(dt)
    meta = snapshot["meta"]

    items_df = snapshot_to_df(snapshot)
    items_df = apply_statuses(items_df).sort_values(by=["remessa", "material"]).reset_index(drop=True)

    qtd_skus_unicos = items_df["material"].astype(str).nunique()
    total_caixas = int(items_df["qtd_solicitada"].fillna(0).sum())
    data_agenda = meta.get("data_agenda", "")
    hora_agenda = meta.get("hora_agenda", "")
    perfil_carregamento = meta.get("perfil_carregamento", "")
    tipo_carga = meta.get("tipo_carga", "")
    metragem_cubica = float(items_df["metragem_cubica"].fillna(0).sum()) if "metragem_cubica" in items_df.columns else 0

    top1, top2, top3, top4 = st.columns(4)
    top1.info(f"**Cliente**\n\n{meta.get('cliente', '')}")
    top2.info(f"**Transportadora**\n\n{meta.get('transportadora', '')}")
    top3.info(f"**Status DT**\n\n{meta.get('status_dt', 'PENDENTE')}")
    top4.info(f"**Remessas na DT**\n\n{meta.get('qtd_remessas', 0)}")

    top5, top6, top7, top8 = st.columns(4)
    top5.info(f"**SKU únicos**\n\n{qtd_skus_unicos}")
    top6.info(f"**Total de caixas**\n\n{total_caixas}")
    top7.info(f"**Data agenda**\n\n{data_agenda}")
    top8.info(f"**Hora agenda**\n\n{hora_agenda}")

    top9, top10, top11 = st.columns(3)
    top9.info(f"**Perfil de carregamento**\n\n{perfil_carregamento}")
    top10.info(f"**Tipo de carga**\n\n{tipo_carga}")
    top11.info(f"**Metragem cúbica**\n\n{metragem_cubica:.3f} m³")

    c1, c2, c3, c4 = st.columns(4)
    conferente = c1.text_input("Conferente", value=meta.get("conferente", ""), key=f"conf_{dt}")
    turno = c2.selectbox(
        "Turno",
        ["Manhã", "Tarde", "Noite"],
        index=["Manhã", "Tarde", "Noite"].index(meta.get("turno", "Manhã")),
        key=f"turno_{dt}",
    )
    c3.text_input("Início", value=meta.get("inicio", ""), disabled=True, key=f"inicio_{dt}")
    c4.text_input("Fim", value=meta.get("fim", ""), disabled=True, key=f"fim_{dt}")

    snapshot["meta"]["conferente"] = conferente
    snapshot["meta"]["turno"] = turno
    save_dt_snapshot(dt, snapshot)

    if dt_locked(snapshot):
        st.error("Esta DT foi finalizada sem divergência e está bloqueada.")
    elif meta.get("status_dt") == "DIVERGENTE":
        st.warning("Esta DT foi finalizada com divergência e pode ser reaberta pelo Assistente ou Gestão.")
    else:
        mark_dt_started(dt, conferente, turno)

    sku_df = get_sku_df()
    sku_map = dict(zip(sku_df["sku"].astype(str), sku_df["qtd_palete"].astype(int))) if not sku_df.empty else {}

    st.subheader("Lançamento por palete")
    x1, x2 = st.columns([3, 1])
    codigo_bip = x1.text_input("Bipar SKU", key=f"bip_{dt}")

    if x2.button("Lançar palete", disabled=dt_locked(get_dt_snapshot(dt)), key=f"btn_bip_{dt}"):
        if not codigo_bip:
            st.warning("Informe o SKU.")
        elif codigo_bip not in sku_map:
            st.error("SKU não cadastrado na base SKU.")
        else:
            qtd_palete = int(sku_map[codigo_bip])
            mask = items_df["material"].astype(str) == str(codigo_bip)
            if not mask.any():
                st.error("SKU não encontrado nesta DT.")
            else:
                items_df.loc[mask, "qtd_conferida"] = items_df.loc[mask, "qtd_conferida"].astype(int) + qtd_palete
                items_df = apply_statuses(items_df)
                update_snapshot_items(dt, items_df)
                st.success(f"{qtd_palete} unidades lançadas para o SKU {codigo_bip}.")
                st.rerun()

    st.subheader("Lançamento manual")
    m1, m2, m3 = st.columns([2, 1, 1])
    sku_manual = m1.text_input("SKU manual", key=f"sku_manual_{dt}")
    qtd_manual = m2.number_input("Quantidade", min_value=1, step=1, key=f"qtd_manual_{dt}")

    if m3.button("Adicionar manual", disabled=dt_locked(get_dt_snapshot(dt)), key=f"btn_manual_{dt}"):
        mask = items_df["material"].astype(str) == str(sku_manual)
        if not mask.any():
            st.error("SKU não encontrado nesta DT.")
        else:
            items_df.loc[mask, "qtd_conferida"] = items_df.loc[mask, "qtd_conferida"].astype(int) + int(qtd_manual)
            items_df = apply_statuses(items_df)
            update_snapshot_items(dt, items_df)
            st.success(f"{qtd_manual} unidades lançadas manualmente para o SKU {sku_manual}.")
            st.rerun()

    st.subheader("Itens da DT")
    editor_df = items_df[[
        "remessa", "doc_referencia", "material", "descricao",
        "qtd_solicitada", "qtd_conferida", "status_item"
    ]].copy()

    edited_view = st.data_editor(
        editor_df,
        hide_index=True,
        use_container_width=True,
        disabled=["remessa", "doc_referencia", "material", "descricao", "qtd_solicitada", "status_item"],
        column_config={
            "remessa": "Remessa",
            "doc_referencia": "Documento referência",
            "material": "SKU",
            "descricao": "Descrição",
            "qtd_solicitada": st.column_config.NumberColumn("Qtd Solicitada"),
            "qtd_conferida": st.column_config.NumberColumn("Qtd Conferida", min_value=0, step=1),
            "status_item": "Status",
        },
        key=f"editor_{dt}",
    )

    items_df["qtd_conferida"] = edited_view["qtd_conferida"].fillna(0).astype(int)
    items_df = apply_statuses(items_df)
    update_snapshot_items(dt, items_df)

    ok_count = int((items_df["status_item"] == "OK").sum())
    div_count = int((items_df["status_item"] == "DIVERGENTE").sum())
    pen_count = int((items_df["status_item"] == "PENDENTE").sum())

    a1, a2, a3 = st.columns(3)
    a1.metric("Itens OK", ok_count)
    a2.metric("Itens divergentes", div_count)
    a3.metric("Itens pendentes", pen_count)

    has_divergence = div_count > 0

    b1, b2, b3 = st.columns(3)

    if b1.button("Gerar PDF", key=f"pdf_{dt}"):
        pdf_bytes = generate_pdf_bytes(get_dt_snapshot(dt))
        st.session_state["last_pdf_bytes"] = pdf_bytes
        st.session_state["last_pdf_name"] = f"espelho_dt_{dt}.pdf"
        st.success("PDF gerado.")

    if st.session_state.get("last_pdf_bytes") and st.session_state.get("last_pdf_name"):
        st.download_button(
            "Baixar último PDF",
            data=st.session_state["last_pdf_bytes"],
            file_name=st.session_state["last_pdf_name"],
            mime="application/pdf",
            key=f"download_pdf_{dt}",
        )

    if b2.button("Finalizar conferência", disabled=dt_locked(get_dt_snapshot(dt)), key=f"final_ok_{dt}"):
        if has_divergence:
            st.error("Existem divergências. Use a opção de finalizar com divergência.")
        else:
            finalize_dt(dt, "FINALIZADO", conferente, turno)
            pdf_bytes = generate_pdf_bytes(get_dt_snapshot(dt))
            file_name = f"espelho_dt_{dt}.pdf"
            st.session_state["last_pdf_bytes"] = pdf_bytes
            st.session_state["last_pdf_name"] = file_name

            try:
                send_pdf_email(pdf_bytes, file_name, dt, "FINALIZADO")
                st.success("Conferência finalizada e PDF enviado por e-mail.")
            except Exception as e:
                st.warning(f"Conferência finalizada, mas houve erro no envio do e-mail: {e}")

    if b3.button("Finalizar com divergência", disabled=dt_locked(get_dt_snapshot(dt)), key=f"final_div_{dt}"):
        finalize_dt(dt, "DIVERGENTE", conferente, turno)
        pdf_bytes = generate_pdf_bytes(get_dt_snapshot(dt))
        file_name = f"espelho_dt_{dt}.pdf"
        st.session_state["last_pdf_bytes"] = pdf_bytes
        st.session_state["last_pdf_name"] = file_name

        try:
            send_pdf_email(pdf_bytes, file_name, dt, "DIVERGENTE")
            st.warning("Conferência finalizada com divergência e PDF enviado por e-mail.")
        except Exception as e:
            st.warning(f"Conferência finalizada com divergência, mas houve erro no envio do e-mail: {e}")


def page_gestao():
    show_logo_main()
    st.title("Gestão")

    base = get_base_vl06_df()
    if base.empty:
        st.info("Primeiro carregue a VL06 na área do Assistente.")
        return

    mgmt = build_management_df()
    if mgmt.empty:
        st.warning("Sem dados.")
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total DTs", len(mgmt))
    c2.metric("Pendentes", int((mgmt["Status DT"] == "PENDENTE").sum()))
    c3.metric("Em andamento", int((mgmt["Status DT"] == "EM_ANDAMENTO").sum()))
    c4.metric("Finalizadas", int((mgmt["Status DT"] == "FINALIZADO").sum()))
    c5.metric("Divergentes", int((mgmt["Status DT"] == "DIVERGENTE").sum()))

    st.subheader("Dashboard")

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        status_counts = mgmt["Status DT"].value_counts().reset_index()
        status_counts.columns = ["Status", "Quantidade"]
        fig_status = px.pie(status_counts, names="Status", values="Quantidade", title="Status das DTs")
        st.plotly_chart(fig_status, use_container_width=True)

    with chart_col2:
        fig_tipo = px.bar(
            mgmt.groupby("Tipo de carga", dropna=False).size().reset_index(name="Quantidade"),
            x="Tipo de carga",
            y="Quantidade",
            title="DTs por Tipo de Carga"
        )
        st.plotly_chart(fig_tipo, use_container_width=True)

    chart_col3, chart_col4 = st.columns(2)

    with chart_col3:
        fig_conf = px.bar(
            mgmt.groupby("Conferente", dropna=False).size().reset_index(name="DTs"),
            x="Conferente",
            y="DTs",
            title="DTs por Conferente"
        )
        st.plotly_chart(fig_conf, use_container_width=True)

    with chart_col4:
        fig_caixas = px.bar(
            mgmt.sort_values("Total caixas", ascending=False).head(10),
            x="DT",
            y="Total caixas",
            title="Top 10 DTs por Total de Caixas"
        )
        st.plotly_chart(fig_caixas, use_container_width=True)

    st.subheader("Fila de DTs")

    filtro_status = st.selectbox("Filtrar por status", ["Todos", "PENDENTE", "EM_ANDAMENTO", "FINALIZADO", "DIVERGENTE"])
    show_df = mgmt.copy()
    if filtro_status != "Todos":
        show_df = show_df[show_df["Status DT"] == filtro_status]

    st.dataframe(show_df, use_container_width=True, hide_index=True)

    st.subheader("Reabrir DT divergente")
    dts_div = mgmt[mgmt["Status DT"] == "DIVERGENTE"]["DT"].tolist()
    if not dts_div:
        st.info("Não há DTs divergentes para reabrir.")
        return

    dt_reopen = st.selectbox("Selecione a DT divergente", dts_div)
    if st.button("Reabrir DT"):
        snapshot = get_dt_snapshot(dt_reopen)
        if dt_can_reopen(snapshot):
            reopen_dt(dt_reopen)
            st.success(f"DT {dt_reopen} reaberta com sucesso.")
            st.rerun()
        else:
            st.error("Só é permitido reabrir DT com status divergente.")


# =========================================================
# MAIN
# =========================================================
if not st.session_state.get("auth_ok"):
    login_screen()
    st.stop()

show_logo_sidebar()
st.sidebar.success(f"Usuário: {st.session_state['usuario']}")
st.sidebar.info(f"Perfil: {st.session_state['perfil']}")

if st.sidebar.button("Sair"):
    logout()

sections = allowed_sections()
section = st.sidebar.radio("Menu", sections)

if section == "Assistente":
    page_assistente()
elif section == "Conferência":
    page_conferencia()
elif section == "Gestão":
    page_gestao()
