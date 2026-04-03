import streamlit as st
import pandas as pd
import sqlite3

st.set_page_config(layout="wide")
st.title("📤 Carregar VL06")

arquivo = st.file_uploader("Selecione o arquivo VL06", type=["xlsx"])

if arquivo:
    try:
        df = pd.read_excel(arquivo)

        # Limpa espaços extras nos nomes das colunas
        df.columns = df.columns.str.strip()

        st.success("Arquivo carregado com sucesso!")

        st.subheader("Colunas encontradas no arquivo")
        st.write(df.columns.tolist())

        conn = sqlite3.connect("banco.db")

        registros = 0

        for _, row in df.iterrows():
            try:
                dt = str(row.get("Nº transporte", "")).strip()
                remessa = str(row.get("Remessa", "")).strip()
                material = str(row.get("Material", "")).strip()
                descricao = str(row.get("Denominação de item", "")).strip()
                cliente = str(row.get("Nome do emissor da ordem", "")).strip()
                perfil = str(row.get("Nome agente de frete", "")).strip()

                qtd = row.get("Qtd.remessa", 0)

                if pd.isna(qtd):
                    qtd = 0
                else:
                    qtd = str(qtd).strip().replace(".", "").replace(",", ".")
                    qtd = int(float(qtd))

                # Ignora linhas sem DT ou Material
                if not dt or dt.lower() == "nan" or not material or material.lower() == "nan":
                    continue

                conn.execute("""
                    INSERT INTO cargas (
                        dt,
                        remessa,
                        material,
                        descricao,
                        qtd_solicitada,
                        cliente,
                        perfil
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    dt,
                    remessa,
                    material,
                    descricao,
                    qtd,
                    cliente,
                    perfil
                ))

                registros += 1

            except Exception as e:
                st.warning(f"Linha ignorada por erro: {e}")
                continue

        conn.commit()
        conn.close()

        st.success(f"✅ {registros} registros inseridos!")

    except Exception as e:
        st.error(f"Erro ao processar o arquivo: {e}")
