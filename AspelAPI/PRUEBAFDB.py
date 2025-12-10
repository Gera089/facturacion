import fdb
import os

os.add_dll_directory(r"C:\FirebirdPython")

con = fdb.connect(
    dsn=r"C:\Program Files (x86)\Common Files\Aspel\Sistemas Aspel\SAE9.00\Empresa01\Datos\SAE90EMPRE01.FDB",
    user="SYSDBA",
    password="masterkey",
    charset="WIN1252"
)

print("CONEXIÓN EXITOSA")