import streamlit as st
import pandas as pd
import sqlite3

st.title("📊 Gestão de Cargas")

conn = sqlite3.connect("banco.db")
df = pd.read_sql("SELECT * FROM cargas", conn)

st.metric("Total", len(df))
st.metric("Pendentes", len(df[df["status"]=="PENDENTE"]))
st.metric("OK", len(df[df["status"]=="OK"]))
st.metric("Divergente", len(df[df["status"]=="DIVERGENTE"]))

st.dataframe(df)

st.subheader("🔄 Reabrir DT")

dt = st.text_input("DT")

if st.button("Reabrir"):

    df_dt = pd.read_sql(f"SELECT * FROM cargas WHERE dt='{dt}'", conn)

    if df_dt.empty:
        st.error("DT não encontrada")
    else:
        if "OK" in df_dt["status"].values:
            st.error("Não pode reabrir conferência sem divergência")
        else:
            conn.execute(f"UPDATE cargas SET status='PENDENTE' WHERE dt='{dt}'")
            conn.commit()
            st.success("Reaberta com sucesso")

conn.close()
