import io
import json
import smtplib
from copy import deepcopy
from datetime import datetime
from email.message import EmailMessage

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


st.set_page_config(layout="wide", page_title="Conferência VL06")

# =========================
# CONFIG E-MAIL
# =========================
EMAIL_REMETENTE = "wellington.feitosa@nadir.com.br"
EMAIL_SENHA = "SUA_SENHA_OU_SENHA_DE_APP"
EMAIL_DESTINO = "wellington.feitosa@nadir.com.br"
SMTP_SERVER = "smtp.office365.com"
SMTP_PORT = 587

REQUIRED_VL06_COLUMNS = [
    "Nº transporte",
    "Remessa",
    "Documento referência",
    "Material",
    "Nome agente de frete",
    "Denominação de item",
    "Qtd.remessa",
    "Nome do emissor da ordem",
    "Peso total",
    "Peso líquido",
    "Volume",
]


# =========================
# ESTADO
# =========================
def init_state():
    defaults = {
        "base_vl06": None,
        "sku_base": None,
        "conferencias": {},
        "last_pdf_bytes": None,
        "last_pdf_name": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# =========================
# FUNÇÕES UTILITÁRIAS
# =========================
def clean_str(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def clean_id(value):
    text = clean_str(value)
    if not text:
        return ""
    try:
        num = float(text.replace(",", "."))
        if num.is_integer():
            return str(int(num))
    except Exception:
        pass
    return text


def to_int_qty(value):
    if pd.isna(value):
        return 0
    text = str(value).strip()
    if not text:
        return 0
    text = text.replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return int(float(text))
    except Exception:
        return 0


# =========================
# NORMALIZAÇÃO DA VL06
# =========================
def normalize_vl06(df_raw: pd.DataFrame) -> pd.DataFrame:
    df = df_raw.copy()
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_VL06_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Colunas ausentes na VL06: {missing}")

    data = pd.DataFrame({
        "dt": df["Nº transporte"].apply(clean_id),
        "remessa": df["Remessa"].apply(clean_id),
        "doc_referencia": df["Documento referência"].apply(clean_id),
        "material": df["Material"].apply(clean_id),
        "transportadora": df["Nome agente de frete"].apply(clean_str),
        "descricao": df["Denominação de item"].apply(clean_str),
        "qtd_solicitada": df["Qtd.remessa"].apply(to_int_qty),
        "cliente": df["Nome do emissor da ordem"].apply(clean_str),
        "peso_total": df["Peso total"].apply(to_int_qty),
        "peso_liquido": df["Peso líquido"].apply(to_int_qty),
        "volume": df["Volume"].apply(to_int_qty),
    })

    # Ignora linhas sem DT, sem material e com quantidade 0
    data = data[
        (data["dt"] != "") &
        (data["material"] != "") &
        (data["qtd_solicitada"] > 0)
    ].copy()

    # Mantém remessas distintas dentro da mesma DT
    data = data.drop_duplicates(
        subset=["dt", "remessa", "doc_referencia", "material", "descricao", "qtd_solicitada"]
    ).reset_index(drop=True)

    data["qtd_conferida"] = 0
    data["status_item"] = "PENDENTE"
    data["status_dt"] = "PENDENTE"
    return data


def normalize_sku_base(df_raw: pd.DataFrame) -> pd.DataFrame:
    df = df_raw.copy()
    df.columns = [str(c).strip() for c in df.columns]

    lower_map = {c.lower(): c for c in df.columns}

    possible_sku = ["sku", "material", "codigo", "código", "item"]
    possible_desc = ["descricao", "descrição", "denominação", "descricao item"]
    possible_qtd = ["qtd_palete", "qtd por palete", "quantidade por palete", "quantidade", "qtd"]

    def find_col(possible):
        for p in possible:
            if p in lower_map:
                return lower_map[p]
        return None

    sku_col = find_col(possible_sku)
    desc_col = find_col(possible_desc)
    qtd_col = find_col(possible_qtd)

    if not sku_col or not qtd_col:
        raise ValueError("A base SKU precisa ter ao menos as colunas SKU e quantidade por palete.")

    out = pd.DataFrame({
        "sku": df[sku_col].apply(clean_id),
        "descricao": df[desc_col].apply(clean_str) if desc_col else "",
        "qtd_palete": df[qtd_col].apply(to_int_qty),
    })

    out = out[(out["sku"] != "") & (out["qtd_palete"] > 0)].drop_duplicates(subset=["sku"]).reset_index(drop=True)
    return out


# =========================
# STATUS
# =========================
def compute_item_status(qtd_conferida: int, qtd_solicitada: int) -> str:
    if qtd_conferida == 0:
        return "PENDENTE"
    if qtd_conferida == qtd_solicitada:
        return "OK"
    return "DIVERGENTE"


def apply_statuses(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["qtd_conferida"] = out["qtd_conferida"].fillna(0).astype(int)
    out["qtd_solicitada"] = out["qtd_solicitada"].fillna(0).astype(int)
    out["status_item"] = out.apply(
        lambda row: compute_item_status(int(row["qtd_conferida"]), int(row["qtd_solicitada"])),
        axis=1,
    )
    return out


# =========================
# SNAPSHOT
# =========================
def get_dt_list():
    base = st.session_state["base_vl06"]
    if base is None or base.empty:
        return []
    return sorted(base["dt"].dropna().astype(str).unique().tolist())


def get_dt_snapshot(dt: str):
    confs = st.session_state["conferencias"]
    if dt in confs:
        return confs[dt]

    base = st.session_state["base_vl06"]
    if base is None:
        return None

    df_dt = base[base["dt"] == dt].copy()
    if df_dt.empty:
        return None

    snapshot = {
        "meta": {
            "dt": dt,
            "conferente": "",
            "turno": "Manhã",
            "inicio": "",
            "fim": "",
            "status_dt": "PENDENTE",
            "cliente": clean_str(df_dt["cliente"].iloc[0]),
            "transportadora": clean_str(df_dt["transportadora"].iloc[0]),
            "qtd_remessas": int(df_dt["remessa"].nunique()),
        },
        "items": apply_statuses(df_dt).to_dict(orient="records"),
    }
    st.session_state["conferencias"][dt] = snapshot
    return snapshot


def save_dt_snapshot(dt: str, snapshot: dict):
    st.session_state["conferencias"][dt] = snapshot


def snapshot_to_df(snapshot: dict) -> pd.DataFrame:
    return pd.DataFrame(snapshot["items"])


def update_snapshot_items(dt: str, df_items: pd.DataFrame):
    snapshot = deepcopy(st.session_state["conferencias"][dt])
    snapshot["items"] = apply_statuses(df_items).to_dict(orient="records")
    save_dt_snapshot(dt, snapshot)


def dt_locked(snapshot: dict) -> bool:
    return snapshot["meta"].get("status_dt") == "FINALIZADO"


def dt_can_reopen(snapshot: dict) -> bool:
    return snapshot["meta"].get("status_dt") == "DIVERGENTE"


def mark_dt_started(dt: str, conferente: str, turno: str):
    snapshot = deepcopy(st.session_state["conferencias"][dt])
    if not snapshot["meta"].get("inicio"):
        snapshot["meta"]["inicio"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot["meta"]["conferente"] = conferente
    snapshot["meta"]["turno"] = turno
    if snapshot["meta"]["status_dt"] == "PENDENTE":
        snapshot["meta"]["status_dt"] = "EM_ANDAMENTO"
    save_dt_snapshot(dt, snapshot)


def finalize_dt(dt: str, final_status: str, conferente: str, turno: str):
    snapshot = deepcopy(st.session_state["conferencias"][dt])
    snapshot["meta"]["conferente"] = conferente
    snapshot["meta"]["turno"] = turno
    if not snapshot["meta"].get("inicio"):
        snapshot["meta"]["inicio"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot["meta"]["fim"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    snapshot["meta"]["status_dt"] = final_status
    save_dt_snapshot(dt, snapshot)


def reopen_dt(dt: str):
    snapshot = deepcopy(st.session_state["conferencias"][dt])
    if snapshot["meta"]["status_dt"] == "DIVERGENTE":
        snapshot["meta"]["status_dt"] = "EM_ANDAMENTO"
        snapshot["meta"]["fim"] = ""
        save_dt_snapshot(dt, snapshot)


# =========================
# GESTÃO
# =========================
def build_management_df():
    base = st.session_state["base_vl06"]
    if base is None or base.empty:
        return pd.DataFrame()

    rows = []
    for dt in get_dt_list():
        snapshot = get_dt_snapshot(dt)
        items_df = snapshot_to_df(snapshot)
        meta = snapshot["meta"]

        rows.append({
            "DT": dt,
            "Cliente": meta.get("cliente", ""),
            "Transportadora": meta.get("transportadora", ""),
            "Remessas": int(items_df["remessa"].nunique()) if not items_df.empty else 0,
            "Conferente": meta.get("conferente", ""),
            "Turno": meta.get("turno", ""),
            "Início": meta.get("inicio", ""),
            "Fim": meta.get("fim", ""),
            "Status DT": meta.get("status_dt", "PENDENTE"),
            "Itens": len(items_df),
            "OK": int((items_df["status_item"] == "OK").sum()) if not items_df.empty else 0,
            "Divergentes": int((items_df["status_item"] == "DIVERGENTE").sum()) if not items_df.empty else 0,
            "Pendentes": int((items_df["status_item"] == "PENDENTE").sum()) if not items_df.empty else 0,
        })

    return pd.DataFrame(rows)


# =========================
# PDF E E-MAIL
# =========================
def generate_pdf_bytes(snapshot: dict) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=18,
        rightMargin=18,
        topMargin=18,
        bottomMargin=18
    )

    styles = getSampleStyleSheet()
    story = []

    meta = snapshot["meta"]
    items_df = snapshot_to_df(snapshot).copy()
    items_df = items_df.sort_values(by=["remessa", "material"]).reset_index(drop=True)

    story.append(Paragraph(f"Espelho de Conferência - DT {meta.get('dt', '')}", styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Status: {meta.get('status_dt', '')}", styles["Normal"]))
    story.append(Paragraph(f"Cliente: {meta.get('cliente', '')}", styles["Normal"]))
    story.append(Paragraph(f"Transportadora: {meta.get('transportadora', '')}", styles["Normal"]))
    story.append(Paragraph(f"Conferente: {meta.get('conferente', '')}", styles["Normal"]))
    story.append(Paragraph(f"Turno: {meta.get('turno', '')}", styles["Normal"]))
    story.append(Paragraph(f"Início: {meta.get('inicio', '')}", styles["Normal"]))
    story.append(Paragraph(f"Fim: {meta.get('fim', '')}", styles["Normal"]))
    story.append(Paragraph(f"Quantidade de remessas: {int(items_df['remessa'].nunique())}", styles["Normal"]))
    story.append(Spacer(1, 10))

    table_data = [[
        "Remessa", "Doc. Ref.", "Material", "Descrição",
        "Qtd Solicitada", "Qtd Conferida", "Status"
    ]]

    for _, row in items_df.iterrows():
        table_data.append([
            str(row["remessa"]),
            str(row["doc_referencia"]),
            str(row["material"]),
            str(row["descricao"]),
            str(row["qtd_solicitada"]),
            str(row["qtd_conferida"]),
            str(row["status_item"]),
        ])

    table = Table(table_data, repeatRows=1, colWidths=[70, 80, 90, 280, 80, 80, 80])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E2F3")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 10),
    ]))

    story.append(table)
    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def send_pdf_email(pdf_bytes: bytes, filename: str, dt: str, status_dt: str):
    msg = EmailMessage()
    msg["Subject"] = f"Conferência DT {dt} - {status_dt}"
    msg["From"] = EMAIL_REMETENTE
    msg["To"] = EMAIL_DESTINO
    msg.set_content(
        f"Segue em anexo o PDF da conferência.\n\nDT: {dt}\nStatus: {status_dt}"
    )

    msg.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=filename
    )

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(EMAIL_REMETENTE, EMAIL_SENHA)
        smtp.send_message(msg)


# =========================
# SNAPSHOT JSON
# =========================
def export_snapshot_json() -> bytes:
    payload = {
        "base_vl06": st.session_state["base_vl06"].to_dict(orient="records") if st.session_state["base_vl06"] is not None else [],
        "sku_base": st.session_state["sku_base"].to_dict(orient="records") if st.session_state["sku_base"] is not None else [],
        "conferencias": st.session_state["conferencias"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def import_snapshot_json(file):
    payload = json.load(file)
    base_vl06 = pd.DataFrame(payload.get("base_vl06", []))
    sku_base = pd.DataFrame(payload.get("sku_base", []))
    conferencias = payload.get("conferencias", {})

    st.session_state["base_vl06"] = base_vl06 if not base_vl06.empty else None
    st.session_state["sku_base"] = sku_base if not sku_base.empty else None
    st.session_state["conferencias"] = conferencias


# =========================
# SIDEBAR
# =========================
def render_sidebar():
    st.sidebar.title("Menu")
    section = st.sidebar.radio("Acesso", ["Assistente", "Conferência", "Gestão"])

    st.sidebar.divider()
    st.sidebar.subheader("Backup do sistema")

    if st.session_state["base_vl06"] is not None:
        st.sidebar.download_button(
            "Baixar snapshot (.json)",
            data=export_snapshot_json(),
            file_name="snapshot_conferencia.json",
            mime="application/json",
        )

    imported = st.sidebar.file_uploader("Importar snapshot", type=["json"], key="snapshot_import")
    if imported is not None:
        import_snapshot_json(imported)
        st.sidebar.success("Snapshot importado.")

    return section


# =========================
# PÁGINAS
# =========================
def page_assistente():
    st.title("📤 Assistente de Logística")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Carregar VL06")
        vl06_file = st.file_uploader("Selecione o arquivo VL06", type=["xlsx", "xls"], key="vl06_file")
        replace = st.checkbox("Substituir base atual", value=True)

        if vl06_file is not None:
            try:
                raw = pd.read_excel(vl06_file)
                normalized = normalize_vl06(raw)

                if st.button("Processar VL06", key="process_vl06"):
                    if st.session_state["base_vl06"] is None or replace:
                        st.session_state["base_vl06"] = normalized.copy()
                        st.session_state["conferencias"] = {}
                    else:
                        combined = pd.concat([st.session_state["base_vl06"], normalized], ignore_index=True)
                        st.session_state["base_vl06"] = combined.drop_duplicates(
                            subset=["dt", "remessa", "doc_referencia", "material"]
                        ).reset_index(drop=True)
                        st.session_state["conferencias"] = {}

                    st.success(f"VL06 carregada com {len(normalized)} linhas válidas.")
                    st.dataframe(normalized.head(20), use_container_width=True)
            except Exception as e:
                st.error(f"Erro ao processar a VL06: {e}")

    with col2:
        st.subheader("Carregar base de SKU por palete")
        sku_file = st.file_uploader("Base SKU (xlsx/csv)", type=["xlsx", "xls", "csv"], key="sku_file")

        if sku_file is not None:
            try:
                if sku_file.name.lower().endswith(".csv"):
                    raw = pd.read_csv(sku_file)
                else:
                    raw = pd.read_excel(sku_file)
                normalized = normalize_sku_base(raw)

                if st.button("Processar base SKU", key="process_sku"):
                    st.session_state["sku_base"] = normalized
                    st.success(f"Base SKU carregada com {len(normalized)} itens.")
                    st.dataframe(normalized.head(20), use_container_width=True)
            except Exception as e:
                st.error(f"Erro ao processar base SKU: {e}")

    st.divider()

    if st.session_state["base_vl06"] is not None:
        base = st.session_state["base_vl06"]
        st.subheader("Resumo da base atual")
        a, b, c = st.columns(3)
        a.metric("Linhas válidas", len(base))
        b.metric("DTs", base["dt"].nunique())
        c.metric("Remessas", base["remessa"].nunique())
        st.dataframe(base.head(50), use_container_width=True)


def page_conferencia():
    st.title("📦 Conferência")

    if st.session_state["base_vl06"] is None or st.session_state["base_vl06"].empty:
        st.info("Primeiro carregue a VL06 na área do Assistente.")
        return

    dt_list = get_dt_list()
    search = st.text_input("Pesquisar DT")
    filtered = [d for d in dt_list if search.strip() in d] if search.strip() else dt_list

    if not filtered:
        st.warning("Nenhuma DT encontrada.")
        return

    dt = st.selectbox("Selecione a DT", filtered)
    snapshot = get_dt_snapshot(dt)
    meta = snapshot["meta"]

    top1, top2, top3, top4 = st.columns(4)
    top1.info(f"**Cliente**\n\n{meta.get('cliente', '')}")
    top2.info(f"**Transportadora**\n\n{meta.get('transportadora', '')}")
    top3.info(f"**Status DT**\n\n{meta.get('status_dt', 'PENDENTE')}")
    top4.info(f"**Remessas na DT**\n\n{meta.get('qtd_remessas', 0)}")

    c1, c2, c3, c4 = st.columns(4)
    conferente = c1.text_input("Conferente", value=meta.get("conferente", ""), key=f"conf_{dt}")
    turno = c2.selectbox("Turno", ["Manhã", "Tarde", "Noite"], index=["Manhã", "Tarde", "Noite"].index(meta.get("turno", "Manhã")), key=f"turno_{dt}")
    c3.text_input("Início", value=meta.get("inicio", ""), disabled=True, key=f"inicio_{dt}")
    c4.text_input("Fim", value=meta.get("fim", ""), disabled=True, key=f"fim_{dt}")

    snapshot["meta"]["conferente"] = conferente
    snapshot["meta"]["turno"] = turno
    save_dt_snapshot(dt, snapshot)

    if dt_locked(snapshot):
        st.error("Esta DT foi finalizada sem divergência e está bloqueada.")
    elif meta.get("status_dt") == "DIVERGENTE":
        st.warning("Esta DT foi finalizada com divergência e pode ser reaberta na Gestão.")
    else:
        mark_dt_started(dt, conferente, turno)

    items_df = snapshot_to_df(get_dt_snapshot(dt))
    items_df = apply_statuses(items_df).sort_values(by=["remessa", "material"]).reset_index(drop=True)

    st.subheader("Lançamento por palete")
    sku_base = st.session_state["sku_base"]
    sku_map = {}
    if sku_base is not None and not sku_base.empty:
        sku_map = dict(zip(sku_base["sku"].astype(str), sku_base["qtd_palete"].astype(int)))

    col_a, col_b = st.columns([3, 1])
    codigo_bip = col_a.text_input("Bipar SKU", key=f"bip_{dt}")
    if col_b.button("Lançar palete", key=f"btn_bip_{dt}", disabled=dt_locked(get_dt_snapshot(dt))):
        if not codigo_bip:
            st.warning("Informe o SKU.")
        elif codigo_bip not in sku_map:
            st.error("SKU não cadastrado na base SKU.")
        else:
            qtd_palete = int(sku_map[codigo_bip])
            mask = items_df["material"].astype(str) == str(codigo_bip)
            if not mask.any():
                st.error("SKU não encontrado nesta DT.")
            else:
                items_df.loc[mask, "qtd_conferida"] = items_df.loc[mask, "qtd_conferida"].astype(int) + qtd_palete
                items_df = apply_statuses(items_df)
                update_snapshot_items(dt, items_df)
                st.success(f"{qtd_palete} unidades lançadas para o SKU {codigo_bip}.")
                st.rerun()

    st.subheader("Lançamento manual")
    m1, m2, m3 = st.columns([2, 1, 1])
    sku_manual = m1.text_input("SKU manual", key=f"sku_manual_{dt}")
    qtd_manual = m2.number_input("Quantidade", min_value=1, step=1, key=f"qtd_manual_{dt}")
    if m3.button("Adicionar manual", key=f"btn_manual_{dt}", disabled=dt_locked(get_dt_snapshot(dt))):
        mask = items_df["material"].astype(str) == str(sku_manual)
        if not mask.any():
            st.error("SKU não encontrado nesta DT.")
        else:
            items_df.loc[mask, "qtd_conferida"] = items_df.loc[mask, "qtd_conferida"].astype(int) + int(qtd_manual)
            items_df = apply_statuses(items_df)
            update_snapshot_items(dt, items_df)
            st.success(f"{qtd_manual} unidades lançadas manualmente para o SKU {sku_manual}.")
            st.rerun()

    st.subheader("Itens da DT")
    editor_df = items_df[[
        "remessa", "doc_referencia", "material", "descricao",
        "qtd_solicitada", "qtd_conferida", "status_item"
    ]].copy()

    edited_view = st.data_editor(
        editor_df,
        hide_index=True,
        use_container_width=True,
        disabled=["remessa", "doc_referencia", "material", "descricao", "qtd_solicitada", "status_item"],
        column_config={
            "remessa": "Remessa",
            "doc_referencia": "Documento referência",
            "material": "SKU",
            "descricao": "Descrição",
            "qtd_solicitada": st.column_config.NumberColumn("Qtd Solicitada"),
            "qtd_conferida": st.column_config.NumberColumn("Qtd Conferida", min_value=0, step=1),
            "status_item": "Status",
        },
        key=f"editor_{dt}",
    )

    items_df["qtd_conferida"] = edited_view["qtd_conferida"].fillna(0).astype(int)
    items_df = apply_statuses(items_df)
    update_snapshot_items(dt, items_df)

    ok_count = int((items_df["status_item"] == "OK").sum())
    div_count = int((items_df["status_item"] == "DIVERGENTE").sum())
    pen_count = int((items_df["status_item"] == "PENDENTE").sum())

    c_ok, c_div, c_pen = st.columns(3)
    c_ok.metric("Itens OK", ok_count)
    c_div.metric("Itens divergentes", div_count)
    c_pen.metric("Itens pendentes", pen_count)

    has_divergence = div_count > 0

    b1, b2, b3 = st.columns(3)

    if b1.button("Gerar PDF", key=f"pdf_{dt}"):
        pdf_bytes = generate_pdf_bytes(get_dt_snapshot(dt))
        st.session_state["last_pdf_bytes"] = pdf_bytes
        st.session_state["last_pdf_name"] = f"espelho_dt_{dt}.pdf"
        st.success("PDF gerado.")

    if st.session_state["last_pdf_bytes"] is not None and st.session_state["last_pdf_name"] is not None:
        st.download_button(
            "Baixar último PDF",
            data=st.session_state["last_pdf_bytes"],
            file_name=st.session_state["last_pdf_name"],
            mime="application/pdf",
            key=f"download_pdf_{dt}",
        )

    if b2.button("Finalizar conferência", key=f"final_ok_{dt}", disabled=dt_locked(get_dt_snapshot(dt))):
        if has_divergence:
            st.error("Existem divergências. Use a opção de finalizar com divergência.")
        else:
            finalize_dt(dt, "FINALIZADO", conferente, turno)
            pdf_bytes = generate_pdf_bytes(get_dt_snapshot(dt))
            st.session_state["last_pdf_bytes"] = pdf_bytes
            st.session_state["last_pdf_name"] = f"espelho_dt_{dt}.pdf"

            try:
                send_pdf_email(pdf_bytes, f"espelho_dt_{dt}.pdf", dt, "FINALIZADO")
                st.success("Conferência finalizada e PDF enviado por e-mail.")
            except Exception as e:
                st.warning(f"Conferência finalizada, mas houve erro no envio do e-mail: {e}")

    if b3.button("Finalizar com divergência", key=f"final_div_{dt}", disabled=dt_locked(get_dt_snapshot(dt))):
        finalize_dt(dt, "DIVERGENTE", conferente, turno)
        pdf_bytes = generate_pdf_bytes(get_dt_snapshot(dt))
        st.session_state["last_pdf_bytes"] = pdf_bytes
        st.session_state["last_pdf_name"] = f"espelho_dt_{dt}.pdf"

        try:
            send_pdf_email(pdf_bytes, f"espelho_dt_{dt}.pdf", dt, "DIVERGENTE")
            st.warning("Conferência finalizada com divergência e PDF enviado por e-mail.")
        except Exception as e:
            st.warning(f"Conferência finalizada com divergência, mas houve erro no envio do e-mail: {e}")


def page_gestao():
    st.title("📊 Gestão")

    if st.session_state["base_vl06"] is None or st.session_state["base_vl06"].empty:
        st.info("Primeiro carregue a VL06 na área do Assistente.")
        return

    mgmt = build_management_df()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total DTs", len(mgmt))
    c2.metric("Pendentes", int((mgmt["Status DT"] == "PENDENTE").sum()))
    c3.metric("Em andamento", int((mgmt["Status DT"] == "EM_ANDAMENTO").sum()))
    c4.metric("Finalizadas", int((mgmt["Status DT"] == "FINALIZADO").sum()))
    c5.metric("Divergentes", int((mgmt["Status DT"] == "DIVERGENTE").sum()))

    filtro_status = st.selectbox("Filtrar por status", ["Todos", "PENDENTE", "EM_ANDAMENTO", "FINALIZADO", "DIVERGENTE"])
    show_df = mgmt.copy()
    if filtro_status != "Todos":
        show_df = show_df[show_df["Status DT"] == filtro_status]

    st.subheader("Fila de DTs")
    st.dataframe(show_df, use_container_width=True, hide_index=True)

    dts_div = mgmt[mgmt["Status DT"] == "DIVERGENTE"]["DT"].tolist()
    st.subheader("Reabrir conferência")
    if not dts_div:
        st.info("Não há DTs divergentes para reabrir.")
        return

    dt_reopen = st.selectbox("DT divergente", dts_div)
    if st.button("Reabrir DT divergente"):
        snapshot = get_dt_snapshot(dt_reopen)
        if dt_can_reopen(snapshot):
            reopen_dt(dt_reopen)
            st.success(f"DT {dt_reopen} reaberta com sucesso.")
            st.rerun()
        else:
            st.error("Só é permitido reabrir DT com status divergente.")


# =========================
# EXECUÇÃO
# =========================
init_state()
section = render_sidebar()

if section == "Assistente":
    page_assistente()
elif section == "Conferência":
    page_conferencia()
else:
    page_gestao()
