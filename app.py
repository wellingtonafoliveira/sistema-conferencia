# =========================================================
# SISTEMA COMPLETO FINAL (SEM FIREBASE)
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
# ARQUIVOS
# =========================================================
DB_FILE = "database.json"
PDF_DIR = "pdfs"

os.makedirs(PDF_DIR, exist_ok=True)

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {"dts": {}, "boc": {}, "insumos": {}}

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

db = load_db()

# =========================================================
# HELPERS
# =========================================================
def now():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

# =========================================================
# LOGIN
# =========================================================
users = {
    "conf": ("123", "conferente"),
    "gestor": ("123", "gestao"),
    "fat": ("123", "faturista"),
    "assist": ("123", "assistente"),
    "coletor": ("123", "conferente"),
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

    story.append(Paragraph(f"ESPELHO DE CONFERÊNCIA - DT {dt}", st.markdown))
    story.append(Spacer(1, 10))

    data_table = [["SKU", "Solicitado", "Conferido", "Status"]]

    for _, row in df.iterrows():
        status = "OK" if row["sol"] == row["conf"] else "DIVERGENTE"
        data_table.append([row["sku"], row["sol"], row["conf"], status])

    table = Table(data_table)
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.black)
    ]))

    story.append(table)

    # INSUMOS
    story.append(Spacer(1, 15))
    story.append(Paragraph("INSUMOS", st.markdown))

    for k,v in insumos.items():
        story.append(Paragraph(f"{k}: {v}", st.markdown))

    # BOC
    story.append(Spacer(1, 10))
    story.append(Paragraph("BOC", st.markdown))

    for b in boc:
        story.append(Paragraph(f"{b['item']} - {b['qtd']}", st.markdown))

    # ASSINATURA
    story.append(Spacer(1, 20))
    story.append(Paragraph("Conferente: ____________________", st.markdown))
    story.append(Paragraph("Lider: ____________________", st.markdown))

    doc.build(story)
    buffer.seek(0)

    path = f"{PDF_DIR}/{dt}.pdf"
    with open(path, "wb") as f:
        f.write(buffer.read())

    return path

# =========================================================
# ASSISTENTE
# =========================================================
def page_assistente():

    st.title("Assistente")

    vl06 = st.file_uploader("Upload VL06", type=["xlsx"])
    if vl06:
        df = pd.read_excel(vl06)
        df.to_csv("vl06.csv", index=False)
        st.success("VL06 carregada")

    sku = st.file_uploader("Upload SKU", type=["xlsx"])
    if sku:
        df = pd.read_excel(sku)
        df.to_csv("sku.csv", index=False)
        st.success("SKU carregado")

# =========================================================
# CONFERÊNCIA
# =========================================================
def page_conferencia():

    st.title("Conferência")

    if not os.path.exists("vl06.csv") or not os.path.exists("sku.csv"):
        st.warning("Carregue VL06 e SKU")
        return

    base = pd.read_csv("vl06.csv")
    sku_df = pd.read_csv("sku.csv")

    sku_map = dict(zip(sku_df.iloc[:,0], sku_df.iloc[:,1]))

    dt = st.text_input("Pesquisar DT")

    if not dt:
        return

    df = base[base["Nº transporte"].astype(str) == dt]

    if df.empty:
        st.warning("DT não encontrada")
        return

    if dt not in db["dts"]:
        db["dts"][dt] = {"itens": {}, "status": "ABERTO"}

    # inicializar itens
    for _, row in df.iterrows():
        cod = str(row["Material"])
        if cod not in db["dts"][dt]["itens"]:
            db["dts"][dt]["itens"][cod] = {
                "sol": int(row["Qtd.remessa"]),
                "conf": 0
            }

    # HO
    st.subheader("HO (Palete)")
    sku = st.text_input("SKU")

    if st.button("Lançar HO"):
        if sku in sku_map:
            db["dts"][dt]["itens"][sku]["conf"] += int(sku_map[sku])

    # HE
    st.subheader("HE (Fracionado)")
    qtd = st.number_input("Qtd HE", 1)

    if st.button("Lançar HE"):
        if sku in db["dts"][dt]["itens"]:
            db["dts"][dt]["itens"][sku]["conf"] += qtd

    # tabela
    tabela = []
    for k,v in db["dts"][dt]["itens"].items():
        tabela.append({"sku": k, "sol": v["sol"], "conf": v["conf"]})

    df_view = pd.DataFrame(tabela)
    st.dataframe(df_view)

    # INSUMOS
    st.subheader("Insumos CP")

    palete = st.number_input("Palete", 0)
    chapa = st.number_input("Chapa", 0)

    db["insumos"][dt] = {
        "palete": palete,
        "chapa": chapa
    }

    # BOC
    st.subheader("BOC")

    item = st.text_input("Item BOC")
    qtd_boc = st.number_input("Qtd BOC", 0)

    if st.button("Salvar BOC"):
        db["boc"].setdefault(dt, []).append({
            "item": item,
            "qtd": qtd_boc
        })

    if st.button("Finalizar Conferência"):

        insumos = db["insumos"].get(dt, {})
        boc = db["boc"].get(dt, [])

        pdf_path = gerar_pdf(dt, df_view, insumos, boc)

        db["dts"][dt]["status"] = "FINALIZADO"
        db["dts"][dt]["pdf"] = pdf_path

        save_db(db)

        st.success("DT finalizada")

# =========================================================
# GESTÃO
# =========================================================
def page_gestao():

    st.title("Dashboard")

    total = len(db["dts"])
    finalizadas = len([d for d in db["dts"].values() if d["status"] == "FINALIZADO"])

    st.metric("Total DT", total)
    st.metric("Finalizadas", finalizadas)

# =========================================================
# FATURISTA
# =========================================================
def page_faturista():

    st.title("Faturamento")

    for dt, info in db["dts"].items():
        if "pdf" in info:
            with open(info["pdf"], "rb") as f:
                st.download_button(
                    f"Baixar {dt}",
                    f,
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
