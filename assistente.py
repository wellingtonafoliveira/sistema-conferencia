import streamlit as st
import pandas as pd
import sqlite3

st.title("📤 Upload VL06")

arquivo = st.file_uploader("Enviar VL06", type=["xlsx"])

if arquivo:
    df = pd.read_excel(arquivo)

    conn = sqlite3.connect("banco.db")

    for _, row in df.iterrows():
        conn.execute("""
        INSERT INTO cargas (dt, remessa, material, descricao, qtd_solicitada)
        VALUES (?, ?, ?, ?, ?)
        """, (
            str(row["Nº transporte"]),
            str(row["Remessa"]),
            str(row["Material"]),
            str(row["Denominação de item"]),
            int(row["Qtd.remessa"])
        ))

    conn.commit()
    conn.close()

    st.success("✅ VL06 carregada com sucesso!")