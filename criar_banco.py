import sqlite3

conn = sqlite3.connect("banco.db")
cursor = conn.cursor()

cursor.execute("DROP TABLE IF EXISTS cargas")

cursor.execute("""
CREATE TABLE cargas (
    dt TEXT,
    remessa TEXT,
    material TEXT,
    descricao TEXT,
    qtd_solicitada INTEGER,
    qtd_conferida INTEGER DEFAULT 0,
    status TEXT DEFAULT 'PENDENTE',
    cliente TEXT,
    perfil TEXT,
    inicio TEXT,
    fim TEXT
)
""")

conn.commit()
conn.close()
