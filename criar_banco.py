import sqlite3

conn = sqlite3.connect("banco.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS cargas (
    dt TEXT,
    remessa TEXT,
    material TEXT,
    descricao TEXT,
    qtd_solicitada INTEGER,
    qtd_conferida INTEGER DEFAULT 0,
    status TEXT DEFAULT 'PENDENTE',
    conferente TEXT,
    inicio TEXT,
    fim TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS cadastro_sku (
    sku TEXT,
    descricao TEXT,
    qtd_palete INTEGER
)
""")

conn.commit()
conn.close()

print("Banco criado com sucesso!")
