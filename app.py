import streamlit as st
import pandas as pd
import json
import os
import pytz
from datetime import datetime
import plotly.express as px

# PDF
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from io import BytesIO

# CLOUDINARY
import cloudinary
import cloudinary.uploader

# CONFIG CLOUDINARY
cloudinary.config(
    cloud_name=st.secrets["cloudinary"]["cloud_name"],
    api_key=st.secrets["cloudinary"]["api_key"],
    api_secret=st.secrets["cloudinary"]["api_secret"]
)

# CONFIG APP
st.set_page_config(layout="wide")
LOGO = "Nadir_Branco_Laranja.png"
DATA_FILE = "data.json"

# ===== TIME =====
def agora_br():
    return datetime.now(pytz.timezone("America/Sao_Paulo"))

# ===== DATABASE =====
def load():
    if not os.path.exists(DATA_FILE):
        return {}
    return json.load(open(DATA_FILE))

def save(data):
    json.dump(data, open(DATA_FILE, "w"), indent=2)

# ===== PDF =====
def gerar_pdf(dt, info):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    try:
        elements.append(Image(LOGO, width=4*cm, height=2*cm))
    except:
        pass

    elements.append(Paragraph("<b>ESPELHO DE CONFERÊNCIA</b>", styles["Title"]))
    elements.append(Spacer(1, 10))

    texto = f"""
    <b>DT:</b> {dt}<br/>
    <b>Conferente:</b> {info['conferente']}<br/>
    <b>Início:</b> {info['inicio']}<br/>
    <b>Fim:</b> {info['fim']}<br/>
    <b>Total Caixas:</b> {info['caixas']}
    """

    elements.append(Paragraph(texto, styles["Normal"]))
    elements.append(Spacer(1, 20))

    table_data = [["Campo", "Valor"]]
    table_data.append(["DT", dt])
    table_data.append(["Conferente", info["conferente"]])
    table_data.append(["Caixas", info["caixas"]])

    table = Table(table_data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('GRID',(0,0),(-1,-1),1,colors.black)
    ]))

    elements.append(table)
    elements.append(Spacer(1, 30))

    elements.append(Paragraph("__________________________", styles["Normal"]))
    elements.append(Paragraph("Conferente", styles["Normal"]))

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()

    return pdf

# ===== CLOUD =====
def upload_pdf(pdf_bytes, nome):
    result = cloudinary.uploader.upload(
        pdf_bytes,
        resource_type="raw",
        public_id=nome,
        format="pdf"
    )
    return result["secure_url"]

# ===== LOGIN =====
def login():
    st.image(LOGO, width=180)
    user = st.text_input("Usuário")
    pwd = st.text_input("Senha", type="password")

    if st.button("Entrar"):
        users = st.secrets["users"]
        if user in users:
            senha, perfil = users[user].split("|")
            if pwd == senha:
                st.session_state["user"] = user
                st.session_state["perfil"] = perfil
                st.rerun()

# ===== MAIN =====
if "user" not in st.session_state:
    login()
    st.stop()

data = load()

st.sidebar.image(LOGO)
menu = st.sidebar.selectbox("Menu", ["Conferência","Gestão"])

# ===== CONFERÊNCIA =====
if menu == "Conferência":

    st.title("Conferência")

    dt = st.text_input("Digite a DT")

    if st.button("Iniciar"):
        data[dt] = {
            "inicio": agora_br().strftime("%d/%m/%Y %H:%M:%S"),
            "fim": "",
            "conferente": st.session_state["user"],
            "caixas": 0,
            "pdf": ""
        }
        save(data)

    if dt in data:
        info = data[dt]

        st.write(info)

        caixas = st.number_input("Adicionar caixas", 0)

        if st.button("Adicionar"):
            data[dt]["caixas"] += caixas
            save(data)

        if st.button("Finalizar"):
            data[dt]["fim"] = agora_br().strftime("%d/%m/%Y %H:%M:%S")

            pdf = gerar_pdf(dt, data[dt])
            url = upload_pdf(pdf, f"DT_{dt}")

            data[dt]["pdf"] = url

            save(data)

            st.success("PDF salvo na nuvem!")
            st.link_button("Abrir PDF", url)

# ===== GESTÃO =====
if menu == "Gestão":

    st.title("Gestão")

    df = pd.DataFrame(data).T.reset_index().rename(columns={"index":"dt"})

    if df.empty:
        st.warning("Sem dados")
    else:
        st.dataframe(df)

        fig = px.bar(df, x="conferente", y="caixas")
        st.plotly_chart(fig)

        st.subheader("PDFs")
        for i, row in df.iterrows():
            if row["pdf"]:
                st.link_button(f"DT {row['dt']}", row["pdf"])
