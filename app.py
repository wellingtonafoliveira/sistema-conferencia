# IMPORTS
import streamlit as st
import pandas as pd
import json
import os
import base64
import pytz
from datetime import datetime
import plotly.express as px
import resend

# CONFIG
st.set_page_config(layout="wide")
LOGO = "Nadir_Branco_Laranja.png"

# TIMEZONE
def agora_br():
    tz = pytz.timezone("America/Sao_Paulo")
    return datetime.now(tz)

def saudacao(nome):
    hora = agora_br().hour
    if hora < 12:
        return f"Bom dia, {nome} 👋"
    elif hora < 18:
        return f"Boa tarde, {nome} 👋"
    else:
        return f"Boa noite, {nome} 👋"

# DATABASE LOCAL JSON
DATA_FILE = "data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    return json.load(open(DATA_FILE, "r"))

def save_data(data):
    json.dump(data, open(DATA_FILE, "w"), indent=2)

# LOGIN
def login():
    st.image(LOGO, width=200)
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

# DASHBOARD PRODUTIVIDADE
def produtividade(df):
    df["horas"] = (pd.to_datetime(df["fim"]) - pd.to_datetime(df["inicio"])).dt.total_seconds()/3600
    resumo = df.groupby("conferente").agg({
        "dt":"count",
        "caixas":"sum",
        "horas":"sum"
    }).reset_index()

    resumo["caixas_hora"] = resumo["caixas"]/resumo["horas"]
    resumo = resumo.sort_values("caixas_hora", ascending=False)
    resumo["ranking"] = range(1, len(resumo)+1)

    return resumo

# MAIN
if "user" not in st.session_state:
    login()
    st.stop()

st.sidebar.image(LOGO)
st.sidebar.write(saudacao(st.session_state["user"]))
menu = st.sidebar.selectbox("Menu", ["Conferência","Gestão"])

data = load_data()

if menu == "Conferência":
    st.image(LOGO, width=200)
    st.write(saudacao(st.session_state["user"]))

    dt = st.text_input("Digite a DT")

    if st.button("Iniciar"):
        data[dt] = {
            "inicio": agora_br().strftime("%d/%m/%Y %H:%M:%S"),
            "fim": "",
            "conferente": st.session_state["user"],
            "caixas": 0
        }
        save_data(data)

    if dt in data:
        st.write(data[dt])

        caixas = st.number_input("Adicionar caixas", 0)

        if st.button("Adicionar"):
            data[dt]["caixas"] += caixas
            save_data(data)

        if st.button("Finalizar"):
            data[dt]["fim"] = agora_br().strftime("%d/%m/%Y %H:%M:%S")
            save_data(data)

if menu == "Gestão":
    st.image(LOGO, width=200)
    st.write(saudacao(st.session_state["user"]))

    df = pd.DataFrame(data).T.reset_index().rename(columns={"index":"dt"})

    if not df.empty:
        st.dataframe(df)

        prod = produtividade(df)

        st.subheader("Ranking")
        st.dataframe(prod)

        fig = px.bar(prod, x="conferente", y="caixas_hora")
        st.plotly_chart(fig)
