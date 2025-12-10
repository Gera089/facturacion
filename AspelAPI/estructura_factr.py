import fdb

empresa_num = "03"  # Cambia según la empresa de prueba

con = fdb.connect(
    dsn=F"192.168.1.105:C:\\Program Files (x86)\\Common Files\\Aspel\\Sistemas Aspel\\SAE9.00\\Empresa{empresa_num}\\Datos\\SAE90EMPRE{empresa_num}.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)
cur = con.cursor()

cur.execute("""
SELECT
  rf.RDB$FIELD_NAME,
  f.RDB$FIELD_TYPE,
  f.RDB$FIELD_SUB_TYPE,
  f.RDB$CHARACTER_LENGTH
FROM RDB$RELATION_FIELDS rf
JOIN RDB$FIELDS f ON rf.RDB$FIELD_SOURCE = f.RDB$FIELD_NAME
WHERE rf.RDB$RELATION_NAME = 'FACTR03'
ORDER BY rf.RDB$FIELD_POSITION
""")

pos = 1
for row in cur.fetchall():
    print(f"{pos:02d} → {row}")
    pos += 1