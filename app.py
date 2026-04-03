import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime

st.set_page_config(layout="wide")
st.title("📦 Conferência")

dt = st.text_input("Digite a DT")

if dt:

    conn = sqlite3.connect("banco.db")
    df = pd.read_sql(f"SELECT * FROM cargas WHERE dt='{dt}'", conn)

    if df.empty:
        st.warning("DT não encontrada")
        st.stop()

    # BLOQUEIO
    if "FINALIZADO" in df["status"].values:
        st.error("🚫 DT já finalizada")
        st.stop()

    df["Qtd Conferida"] = df["qtd_conferida"]

    # MOSTRAR INFO DA CARGA
    st.subheader("📄 Dados da Carga")
    st.write("Cliente:", df["cliente"].iloc[0])
    st.write("Perfil:", df["perfil"].iloc[0])

    edited = st.data_editor(df, use_container_width=True)

    # STATUS
    def status(row):
        if row["Qtd Conferida"] == row["qtd_solicitada"]:
            return "OK"
        elif row["Qtd Conferida"] == 0:
            return "PENDENTE"
        return "DIVERGENTE"

    edited["status"] = edited.apply(status, axis=1)

    st.dataframe(edited)

    if st.button("Finalizar Conferência"):

        for _, row in edited.iterrows():
            conn.execute("""
            UPDATE cargas
            SET qtd_conferida=?, status=?, fim=?
            WHERE dt=? AND material=?
            """, (
                int(row["Qtd Conferida"]),
                row["status"],
                str(datetime.now()),
                dt,
                row["material"]
            ))

        conn.commit()
        conn.close()

        st.success("Conferência finalizada!")
