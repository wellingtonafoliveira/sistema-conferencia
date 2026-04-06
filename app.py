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
import qrcode
import streamlit as st
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# =========================================================
# CONFIG
# =========================================================
st.set_page_config(page_title="Sistema de Conferência", layout="wide")

APP_TZ = ZoneInfo("America/Sao_Paulo")
DB_FILE = "db.json"
VL06_FILE = "vl06.csv"
SKU_FILE = "sku.csv"
LOGO_FILE = "Nadir.png"

cloudinary.config(
    cloud_name=st.secrets["cloudinary"]["cloud_name"],
    api_key=st.secrets["cloudinary"]["api_key"],
    api_secret=st.secrets["cloudinary"]["api_secret"],
    secure=True,
)

# =========================================================
# DB LOCAL
# =========================================================
def default_db():
    return {
        "dts": {},
        "boc": {},
        "insumos": {},
    }


def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return default_db()


def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


db = load_db()

# =========================================================
# HELPERS
# =========================================================
def now_sp():
    return datetime.now(APP_TZ)


def now_sp_str():
    return now_sp().strftime("%d/%m/%Y %H:%M:%S")


def now_sp_date():
    return now_sp().strftime("%d/%m/%Y")


def now_sp_time():
    return now_sp().strftime("%H:%M:%S")


def safe_int(value):
    try:
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return 0


def safe_float(value):
    try:
        return float(str(value).replace(".", "").replace(",", ".")) if isinstance(value, str) and "," in value and "." in value else float(str(value).replace(",", "."))
    except Exception:
        return 0.0


def format_date_only(value):
    if pd.isna(value):
        return ""
    try:
        return pd.to_datetime(value).strftime("%d/%m/%Y")
    except Exception:
        return str(value)


def format_time_only(value):
    if pd.isna(value):
        return ""
    try:
        return pd.to_datetime(value).strftime("%H:%M:%S")
    except Exception:
        return str(value)


def show_logo_sidebar():
    if os.path.exists(LOGO_FILE):
        st.sidebar.image(LOGO_FILE, use_container_width=True)


def show_logo_main():
    if os.path.exists(LOGO_FILE):
        st.image(LOGO_FILE, width=190)


def get_users():
    return st.secrets["users"]


def allowed_sections():
    perfil = st.session_state.get("perfil", "")
    mapping = {
        "assistente": ["Assistente", "Conferência", "Insumos", "Reabrir DT"],
        "conferente": ["Conferência", "Insumos"],
        "gestao": ["Gestão"],
        "faturista": ["Faturamento"],
        "coletor": ["Coletor", "Insumos"],
    }
    return mapping.get(perfil, [])


def login_screen():
    show_logo_main()
    st.title("Acesso ao Sistema")

    usuario = st.text_input("Usuário")
    senha = st.text_input("Senha", type="password")

    if st.button("Entrar", use_container_width=True):
        users = get_users()
        if usuario in users:
            senha_ok, perfil = users[usuario].split("|")
            if senha == senha_ok:
                st.session_state["auth_ok"] = True
                st.session_state["usuario"] = usuario
                st.session_state["perfil"] = perfil
                st.rerun()

        st.error("Usuário ou senha inválidos.")


def logout():
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()


def set_current_dt(dt):
    st.session_state["current_dt"] = dt


def get_current_dt():
    return st.session_state.get("current_dt", "")


def infer_tipo_carga(df_dt):
    if "Tipo de carga" in df_dt.columns:
        val = str(df_dt["Tipo de carga"].dropna().iloc[0]).upper() if not df_dt["Tipo de carga"].dropna().empty else ""
        if val in ["CB", "CP", "CM"]:
            return val

    if "Perfil de carregamento" in df_dt.columns:
        perfil = str(df_dt["Perfil de carregamento"].dropna().iloc[0]).upper() if not df_dt["Perfil de carregamento"].dropna().empty else ""
        if "CP" in perfil:
            return "CP"
        if "CM" in perfil:
            return "CM"
        if "CB" in perfil:
            return "CB"

    return "CB"


def produtividade_por_tipo(tipo):
    mapa = {
        "CB": 30,  # caixas/min
        "CP": 80,
        "CM": 50,
    }
    return mapa.get(tipo, 40)


def status_item(sol, conf):
    sol = safe_int(sol)
    conf = safe_int(conf)

    if conf == 0:
        return "PENDENTE"
    if conf == sol:
        return "OK"
    return "DIVERGENTE"


def dt_status_from_items(items):
    statuses = [status_item(v["sol"], v["conf"]) for v in items.values()]

    if all(s == "OK" for s in statuses) and statuses:
        return "FINALIZADO"
    if any(s == "DIVERGENTE" for s in statuses) or any(s == "PENDENTE" for s in statuses):
        return "DIVERGENTE"
    return "ABERTO"


def metric_card(label, value):
    st.markdown(
        f"""
        <div style="
            background:#ffffff;
            border-radius:16px;
            padding:18px 18px 16px 18px;
            border:1px solid #e5e7eb;
            box-shadow:0 2px 8px rgba(0,0,0,0.06);
            min-height:110px;
            margin-bottom:10px;
        ">
            <div style="font-size:12px;color:#6b7280;margin-bottom:8px;">{label}</div>
            <div style="font-size:18px;font-weight:700;color:#111827;line-height:1.35;">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def upload_pdf_cloudinary(pdf_bytes, public_id):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        temp_path = tmp.name

    try:
        result = cloudinary.uploader.upload(
            temp_path,
            resource_type="raw",
            public_id=public_id,
            overwrite=True,
        )
        return result["secure_url"]
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def get_dt_conf(dt):
    if dt not in db["dts"]:
        db["dts"][dt] = {
            "itens": {},
            "status": "ABERTO",
            "pdf": "",
            "conferente": "",
            "turno": "T1",
            "inicio_data": "",
            "inicio_hora": "",
            "fim_data": "",
            "fim_hora": "",
            "tipo": "",
        }
    return db["dts"][dt]


def get_dt_list():
    if not os.path.exists(VL06_FILE):
        return []
    base = pd.read_csv(VL06_FILE)
    if "Nº transporte" not in base.columns:
        return []
    return sorted(base["Nº transporte"].dropna().astype(str).unique().tolist())

# =========================================================
# PDF
# =========================================================
def build_pdf_bytes(dt, df_items, insumos, boc, meta, qr_img_path=None):
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "PdfTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1636C9"),
        spaceAfter=2,
    )

    subtitle_style = ParagraphStyle(
        "PdfSubTitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=10,
        textColor=colors.HexColor("#6b7280"),
        spaceAfter=10,
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )

    story = []

    if os.path.exists(LOGO_FILE):
        story.append(RLImage(LOGO_FILE, width=46 * mm, height=18 * mm))

    story.append(Paragraph("ESPELHO DE CONFERÊNCIA", title_style))
    story.append(Paragraph("Documento operacional de conferência logística", subtitle_style))
    story.append(Spacer(1, 6))

    info_rows = [
        ["DT", dt, "Status", meta.get("status", "")],
        ["Cliente", meta.get("cliente", ""), "Transportadora", meta.get("transportadora", "")],
        ["Conferente", meta.get("conferente", ""), "Turno", meta.get("turno", "")],
        ["Início", f"{meta.get('inicio_data', '')} {meta.get('inicio_hora', '')}".strip(), "Fim", f"{meta.get('fim_data', '')} {meta.get('fim_hora', '')}".strip()],
        ["Data agenda", meta.get("data_agenda", ""), "Hora agenda", meta.get("hora_agenda", "")],
        ["Perfil de carregamento", meta.get("perfil", ""), "Tipo de carga", meta.get("tipo", "")],
        ["Quantidade de remessas", str(meta.get("remessas", "")), "Total de caixas", str(meta.get("caixas", ""))],
        ["Metragem cúbica", str(meta.get("m3", "")), "Duração", meta.get("duracao", "")],
    ]

    info_table = Table(info_rows, colWidths=[42 * mm, 100 * mm, 42 * mm, 104 * mm])
    info_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#c9d2de")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 10))

    item_rows = [["Remessa", "Material", "Descrição", "Qtd Sol.", "Qtd Conf.", "Status"]]
    for _, row in df_items.iterrows():
        item_rows.append([
            str(row["remessa"]),
            str(row["sku"]),
            str(row["descricao"]),
            str(row["Qtd. Solicitada"]),
            str(row["Qtd. Conferida"]),
            str(row["Status"]),
        ])

    item_table = Table(
        item_rows,
        colWidths=[28 * mm, 36 * mm, 110 * mm, 24 * mm, 24 * mm, 28 * mm],
        repeatRows=1,
    )

    item_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1026D6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#c9d2de")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ])

    for i in range(1, len(item_rows)):
        if item_rows[i][5] == "OK":
            item_style.add("BACKGROUND", (5, i), (5, i), colors.HexColor("#d9f2df"))
        elif item_rows[i][5] == "DIVERGENTE":
            item_style.add("BACKGROUND", (5, i), (5, i), colors.HexColor("#f8d7da"))
        else:
            item_style.add("BACKGROUND", (5, i), (5, i), colors.HexColor("#fff3cd"))

    item_table.setStyle(item_style)
    story.append(item_table)
    story.append(Spacer(1, 12))

    if insumos:
        story.append(Paragraph("Insumos da Carga Paletizada (CP)", styles["Normal"]))
        story.append(Spacer(1, 6))
        insumos_table = Table([
            ["Palete", "Chapa", "Quadro sem ripa", "Quadro com ripa"],
            [
                str(insumos.get("Palete", 0)),
                str(insumos.get("Chapa", 0)),
                str(insumos.get("Quadro sem ripa", 0)),
                str(insumos.get("Quadro com ripa", 0)),
            ]
        ], colWidths=[45 * mm, 45 * mm, 55 * mm, 55 * mm])
        insumos_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1026D6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#c9d2de")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(insumos_table)
        story.append(Spacer(1, 12))

    if boc:
        story.append(Paragraph("Solicitações de BOC", styles["Normal"]))
        story.append(Spacer(1, 6))
        boc_rows = [["Data/Hora", "Remessa", "Item", "Descrição", "Qtd", "Usuário"]]
        for b in boc:
            boc_rows.append([
                b.get("data_hora", ""),
                b.get("remessa", ""),
                b.get("item", ""),
                b.get("descricao", ""),
                str(b.get("qtd", "")),
                b.get("usuario", ""),
            ])

        boc_table = Table(
            boc_rows,
            colWidths=[35 * mm, 28 * mm, 28 * mm, 90 * mm, 16 * mm, 28 * mm],
            repeatRows=1,
        )
        boc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1026D6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#c9d2de")),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(boc_table)
        story.append(Spacer(1, 12))

    story.append(Paragraph("Assinaturas", styles["Normal"]))
    story.append(Spacer(1, 10))

    assinatura_table = Table([
        ["______________________________", "", "______________________________"],
        [meta.get("conferente", ""), "", meta.get("responsavel", "Liderança / Responsável")],
        ["Assinatura do Conferente", "", "Assinatura da Liderança / Responsável"],
    ], colWidths=[95 * mm, 20 * mm, 95 * mm])

    assinatura_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(assinatura_table)
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"Emitido em: {now_sp_str()}", styles["Normal"]))

    if qr_img_path and os.path.exists(qr_img_path):
        story.append(Spacer(1, 12))
        story.append(Paragraph("QR Code do Documento", styles["Normal"]))
        story.append(Spacer(1, 5))
        story.append(RLImage(qr_img_path, width=28 * mm, height=28 * mm))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def gerar_pdf(dt, df_items, insumos, boc, meta):
    public_id = f"espelho_dt_{dt}"

    first_pdf = build_pdf_bytes(dt, df_items, insumos, boc, meta)
    first_url = upload_pdf_cloudinary(first_pdf, public_id)

    qr_path = f"qr_{dt}.png"
    qrcode.make(first_url).save(qr_path)

    try:
        final_pdf = build_pdf_bytes(dt, df_items, insumos, boc, meta, qr_path)
        final_url = upload_pdf_cloudinary(final_pdf, public_id)
    finally:
        if os.path.exists(qr_path):
            os.remove(qr_path)

    return final_url

# =========================================================
# PÁGINA ASSISTENTE
# =========================================================
def page_assistente():
    show_logo_main()
    st.title("Assistente")

    f_vl06 = st.file_uploader("Upload VL06", type=["xlsx"], key="upl_vl06")
    if f_vl06 is not None:
        df_vl06 = pd.read_excel(f_vl06)
        df_vl06.to_csv(VL06_FILE, index=False)
        st.success("VL06 carregada com sucesso.")
        st.dataframe(df_vl06.head(20), use_container_width=True)

    f_sku = st.file_uploader("Upload Base SKU", type=["xlsx"], key="upl_sku")
    if f_sku is not None:
        df_sku = pd.read_excel(f_sku)
        df_sku.to_csv(SKU_FILE, index=False)
        st.success("Base SKU carregada com sucesso.")
        st.dataframe(df_sku.head(20), use_container_width=True)

# =========================================================
# PÁGINA INSUMOS
# =========================================================
def page_insumos():
    show_logo_main()
    st.title("Insumos")

    if not os.path.exists(VL06_FILE):
        st.warning("Suba a VL06 primeiro.")
        return

    dt = get_current_dt()
    if not dt:
        st.info("Abra uma DT na conferência para habilitar os insumos.")
        return

    st.info(f"DT atual: {dt}")

    base = pd.read_csv(VL06_FILE)
    df_dt = base[base["Nº transporte"].astype(str) == dt].copy()
    if df_dt.empty:
        st.warning("DT não encontrada na VL06.")
        return

    tipo = infer_tipo_carga(df_dt)
    if tipo != "CP":
        st.warning("Esta DT não é CP. Insumos só são obrigatórios para carga paletizada.")
        return

    ins = db["insumos"].get(dt, {})

    pal = st.number_input("Palete", value=int(ins.get("Palete", 0)))
    cha = st.number_input("Chapa", value=int(ins.get("Chapa", 0)))
    qs = st.number_input("Quadro sem ripa", value=int(ins.get("Quadro sem ripa", 0)))
    qc = st.number_input("Quadro com ripa", value=int(ins.get("Quadro com ripa", 0)))

    c1, c2 = st.columns(2)
    if c1.button("Salvar Insumos", use_container_width=True):
        db["insumos"][dt] = {
            "Palete": int(pal),
            "Chapa": int(cha),
            "Quadro sem ripa": int(qs),
            "Quadro com ripa": int(qc),
        }
        save_db(db)
        st.success("Insumos salvos com sucesso.")
        st.rerun()

    if c2.button("Editar Insumos", use_container_width=True):
        st.info("Altere os campos acima e clique em Salvar Insumos.")

# =========================================================
# PÁGINA CONFERÊNCIA
# =========================================================
def page_conferencia(modo_coletor=False):
    if not os.path.exists(VL06_FILE) or not os.path.exists(SKU_FILE):
        st.warning("Suba VL06 e base SKU primeiro.")
        return

    base = pd.read_csv(VL06_FILE)
    sku_df = pd.read_csv(SKU_FILE)

    if sku_df.shape[1] < 2:
        st.error("A base SKU precisa ter SKU na primeira coluna e quantidade por palete na segunda.")
        return

    sku_map = dict(zip(sku_df.iloc[:, 0].astype(str), pd.to_numeric(sku_df.iloc[:, 1], errors="coerce").fillna(0).astype(int)))

    show_logo_main()
    st.title("Modo Coletor" if modo_coletor else "Conferência")

    pesquisa = st.text_input("Pesquisar DT", value=get_current_dt())
    dts = get_dt_list()
    filtradas = [d for d in dts if pesquisa in d] if pesquisa else dts

    if not filtradas:
        st.warning("Nenhuma DT encontrada.")
        return

    dt = st.selectbox("Selecione a DT", filtradas)
    set_current_dt(dt)

    df_dt = base[base["Nº transporte"].astype(str) == dt].copy()
    if df_dt.empty:
        st.warning("DT não encontrada.")
        return

    conf = get_dt_conf(dt)

    if conf["status"] == "FINALIZADO":
        st.error("DT finalizada sem divergência - bloqueada.")
        return

    if conf["status"] == "DIVERGENTE" and st.session_state["perfil"] != "assistente":
        st.warning("DT finalizada com divergência - aguarde reabertura pelo assistente.")
        return

    if not conf["inicio_data"]:
        conf["inicio_data"] = now_sp_date()
        conf["inicio_hora"] = now_sp_time()
        save_db(db)

    for _, row in df_dt.iterrows():
        sku = str(row["Material"])
        if sku not in conf["itens"]:
            conf["itens"][sku] = {
                "sol": safe_int(row["Qtd.remessa"]),
                "conf": 0,
                "descricao": str(row.get("Denominação de item", "")),
                "remessa": str(row.get("Remessa", "")),
            }

    cliente = str(df_dt.iloc[0].get("Nome do emissor da ordem", ""))
    transportadora = str(df_dt.iloc[0].get("Nome agente de frete", ""))
    data_agenda = format_date_only(df_dt.iloc[0].get("Data agenda", ""))
    hora_agenda = format_time_only(df_dt.iloc[0].get("Hora agenda", ""))
    perfil_carregamento = str(df_dt.iloc[0].get("Perfil de carregamento", ""))
    tipo = infer_tipo_carga(df_dt)
    conf["tipo"] = tipo

    metragem_cubica = round(pd.to_numeric(df_dt["Volume"], errors="coerce").fillna(0).sum() / 1000, 3) if "Volume" in df_dt.columns else 0
    sku_unicos = df_dt["Material"].astype(str).nunique()
    remessas = df_dt["Remessa"].astype(str).nunique()
    total_caixas = int(pd.to_numeric(df_dt["Qtd.remessa"], errors="coerce").fillna(0).sum())

    produtividade = produtividade_por_tipo(tipo)

    inicio_real = datetime.strptime(f"{conf['inicio_data']} {conf['inicio_hora']}", "%d/%m/%Y %H:%M:%S").replace(tzinfo=APP_TZ)
    agora = now_sp()
    tempo_real = max((agora - inicio_real).total_seconds() / 60, 0)
    previsao_total = round(total_caixas / produtividade, 1) if produtividade > 0 else 0
    tempo_restante = round(max(previsao_total - tempo_real, 0), 1)

    st.subheader("Resumo da DT")

    r1 = st.columns(4, gap="medium")
    with r1[0]:
        metric_card("Cliente", cliente)
    with r1[1]:
        metric_card("Transportadora", transportadora)
    with r1[2]:
        metric_card("Status DT", conf["status"])
    with r1[3]:
        metric_card("Quantidade de remessas", remessas)

    r2 = st.columns(4, gap="medium")
    with r2[0]:
        metric_card("SKU únicos", sku_unicos)
    with r2[1]:
        metric_card("Total de caixas", total_caixas)
    with r2[2]:
        metric_card("Data agenda", data_agenda)
    with r2[3]:
        metric_card("Hora agenda", hora_agenda)

    r3 = st.columns(4, gap="medium")
    with r3[0]:
        metric_card("Perfil de carregamento", perfil_carregamento)
    with r3[1]:
        metric_card("Tipo de carga", tipo)
    with r3[2]:
        metric_card("Metragem cúbica", f"{metragem_cubica:.3f} m³")
    with r3[3]:
        metric_card("Previsão de conferência", f"{previsao_total:.1f} min")

    r4 = st.columns(3, gap="medium")
    with r4[0]:
        metric_card("Tempo real", f"{tempo_real:.1f} min")
    with r4[1]:
        metric_card("Tempo restante", f"{tempo_restante:.1f} min")
    with r4[2]:
        metric_card("Hora fim", conf["fim_hora"] if conf["fim_hora"] else "-")

    st.divider()

    c1, c2, c3, c4 = st.columns(4)
    conf["conferente"] = c1.text_input("Conferente", value=conf.get("conferente", ""))
    conf["turno"] = c2.selectbox("Turno", ["T1", "T2", "T3"], index=["T1", "T2", "T3"].index(conf.get("turno") or "T1"))
    c3.text_input("Hora início", value=f"{conf['inicio_data']} {conf['inicio_hora']}".strip(), disabled=True)
    c4.text_input("Hora fim", value=(f"{conf['fim_data']} {conf['fim_hora']}".strip() if conf["fim_hora"] else ""), disabled=True)

    if tipo == "CP":
        st.info("Esta DT é CP. O botão de insumos foi liberado automaticamente.")
        if st.button("Ir para Insumos", use_container_width=True):
            st.session_state["menu_target"] = "Insumos"
            save_db(db)
            st.rerun()

    st.divider()
    st.subheader("Lançamento por palete / fracionado")

    l1, l2, l3 = st.columns([2, 1, 1])
    sku_lancar = l1.text_input("SKU")
    qtd_ho = l2.number_input("HO (Qtd Paletes)", min_value=0, value=0, step=1)
    qtd_he = l3.number_input("HE (Qtd Fracionada)", min_value=0, value=0, step=1)

    if st.button("Lançar Quantidades", use_container_width=True):
        if sku_lancar not in conf["itens"]:
            st.error("SKU não pertence a esta DT.")
        elif sku_lancar not in sku_map:
            st.error("SKU não cadastrado na base SKU.")
        else:
            total_ho = int(sku_map[sku_lancar]) * int(qtd_ho)
            total_add = total_ho + int(qtd_he)
            conf["itens"][sku_lancar]["conf"] += total_add
            save_db(db)
            st.success(f"Lançado com sucesso: HO {qtd_ho} + HE {qtd_he} = +{total_add} caixas.")
            st.rerun()

    df_items = pd.DataFrame([
        {
            "remessa": v.get("remessa", ""),
            "sku": sku,
            "descricao": v.get("descricao", ""),
            "Qtd. Solicitada": v["sol"],
            "Qtd. Conferida": v["conf"],
            "Status": status_item(v["sol"], v["conf"]),
        }
        for sku, v in conf["itens"].items()
    ])

    st.subheader("Itens da DT")
    st.dataframe(df_items, use_container_width=True, hide_index=True)

    st.subheader("BOC")

    item_boc = st.text_input("Item BOC")
    qtd_boc = st.number_input("Qtd BOC", min_value=0, value=0, step=1)

    remessa_boc = ""
    descricao_boc = ""
    if item_boc in conf["itens"]:
        remessa_boc = conf["itens"][item_boc].get("remessa", "")
        descricao_boc = conf["itens"][item_boc].get("descricao", "")

    if st.button("Salvar BOC"):
        db["boc"].setdefault(dt, []).append({
            "data_hora": now_sp_str(),
            "remessa": remessa_boc,
            "item": item_boc,
            "descricao": descricao_boc,
            "qtd": int(qtd_boc),
            "usuario": st.session_state["usuario"],
        })
        save_db(db)
        st.success("BOC salvo com sucesso.")
        st.rerun()

    bocs = db["boc"].get(dt, [])
    if bocs:
        st.write("BOCs lançados")
        for i, b in enumerate(bocs):
            bx1, bx2 = st.columns([6, 1])
            bx1.write(f"{b['item']} | {b['qtd']} | {b.get('descricao','')}")
            if st.session_state["perfil"] == "assistente":
                if bx2.button(f"Excluir {i}", key=f"exc_boc_{dt}_{i}"):
                    db["boc"][dt].pop(i)
                    save_db(db)
                    st.rerun()

    bpdf1, bpdf2 = st.columns(2)

    if bpdf1.button("Gerar PDF", use_container_width=True):
        meta_pdf = {
            "status": conf["status"],
            "cliente": cliente,
            "transportadora": transportadora,
            "conferente": conf.get("conferente", ""),
            "turno": conf.get("turno", ""),
            "inicio_data": conf.get("inicio_data", ""),
            "inicio_hora": conf.get("inicio_hora", ""),
            "fim_data": conf.get("fim_data", ""),
            "fim_hora": conf.get("fim_hora", ""),
            "data_agenda": data_agenda,
            "hora_agenda": hora_agenda,
            "perfil": perfil_carregamento,
            "tipo": tipo,
            "remessas": remessas,
            "caixas": total_caixas,
            "m3": f"{metragem_cubica:.3f} m³",
            "duracao": f"{tempo_real:.0f} min",
            "responsavel": "Liderança / Responsável",
        }

        pdf_url = gerar_pdf(dt, df_items, db["insumos"].get(dt, {}), db["boc"].get(dt, []), meta_pdf)
        conf["pdf"] = pdf_url
        save_db(db)
        st.success("PDF gerado com sucesso.")
        st.link_button("Abrir último PDF", pdf_url, use_container_width=True)

    if bpdf2.button("Finalizar Conferência", use_container_width=True):
        if tipo == "CP":
            insumos = db["insumos"].get(dt, {})
            if not insumos or sum(insumos.values()) == 0:
                st.error("Obrigatório lançar insumos antes de finalizar uma carga CP.")
                return

        status_final = dt_status_from_items(conf["itens"])

        conf["status"] = status_final
        conf["fim_data"] = now_sp_date()
        conf["fim_hora"] = now_sp_time()

        inicio_pdf = datetime.strptime(f"{conf['inicio_data']} {conf['inicio_hora']}", "%d/%m/%Y %H:%M:%S").replace(tzinfo=APP_TZ)
        fim_pdf = datetime.strptime(f"{conf['fim_data']} {conf['fim_hora']}", "%d/%m/%Y %H:%M:%S").replace(tzinfo=APP_TZ)
        duracao_min = max((fim_pdf - inicio_pdf).total_seconds() / 60, 0)

        meta_pdf = {
            "status": status_final,
            "cliente": cliente,
            "transportadora": transportadora,
            "conferente": conf.get("conferente", ""),
            "turno": conf.get("turno", ""),
            "inicio_data": conf.get("inicio_data", ""),
            "inicio_hora": conf.get("inicio_hora", ""),
            "fim_data": conf.get("fim_data", ""),
            "fim_hora": conf.get("fim_hora", ""),
            "data_agenda": data_agenda,
            "hora_agenda": hora_agenda,
            "perfil": perfil_carregamento,
            "tipo": tipo,
            "remessas": remessas,
            "caixas": total_caixas,
            "m3": f"{metragem_cubica:.3f} m³",
            "duracao": f"{duracao_min:.0f} min",
            "responsavel": "Liderança / Responsável",
        }

        pdf_url = gerar_pdf(dt, df_items, db["insumos"].get(dt, {}), db["boc"].get(dt, []), meta_pdf)
        conf["pdf"] = pdf_url
        save_db(db)

        st.success(f"Conferência finalizada com status: {status_final}")
        st.rerun()

# =========================================================
# PÁGINA REABRIR DT
# =========================================================
def page_reabrir():
    show_logo_main()
    st.title("Reabrir DT")

    if st.session_state["perfil"] != "assistente":
        st.error("Acesso restrito.")
        return

    divergentes = [dt for dt, conf in db["dts"].items() if conf.get("status") == "DIVERGENTE"]

    if not divergentes:
        st.info("Não há DTs divergentes para reabrir.")
        return

    dt = st.selectbox("Selecione a DT divergente", divergentes)

    if st.button("Reabrir DT", use_container_width=True):
        conf = db["dts"][dt]
        for item in conf["itens"]:
            conf["itens"][item]["conf"] = 0
        conf["status"] = "ABERTO"
        conf["fim_data"] = ""
        conf["fim_hora"] = ""
        conf["pdf"] = ""
        save_db(db)
        st.success(f"DT {dt} reaberta e zerada com sucesso.")
        st.rerun()

# =========================================================
# PÁGINA GESTÃO
# =========================================================
def page_gestao():
    show_logo_main()
    st.title("Painel do Gestor")

    if not db["dts"]:
        st.info("Sem dados para dashboard.")
        return

    registros = []
    for dt, conf in db["dts"].items():
        registros.append({
            "DT": dt,
            "Conferente": conf.get("conferente", "-") or "-",
            "Status": conf.get("status", "ABERTO"),
            "Tipo": conf.get("tipo", "-") or "-",
            "Caixas Conferidas": sum(v.get("conf", 0) for v in conf.get("itens", {}).values()),
        })

    df_dash = pd.DataFrame(registros)

    c1, c2, c3 = st.columns(3)
    c1.metric("Total DTs", len(df_dash))
    c2.metric("Cargas com divergência", int((df_dash["Status"] == "DIVERGENTE").sum()))
    c3.metric("Finalizadas", int((df_dash["Status"] == "FINALIZADO").sum()))

    st.subheader("Tipo de carga")
    tipo_counts = df_dash["Tipo"].value_counts().reset_index()
    tipo_counts.columns = ["Tipo", "Quantidade"]
    fig_tipo = px.bar(tipo_counts, x="Tipo", y="Quantidade", text="Quantidade")
    st.plotly_chart(fig_tipo, use_container_width=True)

    st.subheader("Ranking por conferente")
    ranking = (
        df_dash.groupby("Conferente", dropna=False)
        .agg(DTs=("DT", "count"), Caixas=("Caixas Conferidas", "sum"))
        .reset_index()
        .sort_values(["Caixas", "DTs"], ascending=False)
    )
    st.dataframe(ranking, use_container_width=True, hide_index=True)

    st.subheader("Cargas com divergência")
    divergentes = df_dash[df_dash["Status"] == "DIVERGENTE"].copy()
    if divergentes.empty:
        st.info("Sem cargas com divergência.")
    else:
        st.dataframe(divergentes, use_container_width=True, hide_index=True)

# =========================================================
# PÁGINA FATURAMENTO
# =========================================================
def page_faturista():
    show_logo_main()
    st.title("Faturamento")

    if not db["dts"]:
        st.info("Sem DTs cadastradas.")
        return

    dt = st.selectbox("Selecione a DT", list(db["dts"].keys()))
    conf = db["dts"].get(dt, {})

    if conf.get("pdf"):
        st.link_button("Abrir PDF", conf["pdf"], use_container_width=True)
    else:
        st.warning("PDF ainda não disponível.")

    st.subheader("BOC")
    st.write(db["boc"].get(dt, []))

    st.subheader("Insumos")
    st.write(db["insumos"].get(dt, {}))

# =========================================================
# MAIN
# =========================================================
if not st.session_state.get("auth_ok"):
    login_screen()
    st.stop()

show_logo_sidebar()
st.sidebar.success(f"Usuário: {st.session_state['usuario']}")
st.sidebar.info(f"Perfil: {st.session_state['perfil']}")

if st.sidebar.button("Sair", use_container_width=True):
    logout()

sections = allowed_sections()
default_menu = st.session_state.pop("menu_target", sections[0] if sections else "")
index_default = sections.index(default_menu) if default_menu in sections else 0
op = st.sidebar.radio("Menu", sections, index=index_default)

if op == "Assistente":
    page_assistente()
elif op == "Conferência":
    page_conferencia(True)
elif op == "Coletor":
    page_conferencia(True)
elif op == "Insumos":
    page_insumos()
elif op == "Reabrir DT":
    page_reabrir()
elif op == "Gestão":
    page_gestao()
elif op == "Faturamento":
    page_faturista()
