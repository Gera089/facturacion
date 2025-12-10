import fdb

dsn = r"192.168.1.105:C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa03\Datos\SAE90EMPRE03.FDB"

con = fdb.connect(
    dsn=dsn,
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
AND rf.RDB$FIELD_NAME = 'DAT_MOSTR';
""")

print("\n📌 TIPOS REALES DE DAT_MOSTR EN FACTR03:", cur.fetchall(), "\n")