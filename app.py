import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from reportlab.platypus import SimpleDocTemplate, Table
import smtplib
from email.message import EmailMessage

EMAIL = "seu@email.com"
SENHA = "SUA_SENHA"
DESTINO = "destino@email.com"

st.set_page_config(layout="wide")
st.title("📦 Conferência de Carga")

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

    # SKU
    sku_df = pd.read_sql("SELECT * FROM cadastro_sku", conn)
    mapa = dict(zip(sku_df["sku"], sku_df["qtd_palete"]))

    edited = st.data_editor(df, use_container_width=True)

    # BIP
    st.subheader("📷 Bip")
    codigo = st.text_input("SKU Bip")

    if codigo:
        if codigo in mapa:
            for i, row in edited.iterrows():
                if str(row["material"]) == codigo:
                    edited.at[i, "Qtd Conferida"] += mapa[codigo]
        st.rerun()

    # MANUAL
    st.subheader("✍️ Manual")
    sku = st.text_input("SKU manual")
    qtd = st.number_input("Qtd", min_value=1)

    if st.button("Adicionar"):
        for i, row in edited.iterrows():
            if str(row["material"]) == sku:
                edited.at[i, "Qtd Conferida"] += qtd
        st.rerun()

    # STATUS
    def status(row):
        if row["Qtd Conferida"] == row["qtd_solicitada"]:
            return "OK"
        elif row["Qtd Conferida"] == 0:
            return "PENDENTE"
        return "DIVERGENTE"

    edited["status"] = edited.apply(status, axis=1)

    st.dataframe(edited)

    # PDF
    def gerar_pdf(df):
        file = "conferencia.pdf"
        doc = SimpleDocTemplate(file)
        tabela = Table([df.columns.tolist()] + df.values.tolist())
        doc.build([tabela])
        return file

    # EMAIL
    def enviar(pdf):
        msg = EmailMessage()
        msg['Subject'] = f"Conferência DT {dt}"
        msg['From'] = EMAIL
        msg['To'] = DESTINO

        with open(pdf, "rb") as f:
            msg.add_attachment(f.read(), maintype='application', subtype='pdf', filename="conf.pdf")

        with smtplib.SMTP('smtp.office365.com', 587) as smtp:
            smtp.starttls()
            smtp.login(EMAIL, SENHA)
            smtp.send_message(msg)

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Finalizar OK"):
            if "DIVERGENTE" in edited["status"].values:
                st.error("Existe divergência")
            else:
                pdf = gerar_pdf(edited)
                enviar(pdf)

                for _, row in edited.iterrows():
                    conn.execute("""
                    UPDATE cargas SET qtd_conferida=?, status='FINALIZADO', fim=?
                    WHERE dt=? AND material=?
                    """, (
                        int(row["Qtd Conferida"]),
                        str(datetime.now()),
                        dt,
                        row["material"]
                    ))

                conn.commit()
                st.success("Finalizado OK")

    with col2:
        if st.button("Finalizar Divergente"):
            pdf = gerar_pdf(edited)
            enviar(pdf)

            for _, row in edited.iterrows():
                conn.execute("""
                UPDATE cargas SET qtd_conferida=?, status='DIVERGENTE', fim=?
                WHERE dt=? AND material=?
                """, (
                    int(row["Qtd Conferida"]),
                    str(datetime.now()),
                    dt,
                    row["material"]
                ))

            conn.commit()
            st.warning("Finalizado com divergência")

    conn.close()