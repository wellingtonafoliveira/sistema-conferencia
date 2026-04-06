# =========================================================
# FIRESTORE STORAGE
# =========================================================
def default_store():
    return {
        "base_vl06": [],
        "sku_base": [],
        "conferencias": {},
        "insumos_cp": [],
        "boc_solicitacoes": [],
    }


def _meta_ref():
    db = get_db()
    return db.collection("app_data").document("meta")


def _dts_ref():
    db = get_db()
    return db.collection("dts")


def load_store():
    store = default_store()

    meta_doc = _meta_ref().get()
    if meta_doc.exists:
        meta_data = meta_doc.to_dict() or {}
        store["base_vl06"] = meta_data.get("base_vl06", [])
        store["sku_base"] = meta_data.get("sku_base", [])
        store["insumos_cp"] = meta_data.get("insumos_cp", [])
        store["boc_solicitacoes"] = meta_data.get("boc_solicitacoes", [])

    conferencias = {}
    for doc in _dts_ref().stream():
        payload = doc.to_dict() or {}
        conferencias[doc.id] = {
            "meta": payload.get("meta", {}),
            "items": payload.get("items", []),
        }

    store["conferencias"] = conferencias
    return store


def save_store(data):
    db = get_db()

    _meta_ref().set({
        "base_vl06": data.get("base_vl06", []),
        "sku_base": data.get("sku_base", []),
        "insumos_cp": data.get("insumos_cp", []),
        "boc_solicitacoes": data.get("boc_solicitacoes", []),
        "updated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    })

    conferencias = data.get("conferencias", {})
    existing_ids = set(doc.id for doc in _dts_ref().stream())
    incoming_ids = set(conferencias.keys())

    batch = db.batch()

    for dt, snapshot in conferencias.items():
        ref = _dts_ref().document(str(dt))
        batch.set(ref, {
            "meta": snapshot.get("meta", {}),
            "items": snapshot.get("items", []),
            "updated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        })

    for dt_to_delete in existing_ids - incoming_ids:
        batch.delete(_dts_ref().document(str(dt_to_delete)))

    batch.commit()


def get_base_vl06_df():
    doc = _meta_ref().get()
    rows = (doc.to_dict() or {}).get("base_vl06", []) if doc.exists else []
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def save_base_vl06_df(df):
    meta = _meta_ref().get()
    payload = meta.to_dict() if meta.exists else {}
    payload["base_vl06"] = df.to_dict(orient="records")
    payload["updated_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    _meta_ref().set(payload)


def get_sku_df():
    doc = _meta_ref().get()
    rows = (doc.to_dict() or {}).get("sku_base", []) if doc.exists else []
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def save_sku_df(df):
    meta = _meta_ref().get()
    payload = meta.to_dict() if meta.exists else {}
    payload["sku_base"] = df.to_dict(orient="records")
    payload["updated_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    _meta_ref().set(payload)


def get_conferencias():
    confs = {}
    for doc in _dts_ref().stream():
        payload = doc.to_dict() or {}
        confs[doc.id] = {
            "meta": payload.get("meta", {}),
            "items": payload.get("items", []),
        }
    return confs


def save_conferencias(confs):
    db = get_db()
    existing_ids = set(doc.id for doc in _dts_ref().stream())
    incoming_ids = set(confs.keys())

    batch = db.batch()

    for dt, snapshot in confs.items():
        ref = _dts_ref().document(str(dt))
        batch.set(ref, {
            "meta": snapshot.get("meta", {}),
            "items": snapshot.get("items", []),
            "updated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        })

    for dt_to_delete in existing_ids - incoming_ids:
        batch.delete(_dts_ref().document(str(dt_to_delete)))

    batch.commit()


def get_dt_list():
    return sorted([doc.id for doc in _dts_ref().stream()])


def get_dt_snapshot(dt):
    ref = _dts_ref().document(str(dt))
    doc = ref.get()

    if doc.exists:
        snapshot = doc.to_dict() or {}
        snapshot.setdefault("meta", {})
        snapshot.setdefault("items", [])
        snapshot["meta"].setdefault("pdf_url", "")
        snapshot["meta"].setdefault("pdf_public_id", "")
        snapshot["meta"].setdefault("assinatura_conferente", "")
        snapshot["meta"].setdefault("assinatura_lider", "")
        return snapshot

    base = get_base_vl06_df()
    df_dt = base[base["dt"].astype(str) == str(dt)].copy()
    if df_dt.empty:
        return None

    snapshot = {
        "meta": {
            "dt": str(dt),
            "conferente": "",
            "turno": "Manhã",
            "inicio": "",
            "fim": "",
            "status_dt": "PENDENTE",
            "cliente": str(df_dt["cliente"].iloc[0]) if "cliente" in df_dt.columns else "",
            "transportadora": str(df_dt["transportadora"].iloc[0]) if "transportadora" in df_dt.columns else "",
            "qtd_remessas": int(df_dt["remessa"].nunique()) if "remessa" in df_dt.columns else 0,
            "data_agenda": str(df_dt["data_agenda"].iloc[0]) if "data_agenda" in df_dt.columns else "",
            "hora_agenda": str(df_dt["hora_agenda"].iloc[0]) if "hora_agenda" in df_dt.columns else "",
            "perfil_carregamento": str(df_dt["perfil_carregamento"].iloc[0]) if "perfil_carregamento" in df_dt.columns else "",
            "tipo_carga": str(df_dt["tipo_carga"].iloc[0]).upper() if "tipo_carga" in df_dt.columns else "",
            "total_caixas": int(df_dt["qtd_solicitada"].fillna(0).sum()) if "qtd_solicitada" in df_dt.columns else 0,
            "metragem_cubica": float(df_dt["metragem_cubica"].fillna(0).sum()) if "metragem_cubica" in df_dt.columns else 0.0,
            "pdf_url": "",
            "pdf_public_id": "",
            "assinatura_conferente": "",
            "assinatura_lider": "",
        },
        "items": df_dt.to_dict(orient="records"),
    }

    ref.set(snapshot)
    return snapshot


def save_dt_snapshot(dt, snapshot):
    _dts_ref().document(str(dt)).set(snapshot)


def snapshot_to_df(snapshot):
    return pd.DataFrame(snapshot["items"])


def update_snapshot_items(dt, df_items):
    snapshot = deepcopy(get_dt_snapshot(dt))
    snapshot["items"] = df_items.to_dict(orient="records")
    snapshot["meta"]["total_caixas"] = int(pd.to_numeric(df_items["qtd_solicitada"], errors="coerce").fillna(0).sum()) if "qtd_solicitada" in df_items.columns else 0
    if "metragem_cubica" in df_items.columns:
        snapshot["meta"]["metragem_cubica"] = float(pd.to_numeric(df_items["metragem_cubica"], errors="coerce").fillna(0).sum())
    save_dt_snapshot(dt, snapshot)


def get_insumos_df():
    doc = _meta_ref().get()
    rows = (doc.to_dict() or {}).get("insumos_cp", []) if doc.exists else []
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def save_insumos_df(df):
    meta = _meta_ref().get()
    payload = meta.to_dict() if meta.exists else {}
    payload["insumos_cp"] = df.to_dict(orient="records")
    payload["updated_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    _meta_ref().set(payload)


def get_insumos_by_dt(dt):
    df = get_insumos_df()
    return df[df["dt"].astype(str) == str(dt)].copy() if not df.empty else pd.DataFrame()


def get_latest_insumos_cp(dt):
    df = get_insumos_by_dt(dt)
    if df.empty:
        return {}
    return df.sort_values("data_hora", ascending=False).iloc[0].to_dict()


def delete_insumos_by_dt(dt):
    df = get_insumos_df()
    if df.empty:
        return
    save_insumos_df(df[df["dt"].astype(str) != str(dt)].copy())


def get_boc_df():
    doc = _meta_ref().get()
    rows = (doc.to_dict() or {}).get("boc_solicitacoes", []) if doc.exists else []
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def save_boc_df(df):
    meta = _meta_ref().get()
    payload = meta.to_dict() if meta.exists else {}
    payload["boc_solicitacoes"] = df.to_dict(orient="records")
    payload["updated_at"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    _meta_ref().set(payload)


def get_boc_by_dt(dt):
    df = get_boc_df()
    return df[df["dt"].astype(str) == str(dt)].copy() if not df.empty else pd.DataFrame()


def delete_boc_by_dt(dt):
    df = get_boc_df()
    if df.empty:
        return
    save_boc_df(df[df["dt"].astype(str) != str(dt)].copy())
