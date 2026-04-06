# =========================================================
# SISTEMA FINAL CONSOLIDADO - COMPLETO
# =========================================================

import json
from datetime import datetime
from io import BytesIO

import firebase_admin
import pandas as pd
import plotly.express as px
import streamlit as st
from firebase_admin import credentials, firestore, storage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# =========================================================
# CONFIG
# =========================================================
st.set_page_config(layout="wide")

# =========================================================
# FIREBASE
# =========================================================
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        cred_dict = json.loads(st.secrets["firebase"]["service_account_json"])
        cred = credentials.Certificate(cred_dict)

        firebase_admin.initialize_app(
            cred,
            {"storageBucket": st.secrets["firebase"]["bucket_name"]},
        )

    return firestore.client(), storage.bucket()

db, bucket = init_firebase()

# =========================================================
# HELPERS
# =========================================================
def now():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

# =========================================================
# LOGIN HÍBRIDO
# =========================================================
def login():
    st.title("Sistema de Conferência")

    st.subheader("Acesso Corporativo")
    email = st.text_input("E-mail")

    if st.button("Entrar e-mail"):
        doc = db.collection("users").document(email).get()
        if doc.exists:
            st.session_state["perfil"] = doc.to_dict()["perfil"]
            st.session_state["user"] = email
            st.rerun()

    st.divider()

    st.subheader("Acesso Operacional")
    user = st.text_input("Usuário")
    pwd = st.text_input("Senha", type="password")

    if st.button("Entrar"):
        users = st.secrets["users"]
        if user in users:
            senha, perfil = users[user].split("|")
            if pwd == senha:
                st.session_state["perfil"] = perfil
                st.session_state["user"] = user
                st.rerun()

# =========================================================
# BASES (VL06 + SKU)
# =========================================================
def save_base(df, name):
    db.collection("bases").document(name).set({
        "data": df.to_dict("records"),
        "updated": now()
    })

def load_base(name):
    doc = db.collection("bases").document(name).get()
    if doc.exists:
        return pd.DataFrame(doc.to_dict()["data"])
    return pd.DataFrame()

# =========================================================
# PDF COMPLETO
# =========================================================
def gerar_pdf(dt, df, insumos, boc):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))

    story = []
    story.append(Paragraph(f"DT {dt}", st.markdown))
    story.append(Spacer(1, 10))

    table_data = [["SKU", "Solicitado", "Conferido"]]

    for _, row in df.iterrows():
        table_data.append([
            str(row["Material"]),
            str(row["Qtd.remessa"]),
            str(row["qtd_conferida"]),
        ])

    table = Table(table_data)
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.black)
    ]))

    story.append(table)

    # INSUMOS
    story.append(Spacer(1, 10))
    story.append(Paragraph("INSUMOS", st.markdown))
    for k, v in insumos.items():
        story.append(Paragraph(f"{k}: {v}", st.markdown))

    # BOC
    story.append(Spacer(1, 10))
    story.append(Paragraph("BOC", st.markdown))
    for b in boc:
        story.append(Paragraph(f"{b['item']} - {b['qtd']}", st.markdown))

    # ASSINATURA
    story.append(Spacer(1, 20))
    story.append(Paragraph("Conferente: ____________________", st.markdown))
    story.append(Paragraph("Líder: ____________________", st.markdown))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()

# =========================================================
# FIREBASE STORAGE
# =========================================================
def upload_pdf(pdf, dt):
    blob = bucket.blob(f"pdf/{dt}.pdf")
    blob.upload_from_string(pdf, content_type="application/pdf")
    blob.make_public()
    return blob.public_url

# =========================================================
# ASSISTENTE
# =========================================================
def page_assistente():
    st.title("Assistente")

    vl06 = st.file_uploader("Upload VL06", type=["xlsx"])
    if vl06:
        df = pd.read_excel(vl06)
        save_base(df, "vl06")
        st.success("VL06 carregada")

    sku = st.file_uploader("Upload Base SKU", type=["xlsx"])
    if sku:
        df = pd.read_excel(sku)
        save_base(df, "sku")
        st.success("SKU carregado")

# =========================================================
# CONFERÊNCIA
# =========================================================
def page_conferencia():
    st.title("Conferência")

    base = load_base("vl06")
    sku = load_base("sku")

    dt = st.text_input("Pesquisar DT")

    if not dt:
        return

    df = base[base["Nº transporte"].astype(str) == dt].copy()

    if df.empty:
        st.warning("DT não encontrada")
        return

    df["qtd_conferida"] = df.get("qtd_conferida", 0)

    sku_map = dict(zip(sku.iloc[:,0], sku.iloc[:,1]))

    # HO
    st.subheader("Palete (HO)")
    cod = st.text_input("SKU")
    if st.button("Lançar HO"):
        if cod in sku_map:
            df.loc[df["Material"] == cod, "qtd_conferida"] += sku_map[cod]

    # HE
    st.subheader("Fracionado (HE)")
    qtd = st.number_input("Qtd HE", 1)
    if st.button("Lançar HE"):
        df.loc[df["Material"] == cod, "qtd_conferida"] += qtd

    st.dataframe(df)

    # INSUMOS
    st.subheader("Insumos CP")
    palete = st.number_input("Palete", 0)
    chapa = st.number_input("Chapa", 0)

    # BOC
    st.subheader("BOC")
    item_boc = st.text_input("Item")
    qtd_boc = st.number_input("Qtd BOC", 0)

    if st.button("Salvar BOC"):
        db.collection("boc").add({
            "dt": dt,
            "item": item_boc,
            "qtd": qtd_boc,
            "user": st.session_state["user"],
            "data": now()
        })

    if st.button("Finalizar"):
        insumos = {
            "palete": palete,
            "chapa": chapa
        }

        boc = [d.to_dict() for d in db.collection("boc").where("dt","==",dt).stream()]

        pdf = gerar_pdf(dt, df, insumos, boc)
        url = upload_pdf(pdf, dt)

        db.collection("dts").document(dt).set({
            "pdf": url,
            "status": "FINALIZADO",
            "data": now()
        })

        st.success("DT finalizada")

# =========================================================
# GESTÃO
# =========================================================
def page_gestao():
    st.title("Gestão")

    docs = db.collection("dts").stream()
    data = [d.to_dict() for d in docs]

    df = pd.DataFrame(data)

    if df.empty:
        return

    st.metric("DTs", len(df))

# =========================================================
# FATURISTA
# =========================================================
def page_faturista():
    st.title("Faturamento")

    docs = db.collection("dts").stream()

    for d in docs:
        data = d.to_dict()
        st.write(f"DT: {d.id}")
        st.link_button("Abrir PDF", data.get("pdf",""))

# =========================================================
# MAIN
# =========================================================
if "perfil" not in st.session_state:
    login()
    st.stop()

menu = {
    "assistente": ["Assistente","Conferência"],
    "conferente": ["Conferência"],
    "gestao": ["Gestão"],
    "faturista": ["Faturista"]
}

op = st.sidebar.radio("Menu", menu[st.session_state["perfil"]])

if op == "Assistente":
    page_assistente()

if op == "Conferência":
    page_conferencia()

if op == "Gestão":
    page_gestao()

if op == "Faturista":
    page_faturista()
