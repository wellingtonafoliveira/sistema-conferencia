import streamlit as st
import pandas as pd
import sqlite3

st.set_page_config(layout="wide")
st.title("📤 Carregar VL06")

arquivo = st.file_uploader("Selecione o arquivo VL06", type=["xlsx"])

if arquivo:

    df = pd.read_excel(arquivo)

    conn = sqlite3.connect("banco.db")

    registros = 0

    for _, row in df.iterrows():

        try:
            dt = str(row["Nº transporte"]).strip()
            remessa = str(row["Remessa"]).strip()
            material = str(row["Material"]).strip()
            descricao = str(row["Denominação de item"]).strip()
            cliente = str(row["Nome do emissor da ordem"]).strip()
            perfil = str(row["Nome agente de frete"]).strip()

            # 🔧 TRATAR QUANTIDADE
            qtd = row["Qtd.remessa"]

            if pd.isna(qtd):
                qtd = 0
            else:
                qtd = str(qtd).replace(".", "").replace(",", ".")
                qtd = int(float(qtd))

            if not dt or not material:
                continue

            conn.execute("""
            INSERT INTO cargas (
                dt, remessa, material, descricao, qtd_solicitada,
                cliente, perfil
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (dt, remessa, material, descricao, qtd, cliente, perfil))

            registros += 1

        except:
            continue

    conn.commit()
    conn.close()

    st.success(f"✅ {registros} registros inseridos!")
