import os

# Carpeta donde está tu proyecto
root = r"C:\AspelAPI"

TARGET_IP = "127.0.0.1"
NEW_IP = "127.0.0.1"

print("======================================")
print("   Reparador de IP MySQL en API SAE   ")
print("======================================")

for folder, _, files in os.walk(root):
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(folder, file)
            
            with open(path, "r", encoding="utf-8") as f:
                contenido = f.read()

            # Si no encuentra la IP, lo salta
            if TARGET_IP not in contenido:
                continue

            nuevo = contenido.replace(TARGET_IP, NEW_IP)

            with open(path, "w", encoding="utf-8") as f:
                f.write(nuevo)

            print(f"✔ Reemplazado en: {path}")

print("======================================")
print("  Reemplazo completo. Reinicia API.   ")
print("======================================")