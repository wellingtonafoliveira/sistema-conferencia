import streamlit as st
import pandas as pd
import sqlite3

st.set_page_config(layout="wide")
st.title("📤 Carregar VL06")

arquivo = st.file_uploader("Selecione o arquivo VL06", type=["xlsx"])

if arquivo:

    try:
        df = pd.read_excel(arquivo)

        st.success("Arquivo carregado com sucesso!")

        # 🔍 MOSTRAR COLUNAS (AJUDA DEBUG)
        st.subheader("Colunas encontradas no arquivo:")
        st.write(df.columns)

        # 🔗 CONEXÃO BANCO
        conn = sqlite3.connect("banco.db")

        registros_inseridos = 0

        for _, row in df.iterrows():

            # 🔒 TRATAR CAMPOS COM SEGURANÇA
            try:
                dt = str(row.get("Nº transporte", "")).strip()
                remessa = str(row.get("Remessa", "")).strip()
                material = str(row.get("Material", "")).strip()
                descricao = str(row.get("Denominação de item", "")).strip()

                # 🔧 TRATAMENTO DA QUANTIDADE (VL06 PROBLEMÁTICO)
                qtd = row.get("Qtd.remessa", 0)

                if pd.isna(qtd):
                    qtd = 0
                else:
                    qtd = str(qtd).replace(".", "").replace(",", ".")
                    qtd = int(float(qtd))

                # 🚫 IGNORA LINHAS VAZIAS
                if not dt or not material:
                    continue

                # 💾 INSERIR NO BANCO
                conn.execute("""
                INSERT INTO cargas (
                    dt, remessa, material, descricao, qtd_solicitada
                ) VALUES (?, ?, ?, ?, ?)
                """, (dt, remessa, material, descricao, qtd))

                registros_inseridos += 1

            except Exception as e:
                st.warning(f"Erro em uma linha ignorada: {e}")
                continue

        conn.commit()
        conn.close()

        st.success(f"✅ {registros_inseridos} registros inseridos com sucesso!")

    except Exception as e:
        st.error(f"Erro ao processar o arquivo: {e}")
