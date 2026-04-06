import json
import os
import tempfile
from datetime import datetime
from io import BytesIO

import cloudinary
import cloudinary.uploader
import pandas as pd
import qrcode
import streamlit as st
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
def now_dt():
    return datetime.now()


def now_str():
    return now_dt().strftime("%d/%m/%Y %H:%M:%S")


def show_logo_sidebar():
    if os.path.exists(LOGO_FILE):
        st.sidebar.image(LOGO_FILE, use_container_width=True)


def show_logo_main():
    if os.path.exists(LOGO_FILE):
        st.image(LOGO_FILE, width=180)


def allowed_sections():
    perfil = st.session_state["perfil"]
    mapping = {
        "assistente": ["Assistente", "Conferência", "Insumos", "Reabrir DT"],
        "conferente": ["Conferência", "Insumos"],
        "gestao": ["Gestão"],
        "faturista": ["Faturamento"],
        "coletor": ["Coletor", "Insumos"],
    }
    return mapping.get(perfil, [])


def get_users():
    return st.secrets["users"]


def format_time_value(value):
    if pd.isna(value):
        return ""
    try:
        return pd.to_datetime(value).strftime("%H:%M:%S")
    except Exception:
        return str(value)


def format_date_value(value):
    if pd.isna(value):
        return ""
    try:
        return pd.to_datetime(value).strftime("%d/%m/%Y")
    except Exception:
        return str(value)


def metric_card(label, value):
    st.markdown(
        f"""
        <div style="
            background:#ffffff;
            border-radius:14px;
            padding:16px;
            border:1px solid #e5e7eb;
            box-shadow:0 1px 4px rgba(0,0,0,0.06);
            min-height:95px;
        ">
            <div style="font-size:12px;color:#6b7280;margin-bottom:6px;">{label}</div>
            <div style="font-size:18px;font-weight:700;color:#111827;">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def parse_tipo_carga_from_vl06(df_dt):
    candidates = []
    if "Tipo de carga" in df_dt.columns:
        candidates.extend(df_dt["Tipo de carga"].dropna().astype(str).tolist())
    if "Perfil de carregamento" in df_dt.columns:
        for value in df_dt["Perfil de carregamento"].dropna().astype(str).tolist():
            upper = value.upper()
            if "CP" in upper:
                candidates.append("CP")
            elif "CM" in upper:
                candidates.append("CM")
            elif "CB" in upper:
                candidates.append("CB")

    for c in candidates:
        upper = str(c).upper()
        if "CP" in upper:
            return "CP"
        if "CM" in upper:
            return "CM"
        if "CB" in upper:
            return "CB"
    return "CB"


def produtividade_por_tipo(tipo):
    return {
        "CB": 30,
        "CP": 80,
        "CM": 50,
    }.get(tipo, 40)


def dt_status(dt):
    return db["dts"].get(dt, {}).get("status", "ABERTO")


def get_dt_conf(dt):
    if dt not in db["dts"]:
        db["dts"][dt] = {
            "itens": {},
            "status": "ABERTO",
            "pdf": "",
            "conferente": "",
            "turno": "",
            "inicio": "",
            "fim": "",
            "tipo": "",
            "meta": {},
        }
    return db["dts"][dt]


def get_dt_list_from_vl06():
    if not os.path.exists(VL06_FILE):
        return []
    base = pd.read_csv(VL06_FILE)
    if "Nº transporte" not in base.columns:
        return []
    return sorted(base["Nº transporte"].dropna().astype(str).unique().tolist())


def set_current_dt(dt):
    st.session_state["current_dt"] = dt


def get_current_dt():
    return st.session_state.get("current_dt", "")


# =========================================================
# LOGIN
# =========================================================
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
                st.session_state["auth"] = True
                st.session_state["user"] = usuario
                st.session_state["perfil"] = perfil
                st.rerun()

        st.error("Usuário ou senha inválidos.")


def logout():
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()

# =========================================================
# PDF
# =========================================================
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


def build_pdf_bytes(dt, df_itens, insumos, boc, meta, qr_path=None):
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PdfTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=20,
        textColor=colors.HexColor("#1D39C4"),
        leading=24,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "PdfSubtitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=10,
        textColor=colors.HexColor("#6B7280"),
        spaceAfter=10,
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=10 * mm,
    )
    story = []

    if os.path.exists(LOGO_FILE):
        story.append(RLImage(LOGO_FILE, width=45 * mm, height=18 * mm))

    story.append(Paragraph("ESPELHO DE CONFERÊNCIA", title_style))
    story.append(Paragraph("Documento operacional de conferência logística", subtitle_style))
    story.append(Spacer(1, 6))

    info = [
        ["DT", dt, "Status", meta.get("status", "")],
        ["Cliente", meta.get("cliente", ""), "Transportadora", meta.get("transportadora", "")],
        ["Conferente", meta.get("conferente", ""), "Turno", meta.get("turno", "")],
        ["Início", meta.get("inicio", ""), "Fim", meta.get("fim", "")],
        ["Data agenda", meta.get("data_agenda", ""), "Hora agenda", meta.get("hora_agenda", "")],
        ["Perfil de carregamento", meta.get("perfil", ""), "Tipo de carga", meta.get("tipo", "")],
        ["Quantidade de remessas", str(meta.get("remessas", "")), "Total de caixas", str(meta.get("caixas", ""))],
        ["Metragem cúbica", str(meta.get("m3", "")), "Duração", meta.get("duracao", "")],
    ]

    info_table = Table(info, colWidths=[45 * mm, 105 * mm, 45 * mm, 105 * mm])
    info_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C7D2DA")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 10))

    item_data = [["Remessa", "Doc. Ref.", "Material", "Descrição", "Qtd Sol.", "Qtd Conf.", "Status"]]
    for _, row in df_itens.iterrows():
        status = "OK" if int(row["sol"]) == int(row["conf"]) else "DIVERGENTE"
        item_data.append([
            str(row.get("remessa", "")),
            str(row.get("doc_ref", "")),
            str(row["sku"]),
            str(row.get("descricao", "")),
            str(int(row["sol"])),
            str(int(row["conf"])),
            status,
        ])

    item_table = Table(
        item_data,
        colWidths=[28 * mm, 30 * mm, 34 * mm, 100 * mm, 20 * mm, 20 * mm, 24 * mm],
        repeatRows=1,
    )

    item_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1026D6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C7D2DA")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])

    for i in range(1, len(item_data)):
        if item_data[i][6] == "OK":
            item_style.add("BACKGROUND", (6, i), (6, i), colors.HexColor("#DDF6E4"))
        else:
            item_style.add("BACKGROUND", (6, i), (6, i), colors.HexColor("#F8D7DA"))

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
        ], colWidths=[40 * mm, 40 * mm, 55 * mm, 55 * mm])

        insumos_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1026D6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C7D2DA")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(insumos_table)
        story.append(Spacer(1, 12))

    if boc:
        story.append(Paragraph("Solicitações de BOC", styles["Normal"]))
        story.append(Spacer(1, 6))

        boc_data = [["Data/Hora", "Remessa", "Item", "Descrição", "Qtd", "Usuário"]]
        for b in boc:
            boc_data.append([
                b.get("data_hora", ""),
                b.get("remessa", ""),
                b.get("item", ""),
                b.get("descricao", ""),
                str(b.get("qtd", "")),
                b.get("usuario", ""),
            ])

        boc_table = Table(
            boc_data,
            colWidths=[34 * mm, 28 * mm, 28 * mm, 90 * mm, 16 * mm, 28 * mm],
            repeatRows=1,
        )
        boc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1026D6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C7D2DA")),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(boc_table)
        story.append(Spacer(1, 12))

    story.append(Paragraph("Assinaturas", styles["Normal"]))
    story.append(Spacer(1, 12))

    sig_table = Table([
        ["______________________________", "", "______________________________"],
        [meta.get("conferente", ""), "", meta.get("responsavel", "Liderança / Responsável")],
        ["Assinatura do Conferente", "", "Assinatura da Liderança / Responsável"],
    ], colWidths=[90 * mm, 20 * mm, 90 * mm])

    sig_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(sig_table)
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Emitido em: {now_str()}", styles["Normal"]))

    if qr_path and os.path.exists(qr_path):
        story.append(Spacer(1, 12))
        story.append(Paragraph("QR Code do Documento", styles["Normal"]))
        story.append(Spacer(1, 6))
        story.append(RLImage(qr_path, width=28 * mm, height=28 * mm))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def gerar_pdf(dt, df_itens, insumos, boc, meta):
    public_id = f"espelho_dt_{dt}"

    first_pdf = build_pdf_bytes(dt, df_itens, insumos, boc, meta, qr_path=None)
    first_url = upload_pdf_cloudinary(first_pdf, public_id)

    qr_img = qrcode.make(first_url)
    qr_path = f"qr_{dt}.png"
    qr_img.save(qr_path)

    try:
        final_pdf = build_pdf_bytes(dt, df_itens, insumos, boc, meta, qr_path=qr_path)
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

    f_vl06 = st.file_uploader("Upload VL06", type=["xlsx"])
    if f_vl06 is not None:
        df_vl06 = pd.read_excel(f_vl06)
        df_vl06.to_csv(VL06_FILE, index=False)
        st.success("VL06 carregada com sucesso.")
        st.dataframe(df_vl06.head(20), use_container_width=True)

    f_sku = st.file_uploader("Upload Base SKU", type=["xlsx"])
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
        dt = st.text_input("DT")
        if not dt:
            st.info("Selecione uma DT na conferência ou informe manualmente.")
            return

    st.info(f"DT atual: {dt}")

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
        st.info("Altere os campos e clique em Salvar Insumos.")

# =========================================================
# PÁGINA CONFERÊNCIA / COLETOR
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

    dt_query = st.text_input("Pesquisar DT", value=get_current_dt())
    dt_options = get_dt_list_from_vl06()
    filtered = [d for d in dt_options if dt_query in d] if dt_query else dt_options

    if not filtered:
        st.warning("Nenhuma DT encontrada.")
        return

    dt = st.selectbox("Selecione a DT", filtered)
    set_current_dt(dt)

    df = base[base["Nº transporte"].astype(str) == dt].copy()

    if df.empty:
        st.warning("DT não encontrada.")
        return

    conf = get_dt_conf(dt)
    status = conf.get("status", "ABERTO")

    if status == "FINALIZADO":
        st.error("DT finalizada sem divergência - bloqueada.")
        return

    if status == "DIVERGENTE" and st.session_state["perfil"] != "assistente":
        st.warning("DT divergente - aguarde reabertura pelo assistente.")
        return

    if not conf.get("inicio"):
        conf["inicio"] = now_dt().strftime("%H:%M:%S")
        save_db(db)

    for _, r in df.iterrows():
        cod = str(r["Material"])
        if cod not in conf["itens"]:
            conf["itens"][cod] = {
                "sol": int(pd.to_numeric(r["Qtd.remessa"], errors="coerce")),
                "conf": 0,
                "descricao": str(r.get("Denominação de item", "")),
                "remessa": str(r.get("Remessa", "")),
                "doc_ref": str(r.get("Documento referência", "")),
            }

    cliente = str(df.iloc[0].get("Nome do emissor da ordem", "N/A"))
    transportadora = str(df.iloc[0].get("Nome agente de frete", "N/A"))
    data_agenda = format_date_value(df.iloc[0].get("Data agenda", ""))
    hora_agenda = format_time_value(df.iloc[0].get("Hora agenda", ""))
    perfil_carregamento = str(df.iloc[0].get("Perfil de carregamento", "N/A"))
    m3 = round(pd.to_numeric(df.get("Volume", 0), errors="coerce").fillna(0).sum() / 1000, 3)
    sku_unicos = df["Material"].astype(str).nunique()
    remessas = df["Remessa"].astype(str).nunique()
    total_caixas = int(pd.to_numeric(df["Qtd.remessa"], errors="coerce").fillna(0).sum())

    tipo_default = conf.get("tipo") or parse_tipo_carga_from_vl06(df)
    tipo = st.selectbox("Tipo carga", ["CB", "CP", "CM"], index=["CB", "CP", "CM"].index(tipo_default))
    conf["tipo"] = tipo

    produtividade = produtividade_por_tipo(tipo)
    inicio_dt = datetime.strptime(conf["inicio"], "%H:%M:%S")
    agora = now_dt()
    inicio_dt_real = agora.replace(hour=inicio_dt.hour, minute=inicio_dt.minute, second=inicio_dt.second, microsecond=0)
    tempo = max((agora - inicio_dt_real).total_seconds() / 60, 0)
    previsto = round(total_caixas / produtividade, 1) if produtividade > 0 else 0
    restante = round(max(previsto - tempo, 0), 1)

    st.subheader("Resumo da DT")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_card("Cliente", cliente)
    with c2:
        metric_card("Transportadora", transportadora)
    with c3:
        metric_card("Status DT", status)
    with c4:
        metric_card("Quantidade de remessas", remessas)

    c5, c6, c7, c8 = st.columns(4)
    with c5:
        metric_card("SKU únicos", sku_unicos)
    with c6:
        metric_card("Total de caixas", total_caixas)
    with c7:
        metric_card("Data agenda", data_agenda)
    with c8:
        metric_card("Hora agenda", hora_agenda)

    c9, c10, c11, c12 = st.columns(4)
    with c9:
        metric_card("Perfil de carregamento", perfil_carregamento)
    with c10:
        metric_card("Tipo de carga", tipo)
    with c11:
        metric_card("Metragem cúbica", f"{m3:.3f} m³")
    with c12:
        metric_card("Previsão de conferência", f"{previsto:.1f} min")

    c13, c14, c15 = st.columns(3)
    with c13:
        metric_card("Tempo real", f"{tempo:.1f} min")
    with c14:
        metric_card("Tempo restante", f"{restante:.1f} min")
    with c15:
        metric_card("Hora fim", conf.get("fim", "-") or "-")

    st.divider()

    cx1, cx2, cx3, cx4 = st.columns(4)
    conf["conferente"] = cx1.text_input("Conferente", value=conf.get("conferente", ""))
    conf["turno"] = cx2.selectbox("Turno", ["Manhã", "Tarde", "Noite"], index=["Manhã", "Tarde", "Noite"].index(conf.get("turno") or "Manhã"))
    cx3.text_input("Hora início", value=conf.get("inicio", ""), disabled=True)
    cx4.text_input("Hora fim", value=conf.get("fim", ""), disabled=True)

    if tipo == "CP":
        if st.button("Ir para Insumos (Obrigatório antes de finalizar)", use_container_width=True):
            st.session_state["menu_target"] = "Insumos"
            st.rerun()

    st.divider()
    st.subheader("Lançamento por palete / fracionado")

    l1, l2, l3 = st.columns([2, 1, 1])
    sku = l1.text_input("SKU")
    qtd_ho = l2.number_input("HO (Qtd Paletes)", min_value=1, value=1, step=1)
    qtd_he = l3.number_input("HE (Qtd Fracionada)", min_value=0, value=0, step=1)

    b1, b2 = st.columns(2)

    if b1.button("Lançar HO", use_container_width=True):
        if sku not in sku_map:
            st.error("SKU não cadastrado na base SKU.")
        elif sku not in conf["itens"]:
            st.error("SKU não pertence a esta DT.")
        else:
            total_lancar = int(sku_map[sku]) * int(qtd_ho)
            conf["itens"][sku]["conf"] += total_lancar
            save_db(db)
            st.success(f"{qtd_ho} palete(s) lançado(s): +{total_lancar} caixas.")
            st.rerun()

    if b2.button("Lançar HE", use_container_width=True):
        if sku not in conf["itens"]:
            st.error("SKU não pertence a esta DT.")
        else:
            conf["itens"][sku]["conf"] += int(qtd_he)
            save_db(db)
            st.success(f"HE lançado: +{qtd_he} caixas.")
            st.rerun()

    df_view = pd.DataFrame([
        {
            "remessa": v.get("remessa", ""),
            "doc_ref": v.get("doc_ref", ""),
            "sku": k,
            "descricao": v.get("descricao", ""),
            "sol": v["sol"],
            "conf": v["conf"],
            "status": "OK" if v["sol"] == v["conf"] else "DIVERGENTE",
        }
        for k, v in conf["itens"].items()
    ])

    st.subheader("Itens da DT")
    st.dataframe(df_view, use_container_width=True, hide_index=True)

    st.subheader("BOC")
    item_boc = st.text_input("Item BOC")
    qtd_boc = st.number_input("Qtd BOC", min_value=0, value=0, step=1)
    descricao_boc = ""
    remessa_boc = ""

    if item_boc in conf["itens"]:
        descricao_boc = conf["itens"][item_boc].get("descricao", "")
        remessa_boc = conf["itens"][item_boc].get("remessa", "")

    if st.button("Salvar BOC"):
        db["boc"].setdefault(dt, []).append({
            "data_hora": now_str(),
            "remessa": remessa_boc,
            "item": item_boc,
            "descricao": descricao_boc,
            "qtd": int(qtd_boc),
            "usuario": st.session_state["user"],
        })
        save_db(db)
        st.success("BOC salvo.")
        st.rerun()

    bocs = db["boc"].get(dt, [])
    if bocs:
        st.write("BOCs lançados")
        for i, b in enumerate(bocs):
            c1, c2 = st.columns([5, 1])
            c1.write(f"{b['item']} - {b['qtd']} - {b.get('descricao','')}")
            if st.session_state["perfil"] == "assistente":
                if c2.button(f"Excluir {i}", key=f"exc_boc_{i}"):
                    db["boc"][dt].pop(i)
                    save_db(db)
                    st.rerun()

    if st.button("Finalizar Conferência", use_container_width=True):
        insumos = db["insumos"].get(dt, {})

        if tipo == "CP":
            if not insumos or sum(insumos.values()) == 0:
                st.error("Obrigatório lançar insumos antes de finalizar uma carga CP.")
                return

        divergente = any(v["sol"] != v["conf"] for v in conf["itens"].values())
        status_final = "DIVERGENTE" if divergente else "FINALIZADO"

        conf["status"] = status_final
        conf["fim"] = now_dt().strftime("%H:%M:%S")

        duracao_min = max((datetime.strptime(conf["fim"], "%H:%M:%S") - datetime.strptime(conf["inicio"], "%H:%M:%S")).total_seconds() / 60, 0)
        meta_pdf = {
            "status": status_final,
            "cliente": cliente,
            "transportadora": transportadora,
            "conferente": conf.get("conferente", ""),
            "turno": conf.get("turno", ""),
            "inicio": conf.get("inicio", ""),
            "fim": conf.get("fim", ""),
            "data_agenda": data_agenda,
            "hora_agenda": hora_agenda,
            "perfil": perfil_carregamento,
            "tipo": tipo,
            "remessas": remessas,
            "caixas": total_caixas,
            "m3": f"{m3:.3f} m³",
            "duracao": f"{duracao_min:.0f} min",
            "responsavel": "Liderança / Responsável",
        }

        pdf_url = gerar_pdf(dt, df_view, insumos, db["boc"].get(dt, []), meta_pdf)
        conf["pdf"] = pdf_url
        save_db(db)

        st.success(f"Conferência finalizada: {status_final}")
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

    dts_div = [dt for dt, conf in db["dts"].items() if conf.get("status") == "DIVERGENTE"]

    if not dts_div:
        st.info("Não há DTs divergentes para reabrir.")
        return

    dt = st.selectbox("Selecione a DT divergente", dts_div)

    if st.button("Reabrir DT", use_container_width=True):
        conf = db["dts"][dt]
        for item in conf["itens"]:
            conf["itens"][item]["conf"] = 0
        conf["status"] = "ABERTO"
        conf["fim"] = ""
        conf["pdf"] = ""
        save_db(db)
        st.success(f"DT {dt} reaberta e zerada com sucesso.")
        st.rerun()

# =========================================================
# PÁGINA GESTÃO
# =========================================================
def page_gestao():
    show_logo_main()
    st.title("Dashboard de Gestão")

    total = len(db["dts"])
    finalizadas = len([v for v in db["dts"].values() if v.get("status") == "FINALIZADO"])
    divergentes = len([v for v in db["dts"].values() if v.get("status") == "DIVERGENTE"])

    c1, c2, c3 = st.columns(3)
    c1.metric("Total DTs", total)
    c2.metric("Finalizadas", finalizadas)
    c3.metric("Divergentes", divergentes)

    ranking = {}
    for _, conf in db["dts"].items():
        conferente = conf.get("conferente", "-") or "-"
        ranking.setdefault(conferente, {"dts": 0, "caixas": 0})

        ranking[conferente]["dts"] += 1
        total_conf = sum(v.get("conf", 0) for v in conf.get("itens", {}).values())
        ranking[conferente]["caixas"] += total_conf

    df_rank = pd.DataFrame([
        {"Conferente": k, "DTs": v["dts"], "Caixas": v["caixas"]}
        for k, v in ranking.items()
    ])

    st.subheader("Ranking de Conferentes")
    if not df_rank.empty:
        df_rank = df_rank.sort_values(by=["Caixas", "DTs"], ascending=False)
        st.dataframe(df_rank, use_container_width=True, hide_index=True)
    else:
        st.info("Sem dados para ranking.")

# =========================================================
# PÁGINA FATURAMENTO
# =========================================================
def page_faturista():
    show_logo_main()
    st.title("Faturamento")

    dts = list(db["dts"].keys())
    if not dts:
        st.info("Sem DTs cadastradas.")
        return

    dt = st.selectbox("Selecione a DT", dts)
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
if not st.session_state.get("auth"):
    login_screen()
    st.stop()

show_logo_sidebar()
st.sidebar.success(f"Usuário: {st.session_state['user']}")
st.sidebar.info(f"Perfil: {st.session_state['perfil']}")

if st.sidebar.button("Sair", use_container_width=True):
    logout()

sections = allowed_sections()
default_menu = st.session_state.pop("menu_target", sections[0])
op = st.sidebar.radio("Menu", sections, index=sections.index(default_menu) if default_menu in sections else 0)

if op == "Assistente":
    page_assistente()
elif op == "Conferência":
    page_conferencia(False)
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
