import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import cloudinary
import cloudinary.uploader
import pandas as pd
import plotly.express as px
import streamlit as st
from filelock import FileLock
from reportlab.lib import colors, utils
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


# =========================================================
# CONFIG
# =========================================================
st.set_page_config(page_title="Sistema de Conferência", layout="wide")

DATA_FILE = "data_store.json"
LOCK_FILE = "data_store.lock"
LOGO_FILE = "Nadir.png"
APP_TZ = ZoneInfo("America/Sao_Paulo")

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

cloudinary.config(
    cloud_name=st.secrets["cloudinary"]["cloud_name"],
    api_key=st.secrets["cloudinary"]["api_key"],
    api_secret=st.secrets["cloudinary"]["api_secret"],
    secure=True,
)


# =========================================================
# HELPERS UI
# =========================================================
def show_logo_main(width=260):
    if os.path.exists(LOGO_FILE):
        st.image(LOGO_FILE, width=width)


def show_logo_sidebar():
    if os.path.exists(LOGO_FILE):
        st.sidebar.image(LOGO_FILE, use_container_width=True)


def info_card(label, value):
    st.markdown(
        f"""
        <div style="
            background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
            border: 1px solid #dbeafe;
            border-radius: 14px;
            padding: 14px 16px;
            min-height: 95px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.04);
        ">
            <div style="font-size: 12px; color: #1d4ed8; font-weight: 600; margin-bottom: 6px;">
                {label}
            </div>
            <div style="font-size: 18px; color: #0f172a; font-weight: 700;">
                {value if value not in [None, ""] else "-"}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# TIME
# =========================================================
def now_sp():
    return datetime.now(APP_TZ)


def now_sp_str():
    return now_sp().strftime("%d/%m/%Y %H:%M:%S")


def now_sp_file():
    return now_sp().strftime("%Y%m%d_%H%M%S")


def parse_br_datetime(text):
    if not text:
        return None
    try:
        return datetime.strptime(str(text), "%d/%m/%Y %H:%M:%S").replace(tzinfo=APP_TZ)
    except Exception:
        return None


def calc_duration_minutes(inicio, fim):
    dt_ini = parse_br_datetime(inicio)
    dt_fim = parse_br_datetime(fim)
    if not dt_ini or not dt_fim:
        return 0
    seconds = (dt_fim - dt_ini).total_seconds()
    return max(int(seconds / 60), 0)


def format_duration_hhmm(total_minutes):
    total_minutes = int(total_minutes or 0)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


# =========================================================
# STORAGE JSON
# =========================================================
def default_store():
    return {
        "base_vl06": [],
        "sku_base": [],
        "conferencias": {},
        "insumos_cp": [],
        "boc_solicitacoes": [],
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
        show_logo_main(width=190)

    with col_title:
        st.title("Acesso ao Sistema")
        st.caption("Sistema de Conferência Operacional")

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
        return ["Assistente", "Conferência", "Insumos CP", "Solicitação BOC"]
    if perfil == "conferente":
        return ["Conferência", "Insumos CP", "Solicitação BOC"]
    if perfil == "gestao":
        return ["Gestão", "Conferência", "Insumos CP", "Solicitação BOC"]
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
        num = float(str(text).replace(",", "."))
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
    out["qtd_conferida"] = pd.to_numeric(out["qtd_conferida"], errors="coerce").fillna(0).astype(int)
    out["qtd_solicitada"] = pd.to_numeric(out["qtd_solicitada"], errors="coerce").fillna(0).astype(int)
    out["status_item"] = out.apply(
        lambda row: compute_item_status(row["qtd_conferida"], row["qtd_solicitada"]),
        axis=1,
    )
    return out


def get_remaining_qty(row):
    return max(int(row["qtd_solicitada"]) - int(row["qtd_conferida"]), 0)


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
    df = df.dropna(how="all").copy()

    header_row = None
    for idx in range(min(10, len(df))):
        row_vals = [clean_str(v) for v in df.iloc[idx].tolist()]
        if "SKU" in row_vals and "Quantidade por palete" in row_vals:
            header_row = idx
            break

    if header_row is None:
        raise ValueError("Não foi possível localizar o cabeçalho da base SKU.")

    df.columns = [clean_str(c) for c in df.iloc[header_row].tolist()]
    df = df.iloc[header_row + 1:].copy()
    df = df.reset_index(drop=True)

    if "SKU" not in df.columns or "Quantidade por palete" not in df.columns:
        raise ValueError("A base SKU precisa conter as colunas 'SKU' e 'Quantidade por palete'.")

    desc_col = "Descrição" if "Descrição" in df.columns else None

    out = pd.DataFrame({
        "sku": df["SKU"].apply(clean_id),
        "descricao": df[desc_col].apply(clean_str) if desc_col else "",
        "qtd_palete": df["Quantidade por palete"].apply(to_int_qty),
    })

    out = out[(out["sku"] != "") & (out["qtd_palete"] > 0)].copy()
    out = out.drop_duplicates(subset=["sku"]).reset_index(drop=True)

    if out.empty:
        raise ValueError("Nenhum SKU válido com quantidade por palete foi encontrado.")

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


def get_insumos_df():
    store = load_store()
    rows = store.get("insumos_cp", [])
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def save_insumos_df(df):
    store = load_store()
    store["insumos_cp"] = df.to_dict(orient="records")
    save_store(store)


def get_boc_df():
    store = load_store()
    rows = store.get("boc_solicitacoes", [])
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def save_boc_df(df):
    store = load_store()
    store["boc_solicitacoes"] = df.to_dict(orient="records")
    save_store(store)


def get_dt_list():
    base = get_base_vl06_df()
    if base.empty:
        return []
    return sorted(base["dt"].astype(str).unique().tolist())


def get_dt_snapshot(dt):
    confs = get_conferencias()
    if dt in confs:
        snapshot = confs[dt]
        meta = snapshot.setdefault("meta", {})
        meta.setdefault("pdf_url", "")
        meta.setdefault("pdf_public_id", "")
        meta.setdefault("assinatura_conferente", "")
        meta.setdefault("assinatura_lider", "")
        return snapshot

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
            "pdf_url": "",
            "pdf_public_id": "",
            "assinatura_conferente": "",
            "assinatura_lider": "",
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
    snapshot["meta"]["total_caixas"] = int(pd.to_numeric(df_items["qtd_solicitada"], errors="coerce").fillna(0).sum())

    if "metragem_cubica" in df_items.columns:
        snapshot["meta"]["metragem_cubica"] = float(
            pd.to_numeric(df_items["metragem_cubica"], errors="coerce").fillna(0).sum()
        )

    save_dt_snapshot(dt, snapshot)


def dt_locked(snapshot):
    return snapshot["meta"].get("status_dt") == "FINALIZADO"


def dt_can_reopen(snapshot):
    return snapshot["meta"].get("status_dt") == "DIVERGENTE"


def mark_dt_started(dt, conferente, turno):
    snapshot = deepcopy(get_dt_snapshot(dt))
    if not snapshot["meta"].get("inicio"):
        snapshot["meta"]["inicio"] = now_sp_str()
    snapshot["meta"]["conferente"] = conferente
    snapshot["meta"]["turno"] = turno
    if snapshot["meta"]["status_dt"] == "PENDENTE":
        snapshot["meta"]["status_dt"] = "EM_ANDAMENTO"
    save_dt_snapshot(dt, snapshot)


def finalize_dt(dt, final_status, conferente, turno, assinatura_conferente="", assinatura_lider=""):
    snapshot = deepcopy(get_dt_snapshot(dt))
    snapshot["meta"]["conferente"] = conferente
    snapshot["meta"]["turno"] = turno
    snapshot["meta"]["assinatura_conferente"] = assinatura_conferente
    snapshot["meta"]["assinatura_lider"] = assinatura_lider

    if not snapshot["meta"].get("inicio"):
        snapshot["meta"]["inicio"] = now_sp_str()

    snapshot["meta"]["fim"] = now_sp_str()
    snapshot["meta"]["status_dt"] = final_status
    save_dt_snapshot(dt, snapshot)


def reset_dt_conferencia(dt):
    snapshot = deepcopy(get_dt_snapshot(dt))
    items_df = snapshot_to_df(snapshot)

    if items_df.empty:
        return

    items_df["qtd_conferida"] = 0
    items_df["status_item"] = "PENDENTE"

    snapshot["items"] = items_df.to_dict(orient="records")
    snapshot["meta"]["inicio"] = ""
    snapshot["meta"]["fim"] = ""
    snapshot["meta"]["status_dt"] = "PENDENTE"
    snapshot["meta"]["conferente"] = ""
    snapshot["meta"]["turno"] = "Manhã"
    snapshot["meta"]["pdf_url"] = ""
    snapshot["meta"]["pdf_public_id"] = ""
    snapshot["meta"]["assinatura_conferente"] = ""
    snapshot["meta"]["assinatura_lider"] = ""

    save_dt_snapshot(dt, snapshot)


def reopen_dt(dt):
    snapshot = deepcopy(get_dt_snapshot(dt))
    if snapshot["meta"]["status_dt"] == "DIVERGENTE":
        reset_dt_conferencia(dt)


# =========================================================
# LANÇAMENTO HO / HE
# =========================================================
def lancar_quantidade_sku(df_items, sku, quantidade):
    if quantidade <= 0:
        return df_items, False, "Quantidade deve ser maior que zero."

    df = df_items.copy()
    df["material"] = df["material"].astype(str)

    sku = str(sku).strip()
    match_idx = df.index[df["material"] == sku].tolist()

    if not match_idx:
        return df_items, False, "SKU não encontrado nesta DT."

    restante_para_lancar = int(quantidade)

    for idx in match_idx:
        qtd_solicitada = int(df.at[idx, "qtd_solicitada"])
        qtd_conferida = int(df.at[idx, "qtd_conferida"])
        saldo = max(qtd_solicitada - qtd_conferida, 0)

        if saldo <= 0:
            continue

        adicionar = min(saldo, restante_para_lancar)
        df.at[idx, "qtd_conferida"] = qtd_conferida + adicionar
        restante_para_lancar -= adicionar

        if restante_para_lancar <= 0:
            break

    if restante_para_lancar > 0:
        ultimo_idx = match_idx[-1]
        df.at[ultimo_idx, "qtd_conferida"] = int(df.at[ultimo_idx, "qtd_conferida"]) + restante_para_lancar

    df = apply_statuses(df)
    return df, True, ""


# =========================================================
# CLOUDINARY
# =========================================================
def upload_pdf_to_cloudinary(pdf_bytes, filename, folder="espelhos_conferencia"):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        temp_path = tmp.name

    try:
        result = cloudinary.uploader.upload(
            temp_path,
            resource_type="raw",
            folder=folder,
            public_id=filename.replace(".pdf", ""),
            overwrite=True,
        )
        return {
            "url": result.get("secure_url", ""),
            "public_id": result.get("public_id", ""),
        }
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# =========================================================
# PDF
# =========================================================
def get_image_dimensions(path, width=None, height=None):
    img = utils.ImageReader(path)
    iw, ih = img.getSize()
    if width and not height:
        aspect = ih / float(iw)
        height = width * aspect
    elif height and not width:
        aspect = iw / float(ih)
        width = height * aspect
    return width, height


def generate_pdf_bytes(snapshot):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=10 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PdfTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1D39C4"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "PdfSubtitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=9,
        textColor=colors.HexColor("#6B7280"),
        spaceAfter=10,
    )
    normal_style = ParagraphStyle(
        "PdfNormal",
        parent=styles["Normal"],
        alignment=TA_LEFT,
        fontSize=9,
        leading=11,
    )
    signature_title_style = ParagraphStyle(
        "SignatureTitle",
        parent=styles["Normal"],
        alignment=TA_LEFT,
        fontSize=10,
        leading=12,
        textColor=colors.HexColor("#0F172A"),
    )

    story = []
    meta = snapshot["meta"]
    items_df = snapshot_to_df(snapshot).copy().sort_values(by=["remessa", "material"]).reset_index(drop=True)

    if os.path.exists(LOGO_FILE):
        try:
            w, h = get_image_dimensions(LOGO_FILE, width=62 * mm)
            logo = Image(LOGO_FILE, width=w, height=h)
            logo.hAlign = "LEFT"
            story.append(logo)
            story.append(Spacer(1, 4))
        except Exception:
            pass

    story.append(Paragraph("ESPELHO DE CONFERÊNCIA", title_style))
    story.append(Paragraph("Documento operacional de conferência logística", subtitle_style))

    duracao_min = calc_duration_minutes(meta.get("inicio", ""), meta.get("fim", ""))

    info_table = Table([
        ["DT", meta.get("dt", ""), "Status", meta.get("status_dt", "")],
        ["Cliente", meta.get("cliente", ""), "Transportadora", meta.get("transportadora", "")],
        ["Conferente", meta.get("conferente", ""), "Turno", meta.get("turno", "")],
        ["Início", meta.get("inicio", ""), "Fim", meta.get("fim", "")],
        ["Data agenda", meta.get("data_agenda", ""), "Hora agenda", meta.get("hora_agenda", "")],
        ["Perfil de carregamento", meta.get("perfil_carregamento", ""), "Tipo de carga", meta.get("tipo_carga", "")],
        ["Quantidade de remessas", str(meta.get("qtd_remessas", 0)), "Total de caixas", str(meta.get("total_caixas", 0))],
        ["Metragem cúbica", f"{meta.get('metragem_cubica', 0):.3f} m³", "Duração", f"{duracao_min} min"],
    ], colWidths=[42 * mm, 92 * mm, 38 * mm, 95 * mm])

    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#D1D5DB")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EFF4FF")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#EFF4FF")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#111827")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 10))

    table_data = [[
        "Remessa", "Doc. Ref.", "Material", "Descrição",
        "Qtd Sol.", "Qtd Conf.", "Status"
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

    table = Table(
        table_data,
        repeatRows=1,
        colWidths=[26 * mm, 30 * mm, 28 * mm, 104 * mm, 22 * mm, 22 * mm, 24 * mm]
    )

    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1D39C4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9CA3AF")),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]

    for i, row in enumerate(table_data[1:], start=1):
        status = row[6]
        if i % 2 == 0:
            style_commands.append(("BACKGROUND", (0, i), (5, i), colors.HexColor("#FAFAFA")))

        if status == "OK":
            style_commands.append(("BACKGROUND", (6, i), (6, i), colors.HexColor("#DCFCE7")))
        elif status == "DIVERGENTE":
            style_commands.append(("BACKGROUND", (6, i), (6, i), colors.HexColor("#FEE2E2")))
        elif status == "PENDENTE":
            style_commands.append(("BACKGROUND", (6, i), (6, i), colors.HexColor("#FEF3C7")))

    table.setStyle(TableStyle(style_commands))
    story.append(table)
    story.append(Spacer(1, 14))

    assinatura_conferente = meta.get("assinatura_conferente", "") or meta.get("conferente", "")
    assinatura_lider = meta.get("assinatura_lider", "")

    signature_table = Table([
        ["", ""],
        [
            f"________________________________________<br/>{assinatura_conferente}<br/>Assinatura do Conferente",
            f"________________________________________<br/>{assinatura_lider}<br/>Assinatura da Liderança / Responsável",
        ],
    ], colWidths=[125 * mm, 125 * mm])

    signature_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("FONTSIZE", (0, 1), (-1, 1), 9),
    ]))

    story.append(Paragraph("Assinaturas", signature_title_style))
    story.append(Spacer(1, 6))
    story.append(signature_table)
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Emitido em: {now_sp_str()}", normal_style))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


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

        inicio = meta.get("inicio", "")
        fim = meta.get("fim", "")
        duracao_min = calc_duration_minutes(inicio, fim)
        total_caixas = int(items_df["qtd_solicitada"].fillna(0).sum()) if not items_df.empty else 0

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
            "Início": inicio,
            "Fim": fim,
            "Duração min": duracao_min,
            "Duração HH:MM": format_duration_hhmm(duracao_min),
            "Status DT": meta.get("status_dt", "PENDENTE"),
            "Itens": len(items_df),
            "SKU únicos": items_df["material"].astype(str).nunique() if not items_df.empty else 0,
            "Total caixas": total_caixas,
            "M³": round(float(items_df["metragem_cubica"].fillna(0).sum()), 3) if "metragem_cubica" in items_df.columns else 0,
            "OK": int((items_df["status_item"] == "OK").sum()) if not items_df.empty else 0,
            "Divergentes": int((items_df["status_item"] == "DIVERGENTE").sum()) if not items_df.empty else 0,
            "Pendentes": int((items_df["status_item"] == "PENDENTE").sum()) if not items_df.empty else 0,
            "PDF URL": meta.get("pdf_url", ""),
        })

    df = pd.DataFrame(rows)
    df["Caixas por min"] = df.apply(
        lambda row: round(row["Total caixas"] / row["Duração min"], 2) if row["Duração min"] > 0 else 0,
        axis=1,
    )
    return df


def build_conferente_ranking(mgmt):
    if mgmt.empty:
        return pd.DataFrame()

    df = mgmt.copy()
    df = df[df["Conferente"].astype(str).str.strip() != ""].copy()

    if df.empty:
        return pd.DataFrame()

    ranking = df.groupby("Conferente", dropna=False).agg(
        DTs=("DT", "count"),
        Total_Caixas=("Total caixas", "sum"),
        Total_Divergencias=("Divergentes", "sum"),
        Tempo_Total_Min=("Duração min", "sum"),
        DTs_Finalizadas=("Status DT", lambda s: int((s == "FINALIZADO").sum())),
        DTs_Divergentes=("Status DT", lambda s: int((s == "DIVERGENTE").sum())),
    ).reset_index()

    ranking["Produtividade"] = ranking.apply(
        lambda row: round(row["Total_Caixas"] / row["Tempo_Total_Min"], 2) if row["Tempo_Total_Min"] > 0 else 0,
        axis=1,
    )
    ranking["Tempo Total"] = ranking["Tempo_Total_Min"].apply(format_duration_hhmm)

    ranking = ranking.sort_values(["Produtividade", "DTs"], ascending=[False, False]).reset_index(drop=True)
    ranking.index = ranking.index + 1
    ranking["Posição"] = ranking.index
    return ranking


def estimate_minutes_by_history(mgmt, total_caixas, metragem_cubica, tipo_carga):
    if mgmt.empty:
        return 0

    df = mgmt.copy()
    df = df[df["Duração min"] > 0].copy()
    if df.empty:
        return 0

    same_type = df[df["Tipo de carga"] == tipo_carga].copy()
    base_ref = same_type if not same_type.empty else df

    total_duration = base_ref["Duração min"].sum()
    total_boxes = base_ref["Total caixas"].sum()

    if total_duration <= 0 or total_boxes <= 0:
        return 0

    media_caixas_min = total_boxes / total_duration
    if media_caixas_min <= 0:
        return 0

    estimado = int(round(total_caixas / media_caixas_min))
    if metragem_cubica and metragem_cubica > 0:
        estimado += int(round(metragem_cubica * 1.5))

    return max(estimado, 1)


# =========================================================
# PÁGINAS
# =========================================================
def page_assistente():
    show_logo_main()
    st.title("Assistente de Logística")
    st.caption("Carga de bases, manutenção e reabertura de conferências divergentes")

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
                raw = pd.read_csv(sku_file) if sku_file.name.lower().endswith(".csv") else pd.read_excel(sku_file, header=None)
                normalized = normalize_sku_base(raw)

                if st.button("Processar base SKU", key="process_sku"):
                    save_sku_df(normalized)
                    st.success(f"Base SKU carregada com {len(normalized)} itens.")
                    st.dataframe(normalized.head(20), use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f"Erro ao processar base SKU: {e}")

    st.divider()

    base = get_base_vl06_df()
    if not base.empty:
        a, b, c = st.columns(3)
        a.metric("Linhas válidas", len(base))
        b.metric("DTs", base["dt"].nunique())
        c.metric("Remessas", base["remessa"].nunique())
        st.dataframe(base.head(50), use_container_width=True, hide_index=True)

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
                    st.success(f"DT {dt_refazer} reaberta com conferência zerada. Pronta para iniciar do zero.")
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

    mgmt_hist = build_management_df()
    tempo_estimado = estimate_minutes_by_history(
        mgmt_hist,
        total_caixas=total_caixas,
        metragem_cubica=metragem_cubica,
        tipo_carga=tipo_carga,
    )

    row1 = st.columns(4)
    with row1[0]:
        info_card("Cliente", meta.get("cliente", ""))
    with row1[1]:
        info_card("Transportadora", meta.get("transportadora", ""))
    with row1[2]:
        info_card("Status DT", meta.get("status_dt", "PENDENTE"))
    with row1[3]:
        info_card("Remessas na DT", meta.get("qtd_remessas", 0))

    row2 = st.columns(4)
    with row2[0]:
        info_card("SKU únicos", qtd_skus_unicos)
    with row2[1]:
        info_card("Total de caixas", total_caixas)
    with row2[2]:
        info_card("Data agenda", data_agenda)
    with row2[3]:
        info_card("Hora agenda", hora_agenda)

    row3 = st.columns(4)
    with row3[0]:
        info_card("Perfil de carregamento", perfil_carregamento)
    with row3[1]:
        info_card("Tipo de carga", tipo_carga)
    with row3[2]:
        info_card("Metragem cúbica", f"{metragem_cubica:.3f} m³")
    with row3[3]:
        info_card("Previsão de conferência", f"{tempo_estimado} min")

    st.divider()

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

    s1, s2 = st.columns(2)
    assinatura_conferente = s1.text_input(
        "Nome para assinatura do conferente",
        value=meta.get("assinatura_conferente", meta.get("conferente", "")),
        key=f"assinatura_conf_{dt}",
    )
    assinatura_lider = s2.text_input(
        "Nome para assinatura da liderança / responsável",
        value=meta.get("assinatura_lider", ""),
        key=f"assinatura_lider_{dt}",
    )

    snapshot["meta"]["conferente"] = conferente
    snapshot["meta"]["turno"] = turno
    snapshot["meta"]["assinatura_conferente"] = assinatura_conferente
    snapshot["meta"]["assinatura_lider"] = assinatura_lider
    save_dt_snapshot(dt, snapshot)

    if dt_locked(snapshot):
        st.error("Esta DT foi finalizada sem divergência e está bloqueada.")
    elif meta.get("status_dt") == "DIVERGENTE":
        st.warning("Esta DT foi finalizada com divergência e pode ser reaberta pelo Assistente ou Gestão.")
    else:
        mark_dt_started(dt, conferente, turno)

    sku_df = get_sku_df()
    if sku_df.empty:
        st.warning("Carregue a base SKU para habilitar o lançamento por HO e HE.")
    else:
        sku_map = dict(zip(sku_df["sku"].astype(str), sku_df["qtd_palete"].astype(int)))
        desc_map = dict(zip(sku_df["sku"].astype(str), sku_df["descricao"].astype(str)))

        st.subheader("Lançamento de Conferência")
        l1, l2, l3, l4 = st.columns([2, 1, 1, 1])

        sku_lanc = l1.text_input("SKU", key=f"sku_lanc_{dt}")
        ho_paletes = l2.number_input("HO (qtde de paletes)", min_value=0, step=1, key=f"ho_{dt}")
        he_qtd = l3.number_input("HE (qtde fracionada)", min_value=0, step=1, key=f"he_{dt}")

        if l4.button("Lançar", disabled=dt_locked(get_dt_snapshot(dt)), key=f"btn_lancar_ho_he_{dt}"):
            sku_digitado = clean_id(sku_lanc)

            if not sku_digitado:
                st.warning("Informe o SKU.")
            elif sku_digitado not in sku_map:
                st.error("SKU não cadastrado na base SKU.")
            else:
                qtd_por_palete = int(sku_map[sku_digitado])
                qtd_ho = int(ho_paletes) * qtd_por_palete
                qtd_he = int(he_qtd)
                qtd_total = qtd_ho + qtd_he

                if qtd_total <= 0:
                    st.warning("Informe HO e/ou HE para lançar.")
                else:
                    novo_df, ok, msg = lancar_quantidade_sku(items_df, sku_digitado, qtd_total)
                    if not ok:
                        st.error(msg)
                    else:
                        update_snapshot_items(dt, novo_df)
                        desc = desc_map.get(sku_digitado, "")
                        st.success(
                            f"Lançamento realizado para SKU {sku_digitado} - {desc}. "
                            f"HO: {ho_paletes} palete(s) = {qtd_ho} caixas | "
                            f"HE: {qtd_he} caixa(s) | "
                            f"Total lançado: {qtd_total}."
                        )
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

    st.divider()

    b1, b2, b3 = st.columns(3)

    if b1.button("Gerar PDF", key=f"pdf_{dt}"):
        snapshot_preview = get_dt_snapshot(dt)
        snapshot_preview["meta"]["assinatura_conferente"] = assinatura_conferente
        snapshot_preview["meta"]["assinatura_lider"] = assinatura_lider
        pdf_bytes = generate_pdf_bytes(snapshot_preview)
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

    snapshot_atual = get_dt_snapshot(dt)
    pdf_url = snapshot_atual["meta"].get("pdf_url", "")
    if pdf_url:
        st.link_button("Abrir PDF salvo online", pdf_url, use_container_width=True)

    if b2.button("Finalizar conferência", disabled=dt_locked(get_dt_snapshot(dt)), key=f"final_ok_{dt}"):
        if has_divergence:
            st.error("Existem divergências. Use a opção de finalizar com divergência.")
        else:
            finalize_dt(
                dt,
                "FINALIZADO",
                conferente,
                turno,
                assinatura_conferente=assinatura_conferente,
                assinatura_lider=assinatura_lider,
            )

            snapshot_final = get_dt_snapshot(dt)
            pdf_bytes = generate_pdf_bytes(snapshot_final)
            file_name = f"espelho_dt_{dt}_{now_sp_file()}.pdf"

            st.session_state["last_pdf_bytes"] = pdf_bytes
            st.session_state["last_pdf_name"] = file_name

            try:
                cloud_result = upload_pdf_to_cloudinary(pdf_bytes, file_name)
                snapshot_final["meta"]["pdf_url"] = cloud_result["url"]
                snapshot_final["meta"]["pdf_public_id"] = cloud_result["public_id"]
                save_dt_snapshot(dt, snapshot_final)

                st.success("Conferência finalizada e PDF salvo online com sucesso.")
                st.rerun()
            except Exception as e:
                st.warning(f"Conferência finalizada, mas houve erro ao salvar o PDF online: {e}")

    if b3.button("Finalizar com divergência", disabled=dt_locked(get_dt_snapshot(dt)), key=f"final_div_{dt}"):
        finalize_dt(
            dt,
            "DIVERGENTE",
            conferente,
            turno,
            assinatura_conferente=assinatura_conferente,
            assinatura_lider=assinatura_lider,
        )

        snapshot_final = get_dt_snapshot(dt)
        pdf_bytes = generate_pdf_bytes(snapshot_final)
        file_name = f"espelho_dt_{dt}_{now_sp_file()}.pdf"

        st.session_state["last_pdf_bytes"] = pdf_bytes
        st.session_state["last_pdf_name"] = file_name

        try:
            cloud_result = upload_pdf_to_cloudinary(pdf_bytes, file_name)
            snapshot_final["meta"]["pdf_url"] = cloud_result["url"]
            snapshot_final["meta"]["pdf_public_id"] = cloud_result["public_id"]
            save_dt_snapshot(dt, snapshot_final)

            st.warning("Conferência finalizada com divergência e PDF salvo online.")
            st.rerun()
        except Exception as e:
            st.warning(f"Conferência finalizada com divergência, mas houve erro ao salvar o PDF online: {e}")


def page_insumos_cp():
    show_logo_main()
    st.title("Lançamento de Insumos CP")
    st.caption("Utilize esta opção para cargas paletizadas (CP)")

    base = get_base_vl06_df()
    if base.empty:
        st.info("Primeiro carregue a VL06 na área do Assistente.")
        return

    dt_list = get_dt_list()
    dt = st.selectbox("Selecione a DT", dt_list, key="dt_insumos_cp")

    snapshot = get_dt_snapshot(dt)
    tipo_carga = snapshot["meta"].get("tipo_carga", "")

    st.info(f"Tipo de carga da DT: {tipo_carga if tipo_carga else '-'}")

    col1, col2, col3, col4 = st.columns(4)
    palete = col1.number_input("Palete", min_value=0, step=1, key="insumo_palete")
    chapa = col2.number_input("Chapa", min_value=0, step=1, key="insumo_chapa")
    quadro_sem_ripa = col3.number_input("Quadro sem ripa", min_value=0, step=1, key="insumo_qsr")
    quadro_com_ripa = col4.number_input("Quadro com ripa", min_value=0, step=1, key="insumo_qcr")

    if st.button("Salvar insumos CP", use_container_width=True):
        novo = {
            "data_hora": now_sp_str(),
            "usuario": st.session_state.get("usuario", ""),
            "dt": dt,
            "tipo_carga": tipo_carga,
            "palete": int(palete),
            "chapa": int(chapa),
            "quadro_sem_ripa": int(quadro_sem_ripa),
            "quadro_com_ripa": int(quadro_com_ripa),
        }

        hist = get_insumos_df()
        hist = pd.concat([hist, pd.DataFrame([novo])], ignore_index=True)
        save_insumos_df(hist)
        st.success("Insumos CP lançados com sucesso.")

    hist = get_insumos_df()
    if not hist.empty:
        st.subheader("Histórico de insumos")
        show = hist[hist["dt"].astype(str) == str(dt)].copy()
        if show.empty:
            st.info("Sem lançamentos para esta DT.")
        else:
            st.dataframe(show.sort_values("data_hora", ascending=False), use_container_width=True, hide_index=True)


def page_boc():
    show_logo_main()
    st.title("Solicitação de BOC")
    st.caption("Utilize quando um item não for carregado na totalidade por falta de caixas")

    base = get_base_vl06_df()
    if base.empty:
        st.info("Primeiro carregue a VL06 na área do Assistente.")
        return

    dt_list = get_dt_list()
    dt = st.selectbox("Selecione a DT", dt_list, key="dt_boc")

    snapshot = get_dt_snapshot(dt)
    items_df = snapshot_to_df(snapshot).copy()

    if items_df.empty:
        st.warning("Sem itens nesta DT.")
        return

    remessas = sorted(items_df["remessa"].astype(str).unique().tolist())
    remessa = st.selectbox("Remessa", remessas, key="boc_remessa")

    itens_remessa = items_df[items_df["remessa"].astype(str) == str(remessa)].copy()
    itens_remessa["item_label"] = (
        itens_remessa["material"].astype(str) + " - " + itens_remessa["descricao"].astype(str)
    )

    item_label = st.selectbox("Item", itens_remessa["item_label"].tolist(), key="boc_item")
    qtd = st.number_input("Qtd", min_value=1, step=1, key="boc_qtd")

    if st.button("Salvar solicitação BOC", use_container_width=True):
        sku_sel = item_label.split(" - ")[0].strip()
        desc_sel = " - ".join(item_label.split(" - ")[1:]).strip()

        novo = {
            "data_hora": now_sp_str(),
            "usuario": st.session_state.get("usuario", ""),
            "dt": dt,
            "remessa": remessa,
            "item": sku_sel,
            "descricao": desc_sel,
            "qtd": int(qtd),
        }

        hist = get_boc_df()
        hist = pd.concat([hist, pd.DataFrame([novo])], ignore_index=True)
        save_boc_df(hist)
        st.success("Solicitação de BOC registrada com sucesso.")

    hist = get_boc_df()
    if not hist.empty:
        st.subheader("Histórico de solicitações BOC")
        show = hist[hist["dt"].astype(str) == str(dt)].copy()
        if show.empty:
            st.info("Sem solicitações para esta DT.")
        else:
            st.dataframe(show.sort_values("data_hora", ascending=False), use_container_width=True, hide_index=True)


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
        conf_df = mgmt.copy()
        conf_df["Conferente"] = conf_df["Conferente"].replace("", "Sem conferente")
        fig_conf = px.bar(
            conf_df.groupby("Conferente", dropna=False).size().reset_index(name="DTs"),
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

    st.subheader("Ranking de Conferentes")
    ranking = build_conferente_ranking(mgmt)

    if not ranking.empty:
        st.dataframe(
            ranking[[
                "Posição", "Conferente", "DTs", "DTs_Finalizadas",
                "DTs_Divergentes", "Total_Caixas", "Tempo Total",
                "Produtividade", "Total_Divergencias"
            ]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Ainda não há dados suficientes para ranking.")

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
            st.success(f"DT {dt_reopen} reaberta e zerada com sucesso.")
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

if st.sidebar.button("Sair", use_container_width=True):
    logout()

sections = allowed_sections()
section = st.sidebar.radio("Menu", sections)

if section == "Assistente":
    page_assistente()
elif section == "Conferência":
    page_conferencia()
elif section == "Insumos CP":
    page_insumos_cp()
elif section == "Solicitação BOC":
    page_boc()
elif section == "Gestão":
    page_gestao()
