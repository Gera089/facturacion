"""Update migration_notes status based on current code functionality.
Run this after init_db() to mark modules that are already operational.
Usage: python seed_migrations.py"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timezone
from app.db import get_connection
from app.legacy_db import get_legacy_connection


def mysql_available() -> bool:
    try:
        conn = get_legacy_connection()
        conn.close()
        return True
    except Exception:
        return False


MODULES = {
    "auth":        ("Autenticacion",    "listo",     "Usuarios y sesiones en SQLite."),
    "usuarios":    ("Usuarios",         "listo",     "CRUD de usuarios via MySQL directo."),
    "cadenas":     ("Cadenas",          "listo",     "Reportes via MySQL directo."),
    "clientes":    ("Clientes",         "pendiente", "Ejecutar POST /api/migrations/clientes/run"),
    "productos":   ("Productos",        "pendiente", "Ejecutar POST /api/migrations/productos/run"),
    "facturacion": ("Facturacion",      "pendiente", "Ejecutar POST /api/migrations/facturacion/run"),
    "impresion":   ("Impresion",        "pendiente", "Ejecutar POST /api/migrations/impresion/run"),
    "cobranza":    ("Cobranza",         "pendiente", "Ejecutar POST /api/migrations/cobranza/run"),
    "timbrado":    ("Timbrado",         "pendiente", "Ejecutar POST /api/migrations/timbrado/run"),
    "reportes":    ("Reportes",         "pendiente", "Ejecutar POST /api/migrations/reportes/run"),
}


def seed():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    has_mysql = mysql_available()
    print(f"MySQL disponible: {has_mysql}")
    print()

    with get_connection() as conn:
        for module_key, (title, status, notes) in MODULES.items():
            existing = conn.execute(
                "SELECT id, status FROM migration_notes WHERE module_key = ?",
                (module_key,),
            ).fetchone()

            if existing:
                old_status = existing["status"]
                if existing["status"] != status:
                    conn.execute(
                        "UPDATE migration_notes SET status = ?, notes = ?, updated_at = ? WHERE module_key = ?",
                        (status, notes, now, module_key),
                    )
                    print(f"  {module_key:15s} {old_status:12s} -> {status:12s}  ({notes})")
                else:
                    print(f"  {module_key:15s} ya esta como '{status}'.")
            else:
                conn.execute(
                    "INSERT INTO migration_notes (module_key, title, status, notes, updated_at) VALUES (?,?,?,?,?)",
                    (module_key, title, status, notes, now),
                )
                print(f"  {module_key:15s} creado como '{status}'.")

        conn.commit()

    print()
    if not has_mysql:
        print("NOTA: MySQL no disponible. Los modulos 'pendiente' requieren conexion")
        print("      para ejecutar su migracion via POST /api/migrations/{key}/run")


if __name__ == "__main__":
    seed()
