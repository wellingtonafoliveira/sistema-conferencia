# ==========================================
# SISTEMA COMPLETO FINAL - VERSÃO PROFISSIONAL
# ==========================================

import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# ==========================================
# CONFIG
# ==========================================
st.set_page_config(layout="wide")
DATA_FILE = "data_store.json"
APP_TZ = ZoneInfo("America/Sao_Paulo")

# ==========================================
# STORAGE
# ==========================================
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"conferencias": {}, "insumos": [], "boc": []}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

# ==========================================
# LOGIN
# ==========================================
def login():
    st.title("Login")

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
        st.error("Login inválido")

# ==========================================
# PERMISSÕES
# ==========================================
def menu():
    perfil = st.session_state["perfil"]

    if perfil == "assistente":
        return ["Assistente","Conferência","Insumos","BOC"]

    if perfil == "conferente":
        return ["Conferência","Insumos","BOC"]

    if perfil == "gestao":
        return ["Gestão"]

    if perfil == "faturista":
        return ["Faturista"]

    if perfil == "coletor":
        return ["Coletor"]

# ==========================================
# HELPERS
# ==========================================
def now():
    return datetime.now(APP_TZ).strftime("%d/%m/%Y %H:%M:%S")

# ==========================================
# CONFERENCIA
# ==========================================
def page_conferencia():
    st.title("Conferência")

    dt = st.text_input("DT")

    data = load_data()

    if dt not in data["conferencias"]:
        data["conferencias"][dt] = {
            "itens": [],
            "status":"PENDENTE",
            "inicio": now()
        }

    sku = st.text_input("SKU")
    qtd = st.number_input("Quantidade", min_value=1)

    if st.button("Lançar"):
        data["conferencias"][dt]["itens"].append({
            "sku": sku,
            "qtd": qtd
        })
        save_data(data)
        st.success("Lançado")

    st.json(data["conferencias"][dt])

# ==========================================
# INSUMOS
# ==========================================
def page_insumos():
    st.title("Insumos CP")

    dt = st.text_input("DT")

    data = load_data()

    palete = st.number_input("Palete")
    chapa = st.number_input("Chapa")

    if st.button("Salvar"):
        data["insumos"] = [i for i in data["insumos"] if i["dt"] != dt]

        data["insumos"].append({
            "dt": dt,
            "palete": palete,
            "chapa": chapa,
            "data": now()
        })

        save_data(data)
        st.success("Salvo")

# ==========================================
# BOC
# ==========================================
def page_boc():
    st.title("BOC")

    dt = st.text_input("DT")
    item = st.text_input("Item")
    qtd = st.number_input("Qtd")

    data = load_data()

    if st.button("Salvar"):
        data["boc"].append({
            "dt": dt,
            "item": item,
            "qtd": qtd,
            "usuario": st.session_state["user"],
            "data": now()
        })
        save_data(data)
        st.success("BOC salvo")

    df = pd.DataFrame(data["boc"])
    if not df.empty:
        st.dataframe(df)

        if st.session_state["perfil"] == "assistente":
            idx = st.number_input("Índice para excluir", step=1)

            motivo = st.text_input("Motivo")

            if st.button("Excluir BOC"):
                if motivo:
                    data["boc"].pop(int(idx))
                    save_data(data)
                    st.success("Excluído")
                else:
                    st.error("Informe motivo")

# ==========================================
# FATURISTA
# ==========================================
def page_faturista():
    st.title("Faturista")

    dt = st.text_input("DT")

    data = load_data()

    st.subheader("Conferência")
    st.json(data["conferencias"].get(dt, {}))

    st.subheader("Insumos")
    st.write([i for i in data["insumos"] if i["dt"]==dt])

    st.subheader("BOC")
    st.write([b for b in data["boc"] if b["dt"]==dt])

# ==========================================
# COLETOR
# ==========================================
def page_coletor():
    st.title("Coletor")

    dt = st.text_input("DT")

    sku = st.text_input("Bipar SKU")
    qtd = st.number_input("Qtd", min_value=1)

    data = load_data()

    if st.button("Baixar"):
        if dt not in data["conferencias"]:
            data["conferencias"][dt] = {"itens":[]}

        data["conferencias"][dt]["itens"].append({
            "sku": sku,
            "qtd": qtd
        })

        save_data(data)
        st.success("OK")

# ==========================================
# MAIN
# ==========================================
if "user" not in st.session_state:
    login()
    st.stop()

menu_sel = st.sidebar.radio("Menu", menu())

if menu_sel == "Conferência":
    page_conferencia()

elif menu_sel == "Insumos":
    page_insumos()

elif menu_sel == "BOC":
    page_boc()

elif menu_sel == "Faturista":
    page_faturista()

elif menu_sel == "Coletor":
    page_coletor()

elif menu_sel == "Gestão":
    st.title("Gestão (em evolução)")
