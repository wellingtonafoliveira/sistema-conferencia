# =========================================================
# SISTEMA COMPLETO SEM FIREBASE (LOCAL)
# =========================================================

import os
import json
from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors

st.set_page_config(layout="wide")

# =========================================================
# ARQUIVOS LOCAIS
# =========================================================
DATA_FILE = "dados.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"dts": {}, "boc": {}}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

data = load_data()

# =========================================================
# HELPERS
# =========================================================
def now():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

# =========================================================
# LOGIN SIMPLES
# =========================================================
users = {
    "conf": ("123", "conferente"),
    "gestor": ("123", "gestao"),
    "fat": ("123", "faturista"),
    "assist": ("123", "assistente"),
}

if "user" not in st.session_state:
    st.title("Login")
    u = st.text_input("Usuário")
    p = st.text_input("Senha", type="password")

    if st.button("Entrar"):
        if u in users and users[u][0] == p:
            st.session_state["user"] = u
            st.session_state["perfil"] = users[u][1]
            st.rerun()

    st.stop()

# =========================================================
# PDF
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
            str(row["Qtd"]),
            str(row["Conf"])
        ])

    table = Table(table_data)
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.black)
    ]))

    story.append(table)

    story.append(Spacer(1,10))
    story.append(Paragraph("INSUMOS", st.markdown))
    for k,v in insumos.items():
        story.append(Paragraph(f"{k}: {v}", st.markdown))

    story.append(Spacer(1,10))
    story.append(Paragraph("BOC", st.markdown))
    for b in boc:
        story.append(Paragraph(f"{b['item']} - {b['qtd']}", st.markdown))

    story.append(Spacer(1,20))
    story.append(Paragraph("Conferente: ____________________", st.markdown))
    story.append(Paragraph("Lider: ____________________", st.markdown))

    doc.build(story)
    buffer.seek(0)
    return buffer

# =========================================================
# ASSISTENTE (UPLOAD)
# =========================================================
def page_assistente():
    st.title("Assistente")

    vl06 = st.file_uploader("VL06", type=["xlsx"])
    if vl06:
        df = pd.read_excel(vl06)
        df.to_csv("vl06.csv", index=False)
        st.success("VL06 salva")

    sku = st.file_uploader("Base SKU", type=["xlsx"])
    if sku:
        df = pd.read_excel(sku)
        df.to_csv("sku.csv", index=False)
        st.success("SKU salva")

# =========================================================
# CONFERÊNCIA
# =========================================================
def page_conferencia():
    st.title("Conferência")

    if not os.path.exists("vl06.csv") or not os.path.exists("sku.csv"):
        st.warning("Suba VL06 e SKU primeiro")
        return

    base = pd.read_csv("vl06.csv")
    sku = pd.read_csv("sku.csv")

    dt = st.text_input("Pesquisar DT")

    if not dt:
        return

    df = base[base["Nº transporte"].astype(str) == dt].copy()

    if df.empty:
        st.warning("DT não encontrada")
        return

    if dt not in data["dts"]:
        data["dts"][dt] = {"itens": {}, "status": "ABERTO"}

    for i, row in df.iterrows():
        cod = str(row["Material"])
        if cod not in data["dts"][dt]["itens"]:
            data["dts"][dt]["itens"][cod] = 0

    # HO
    st.subheader("HO")
    cod = st.text_input("SKU")
    if st.button("Lançar HO"):
        qtd_palete = int(sku.iloc[0,1])  # simplificado
        data["dts"][dt]["itens"][cod] += qtd_palete

    # HE
    st.subheader("HE")
    qtd = st.number_input("Qtd HE", 1)
    if st.button("Lançar HE"):
        data["dts"][dt]["itens"][cod] += qtd

    # tabela
    tabela = []
    for cod, qtd_conf in data["dts"][dt]["itens"].items():
        tabela.append({"Material": cod, "Qtd": 0, "Conf": qtd_conf})

    df_view = pd.DataFrame(tabela)
    st.dataframe(df_view)

    # INSUMOS
    st.subheader("Insumos")
    palete = st.number_input("Palete", 0)
    chapa = st.number_input("Chapa", 0)

    # BOC
    st.subheader("BOC")
    item = st.text_input("Item BOC")
    qtd_boc = st.number_input("Qtd BOC", 0)

    if st.button("Salvar BOC"):
        data["boc"].setdefault(dt, []).append({
            "item": item,
            "qtd": qtd_boc
        })

    if st.button("Finalizar"):
        insumos = {"palete": palete, "chapa": chapa}
        boc = data["boc"].get(dt, [])

        pdf = gerar_pdf(dt, df_view, insumos, boc)

        with open(f"{dt}.pdf", "wb") as f:
            f.write(pdf.read())

        data["dts"][dt]["status"] = "FINALIZADO"
        save_data(data)

        st.success("DT finalizada")

# =========================================================
# GESTÃO
# =========================================================
def page_gestao():
    st.title("Gestão")

    total = len(data["dts"])
    st.metric("DTs", total)

# =========================================================
# FATURISTA
# =========================================================
def page_faturista():
    st.title("Faturamento")

    for dt in data["dts"]:
        if os.path.exists(f"{dt}.pdf"):
            st.download_button(
                f"Baixar {dt}",
                open(f"{dt}.pdf", "rb"),
                file_name=f"{dt}.pdf"
            )

# =========================================================
# MENU
# =========================================================
menu = {
    "assistente": ["Assistente", "Conferência"],
    "conferente": ["Conferência"],
    "gestao": ["Gestão"],
    "faturista": ["Faturamento"]
}

op = st.sidebar.radio("Menu", menu[st.session_state["perfil"]])

if op == "Assistente":
    page_assistente()

if op == "Conferência":
    page_conferencia()

if op == "Gestão":
    page_gestao()

if op == "Faturamento":
    page_faturista()
