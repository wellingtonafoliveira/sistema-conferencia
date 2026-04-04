import streamlit as st
import pandas as pd
import json
import os
import pytz
from datetime import datetime
import plotly.express as px

# PDF
from reportlab.platypus import *
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from io import BytesIO

# CLOUDINARY
import cloudinary
import cloudinary.uploader

# CONFIG
st.set_page_config(layout="wide")
LOGO = "Nadir_Branco_Laranja.png"
DATA_FILE = "data.json"
VL06_FILE = "vl06.json"

# CLOUDINARY
cloudinary.config(
    cloud_name=st.secrets["cloudinary"]["cloud_name"],
    api_key=st.secrets["cloudinary"]["api_key"],
    api_secret=st.secrets["cloudinary"]["api_secret"]
)

# ===== TIME =====
def agora():
    return datetime.now(pytz.timezone("America/Sao_Paulo"))

# ===== LOAD =====
def load(file):
    if not os.path.exists(file):
        return {}
    return json.load(open(file))

def save(data, file):
    json.dump(data, open(file, "w"), indent=2)

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

# ===== PDF =====
def gerar_pdf(dt, dados):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Image(LOGO, width=4*cm, height=2*cm))
    elements.append(Paragraph("<b>Espelho de Conferência</b>", styles["Title"]))
    elements.append(Spacer(1,10))

    info = f"""
    DT: {dt}<br/>
    Conferente: {dados['conferente']}<br/>
    Início: {dados['inicio']}<br/>
    Fim: {dados['fim']}<br/>
    Caixas: {dados['caixas']}
    """

    elements.append(Paragraph(info, styles["Normal"]))
    elements.append(Spacer(1,20))

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

# ===== UPLOAD =====
def upload_pdf(pdf, nome):
    result = cloudinary.uploader.upload(
        pdf,
        resource_type="raw",
        public_id=nome,
        format="pdf"
    )
    return result["secure_url"]

# ===== LOGIN =====
if "user" not in st.session_state:
    login()
    st.stop()

perfil = st.session_state["perfil"]

st.sidebar.image(LOGO)
menu = st.sidebar.selectbox("Menu", ["Assistente","Conferência","Gestão"])

data = load(DATA_FILE)
vl06 = load(VL06_FILE)

# ===== ASSISTENTE =====
if menu == "Assistente" and perfil != "conferente":

    st.title("Upload VL06")

    file = st.file_uploader("Enviar VL06")

    if file:
        df = pd.read_excel(file)

        df = df[df["Qtd.remessa"] > 0]

        vl06 = df.to_dict(orient="records")
        save(vl06, VL06_FILE)

        st.success("VL06 carregada com sucesso")

# ===== CONFERÊNCIA =====
if menu == "Conferência":

    st.title("Conferência")

    dt = st.text_input("Digite a DT")

    if dt not in data:
        if st.button("Iniciar"):
            data[dt] = {
                "inicio": agora().strftime("%d/%m/%Y %H:%M"),
                "fim": "",
                "conferente": st.session_state["user"],
                "caixas": 0,
                "pdf": ""
            }
            save(data, DATA_FILE)

    if dt in data:
        st.write(data[dt])

        qtd = st.number_input("Adicionar caixas", 0)

        if st.button("Adicionar"):
            data[dt]["caixas"] += qtd
            save(data, DATA_FILE)

        if st.button("Finalizar"):
            data[dt]["fim"] = agora().strftime("%d/%m/%Y %H:%M")

            pdf = gerar_pdf(dt, data[dt])
            url = upload_pdf(pdf, f"DT_{dt}")

            data[dt]["pdf"] = url
            save(data, DATA_FILE)

            st.success("PDF salvo")
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

        for _, row in df.iterrows():
            if row["pdf"]:
                st.link_button(f"DT {row['dt']}", row["pdf"])
