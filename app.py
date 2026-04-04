import base64
import json
import os
from copy import deepcopy
from datetime import datetime
from io import BytesIO

import pandas as pd
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
    if pd.isna(value) or str(value).strip() == "":
        return ""
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        return clean_str(value)
    return dt.strftime("%d/%m/%Y")


def format_time_only(value):
    if pd.isna(value) or str(value).strip() == "":
        return ""
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        return clean_str(value)
    return dt.strftime("%H:%M")


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

    st.subheader("Refazer conferência de DT divergente")
    mgmt = build_management_df()
    dts_div = mgmt[mgmt["Status DT"] == "DIVERGENTE"]["DT"].tolist() if not mgmt.empty else []

    if dts_div:
        dt_refazer = st.selectbox("Selecione a DT divergente", dts_div, key="dt_refazer_assistente")
        if st.button("Refazer conferência", key="btn_refazer_assistente"):
            snapshot = get_dt_snapshot(dt_refazer)
            if dt_can_reopen(snapshot):
                reopen_dt(dt_refazer)
                st.success(f"DT {
