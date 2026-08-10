from __future__ import annotations

import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.dependencies import require_user


router = APIRouter(prefix="/api/comandas/tarifador", tags=["Tarifador de envios"])

TARIFADOR_DB = settings.storage_dir / "tarifador_envios.sqlite3"
ORIGINAL_TARIFADOR_DB = Path(r"D:\Proyectos\Tarifador de envios\instance\tarifador.db")


class TarifadorClientIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=50)
    notes: str = ""
    default_destination: str = ""


class TarifadorCarrierIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    contact: str = Field(default="", max_length=200)
    notes: str = ""
    volumetric_factor: int = Field(default=5000, ge=1)


class TarifadorOriginIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    address: str = Field(default="", max_length=300)
    city: str = Field(default="", max_length=100)
    state: str = Field(default="", max_length=100)
    zip_code: str = Field(default="", max_length=20)
    country: str = Field(default="Mexico", max_length=100)
    contact: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=50)
    email: str = Field(default="", max_length=200)
    notes: str = ""
    is_active: bool = True


class TarifadorBoxIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    length_cm: float = Field(default=0, ge=0)
    width_cm: float = Field(default=0, ge=0)
    height_cm: float = Field(default=0, ge=0)
    weight_kg: float = Field(default=0, ge=0)
    notes: str = ""
    is_active: bool = True


class TarifadorShipmentIn(BaseModel):
    id: int | None = None
    client_id: int = Field(gt=0)
    carrier_id: int = Field(gt=0)
    origin_id: int | None = None
    weight_kg: float = Field(ge=0)
    length_cm: float = Field(default=0, ge=0)
    width_cm: float = Field(default=0, ge=0)
    height_cm: float = Field(default=0, ge=0)
    shipping_cost: float = Field(ge=0)
    origin: str = Field(default="", max_length=200)
    destination: str = Field(default="", max_length=200)
    date: str = ""


class TarifadorZoneIn(BaseModel):
    id: int | None = None
    carrier_id: int = Field(gt=0)
    code: str = Field(min_length=1, max_length=10)
    name: str = Field(default="", max_length=200)
    description: str = ""
    states: str = ""


class TarifadorServiceIn(BaseModel):
    id: int | None = None
    carrier_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(default="", max_length=50)
    description: str = ""
    delivery_time: str = Field(default="", max_length=100)
    is_active: bool = True


class TarifadorRateIn(BaseModel):
    id: int | None = None
    carrier_id: int = Field(gt=0)
    service_id: int | None = None
    zone_id: int | None = None
    weight_from: float = Field(default=0, ge=0)
    weight_to: float = Field(default=999, ge=0)
    price: float = Field(ge=0)
    is_active: bool = True


class TarifadorQuoteItem(BaseModel):
    name: str = Field(default="Bulto", max_length=120)
    qty: int = Field(default=1, ge=1)
    weight_kg: float = Field(default=0, ge=0)
    length_cm: float = Field(default=0, ge=0)
    width_cm: float = Field(default=0, ge=0)
    height_cm: float = Field(default=0, ge=0)


class TarifadorQuoteIn(BaseModel):
    carrier_id: int = Field(gt=0)
    zone_id: int | None = None
    service_id: int | None = None
    origin_id: int | None = None
    destination: str = Field(default="", max_length=250)
    items: list[TarifadorQuoteItem] = Field(default_factory=list)
    weight_kg: float = Field(default=0, ge=0)
    length_cm: float = Field(default=0, ge=0)
    width_cm: float = Field(default=0, ge=0)
    height_cm: float = Field(default=0, ge=0)


class TarifadorRatesIn(BaseModel):
    client_id: int | None = None
    carrier_id: int | None = None
    markup: float = Field(default=10, ge=0, le=500)


def _dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


def _conn() -> sqlite3.Connection:
    _ensure_tarifador_db()
    conn = sqlite3.connect(TARIFADOR_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tarifador_db() -> None:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    if not TARIFADOR_DB.exists() and ORIGINAL_TARIFADOR_DB.exists():
        shutil.copy2(ORIGINAL_TARIFADOR_DB, TARIFADOR_DB)
    conn = sqlite3.connect(TARIFADOR_DB)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS client (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                email TEXT,
                phone TEXT,
                notes TEXT,
                created_at DATETIME,
                default_destination TEXT
            );
            CREATE TABLE IF NOT EXISTS carrier (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                contact TEXT,
                notes TEXT,
                created_at DATETIME,
                volumetric_factor INTEGER DEFAULT 5000
            );
            CREATE TABLE IF NOT EXISTS carrier_service (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                carrier_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                code TEXT,
                description TEXT,
                delivery_time TEXT,
                is_active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS carrier_zone (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                carrier_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                description TEXT,
                states TEXT
            );
            CREATE TABLE IF NOT EXISTS carrier_rate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                carrier_id INTEGER NOT NULL,
                service_id INTEGER,
                zone_id INTEGER,
                weight_from REAL NOT NULL DEFAULT 0,
                weight_to REAL NOT NULL DEFAULT 999,
                price REAL NOT NULL,
                is_active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS origin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT,
                city TEXT,
                state TEXT,
                zip_code TEXT,
                country TEXT DEFAULT 'Mexico',
                contact TEXT,
                phone TEXT,
                email TEXT,
                notes TEXT,
                is_active INTEGER DEFAULT 1,
                created_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS box_type (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                length_cm REAL NOT NULL DEFAULT 0,
                width_cm REAL NOT NULL DEFAULT 0,
                height_cm REAL NOT NULL DEFAULT 0,
                weight_kg REAL DEFAULT 0,
                notes TEXT,
                is_active INTEGER DEFAULT 1,
                created_at DATETIME
            );
            CREATE TABLE IF NOT EXISTS shipment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                carrier_id INTEGER NOT NULL,
                origin_id INTEGER,
                weight_kg REAL NOT NULL,
                length_cm REAL DEFAULT 0,
                width_cm REAL DEFAULT 0,
                height_cm REAL DEFAULT 0,
                volumetric_weight_kg REAL DEFAULT 0,
                shipping_cost REAL NOT NULL,
                origin TEXT,
                destination TEXT,
                date DATE,
                created_at DATETIME
            );
            """
        )
        if not conn.execute("SELECT 1 FROM carrier LIMIT 1").fetchone():
            _seed_tarifador(conn)
        conn.commit()
    finally:
        conn.close()


def _seed_tarifador(conn: sqlite3.Connection) -> None:
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    carriers = [
        ("DHL", "dhl.com.mx", 5000),
        ("FedEx", "fedex.com/mx", 5000),
        ("Estafeta", "estafeta.com", 5000),
        ("Tresguerras", "tresguerras.com.mx", 4000),
        ("Paquetexpress", "paquetexpress.com.mx", 5000),
    ]
    ids = {}
    for name, contact, factor in carriers:
        cur = conn.execute(
            "INSERT INTO carrier (name, contact, volumetric_factor, created_at) VALUES (?, ?, ?, ?)",
            (name, contact, factor, now),
        )
        ids[name] = cur.lastrowid
    zone_sets = {
        "DHL": {"A": "CDMX y zona metropolitana", "B": "Estado de Mexico", "C": "Centro", "D": "Bajio", "E": "Sureste", "H": "Destinos remotos"},
        "FedEx": {"1": "Local", "2": "Zona 2", "3": "Zona 3", "8": "Remota"},
        "Estafeta": {"1": "Metropolitana", "2": "Cercana", "3": "Nacional", "4": "Extendida"},
        "Tresguerras": {"1": "Metropolitana", "2": "Centro", "3": "Occidente", "4": "Norte", "8": "Extendida"},
        "Paquetexpress": {"1": "Local", "2": "Regional", "3": "Nacional", "4": "Extendida"},
    }
    rates = []
    for carrier, zones in zone_sets.items():
        for code, desc in zones.items():
            zcur = conn.execute(
                "INSERT INTO carrier_zone (carrier_id, code, name, description, states) VALUES (?, ?, ?, ?, ?)",
                (ids[carrier], code, f"Zona {code}", desc, desc),
            )
            base = 250 + (len(rates) % 8) * 20
            for weight, mult in [(1, 1), (3, 1.25), (5, 1.55), (10, 2.1), (20, 3.4), (50, 6.5)]:
                rates.append((ids[carrier], zcur.lastrowid, 0, weight, round(base * mult, 2), 1))
    conn.executemany(
        "INSERT INTO carrier_rate (carrier_id, zone_id, weight_from, weight_to, price, is_active) VALUES (?, ?, ?, ?, ?, ?)",
        rates,
    )
    conn.execute(
        "INSERT INTO origin (name, address, city, state, zip_code, country, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
        ("Bodega principal", "", "CDMX", "CDMX", "", "Mexico", now),
    )
    conn.execute(
        "INSERT INTO box_type (name, length_cm, width_cm, height_cm, weight_kg, is_active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
        ("Caja estandar", 30, 30, 30, 1, now),
    )


def _bool(value) -> int:
    return 1 if value else 0


def _volumetric_weight(length: float, width: float, height: float, factor: int | float) -> float:
    if length and width and height and factor:
        return (float(length) * float(width) * float(height)) / float(factor)
    return 0.0


def _carrier(conn: sqlite3.Connection, carrier_id: int) -> dict:
    carrier = _dict(conn.execute("SELECT * FROM carrier WHERE id = ?", (carrier_id,)).fetchone())
    if not carrier:
        raise HTTPException(status_code=404, detail="Transportista no encontrado.")
    return carrier


def _zone_match(conn: sqlite3.Connection, carrier_id: int, destination: str) -> dict | None:
    zones = [_dict(r) for r in conn.execute("SELECT * FROM carrier_zone WHERE carrier_id = ? ORDER BY code", (carrier_id,)).fetchall()]
    if not zones:
        return None
    words = [w for w in str(destination or "").lower().replace(",", " ").split() if len(w) > 2]
    best = None
    best_score = 0
    for zone in zones:
        text = " ".join(str(zone.get(k) or "") for k in ("code", "name", "description", "states")).lower()
        score = sum(1 for word in words if word in text)
        if score > best_score:
            best_score = score
            best = zone
    return best or zones[0]


def _rate_for(conn: sqlite3.Connection, carrier_id: int, zone_id: int | None, service_id: int | None, chargeable_weight: float) -> dict | None:
    params: list = [carrier_id]
    sql = """
        SELECT r.*, z.code AS zone_code, z.name AS zone_name, z.description AS zone_description,
               s.name AS service_name
        FROM carrier_rate r
        LEFT JOIN carrier_zone z ON z.id = r.zone_id
        LEFT JOIN carrier_service s ON s.id = r.service_id
        WHERE r.carrier_id = ? AND COALESCE(r.is_active, 1) = 1
    """
    if zone_id:
        sql += " AND r.zone_id = ?"
        params.append(zone_id)
    if service_id:
        sql += " AND (r.service_id = ? OR r.service_id IS NULL)"
        params.append(service_id)
    sql += " AND r.weight_from < ? AND r.weight_to >= ? ORDER BY CASE WHEN r.service_id IS NULL THEN 1 ELSE 0 END, r.weight_to LIMIT 1"
    params.extend([chargeable_weight, chargeable_weight])
    return _dict(conn.execute(sql, params).fetchone())


@router.get("/summary")
def tarifador_summary(user=Depends(require_user)):
    with _conn() as conn:
        return {
            "clients": conn.execute("SELECT COUNT(*) FROM client").fetchone()[0],
            "carriers": conn.execute("SELECT COUNT(*) FROM carrier").fetchone()[0],
            "shipments": conn.execute("SELECT COUNT(*) FROM shipment").fetchone()[0],
            "boxes": conn.execute("SELECT COUNT(*) FROM box_type").fetchone()[0],
        }


@router.get("/catalogos")
def tarifador_catalogos(user=Depends(require_user)):
    with _conn() as conn:
        return {
            "clients": [_dict(r) for r in conn.execute("SELECT * FROM client ORDER BY name LIMIT 5000").fetchall()],
            "carriers": [_dict(r) for r in conn.execute("SELECT * FROM carrier ORDER BY name").fetchall()],
            "origins": [_dict(r) for r in conn.execute("SELECT * FROM origin ORDER BY name").fetchall()],
            "boxes": [_dict(r) for r in conn.execute("SELECT * FROM box_type ORDER BY name").fetchall()],
        }


@router.get("/clients")
def tarifador_clients(q: str = "", limit: int = Query(300, ge=1, le=5000), user=Depends(require_user)):
    with _conn() as conn:
        params = []
        sql = """
            SELECT c.*, COUNT(s.id) AS shipments
            FROM client c
            LEFT JOIN shipment s ON s.client_id = c.id
        """
        if q.strip():
            sql += " WHERE c.name LIKE ? OR c.phone LIKE ? OR c.email LIKE ? OR c.notes LIKE ?"
            like = f"%{q.strip()}%"
            params = [like, like, like, like]
        sql += " GROUP BY c.id ORDER BY c.name LIMIT ?"
        params.append(limit)
        return [_dict(r) for r in conn.execute(sql, params).fetchall()]


@router.post("/clients")
def tarifador_client_save(payload: TarifadorClientIn, user=Depends(require_user)):
    with _conn() as conn:
        now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
        if payload.id:
            conn.execute(
                "UPDATE client SET name=?, email=?, phone=?, notes=?, default_destination=? WHERE id=?",
                (payload.name, payload.email, payload.phone, payload.notes, payload.default_destination, payload.id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO client (name, email, phone, notes, default_destination, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (payload.name, payload.email, payload.phone, payload.notes, payload.default_destination, now),
            )
            payload.id = cur.lastrowid
        conn.commit()
        return _dict(conn.execute("SELECT * FROM client WHERE id=?", (payload.id,)).fetchone())


@router.delete("/clients/{client_id}")
def tarifador_client_delete(client_id: int, user=Depends(require_user)):
    with _conn() as conn:
        conn.execute("DELETE FROM client WHERE id=?", (client_id,))
        conn.commit()
        return {"ok": True}


@router.post("/clients/import-migration")
def tarifador_clients_import_migration(user=Depends(require_user)):
    if not settings.db_path.exists():
        raise HTTPException(status_code=404, detail="Base de migracion no encontrada.")
    added = 0
    skipped = 0
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    source = sqlite3.connect(settings.db_path)
    source.row_factory = sqlite3.Row
    try:
        rows = source.execute(
            """
            SELECT numero, nombre, empresa, rfc, telefono, correo_electronico,
                   calle, no_exterior, no_interior, colonia, estado, observaciones,
                   consignatario, consig_calle, consig_no_exterior, consig_no_interior,
                   consig_colonia, consig_poblacion, consig_estado
            FROM cliente_migracion
            ORDER BY nombre
            """
        ).fetchall()
    finally:
        source.close()
    with _conn() as conn:
        for row in rows:
            nombre = str(row["nombre"] or "").strip()
            if not nombre:
                continue
            display = f"{nombre} ({row['empresa']})" if row["empresa"] else nombre
            if conn.execute("SELECT id FROM client WHERE name=?", (display,)).fetchone():
                skipped += 1
                continue
            address_parts = [str(row[k] or "").strip() for k in ("calle", "no_exterior", "no_interior", "colonia") if str(row[k] or "").strip()]
            notes = [f"#{row['numero']}"]
            if row["rfc"]:
                notes.append(f"RFC: {row['rfc']}")
            if address_parts:
                notes.append("Dir: " + ", ".join(address_parts))
            if row["estado"]:
                notes.append(f"Estado: {row['estado']}")
            if row["observaciones"]:
                notes.append(f"Obs: {row['observaciones']}")
            consig_parts = [
                str(row[k] or "").strip()
                for k in ("consignatario", "consig_calle", "consig_no_exterior", "consig_no_interior", "consig_colonia", "consig_poblacion", "consig_estado")
                if str(row[k] or "").strip()
            ]
            conn.execute(
                "INSERT INTO client (name, email, phone, notes, default_destination, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (display, row["correo_electronico"] or "", row["telefono"] or "", " | ".join(notes), ", ".join(consig_parts), now),
            )
            added += 1
        conn.commit()
    return {"added": added, "skipped": skipped}


@router.get("/carriers")
def tarifador_carriers(user=Depends(require_user)):
    with _conn() as conn:
        return [_dict(r) for r in conn.execute("SELECT * FROM carrier ORDER BY name").fetchall()]


@router.post("/carriers")
def tarifador_carrier_save(payload: TarifadorCarrierIn, user=Depends(require_user)):
    with _conn() as conn:
        now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
        if payload.id:
            conn.execute(
                "UPDATE carrier SET name=?, contact=?, notes=?, volumetric_factor=? WHERE id=?",
                (payload.name, payload.contact, payload.notes, payload.volumetric_factor, payload.id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO carrier (name, contact, notes, volumetric_factor, created_at) VALUES (?, ?, ?, ?, ?)",
                (payload.name, payload.contact, payload.notes, payload.volumetric_factor, now),
            )
            payload.id = cur.lastrowid
        conn.commit()
        return _dict(conn.execute("SELECT * FROM carrier WHERE id=?", (payload.id,)).fetchone())


@router.delete("/carriers/{carrier_id}")
def tarifador_carrier_delete(carrier_id: int, user=Depends(require_user)):
    with _conn() as conn:
        conn.execute("DELETE FROM carrier WHERE id=?", (carrier_id,))
        conn.commit()
        return {"ok": True}


@router.get("/carriers/{carrier_id}/detalle")
def tarifador_carrier_detail(carrier_id: int, user=Depends(require_user)):
    with _conn() as conn:
        carrier = _carrier(conn, carrier_id)
        carrier["zones"] = [_dict(r) for r in conn.execute("SELECT * FROM carrier_zone WHERE carrier_id=? ORDER BY code", (carrier_id,)).fetchall()]
        carrier["services"] = [_dict(r) for r in conn.execute("SELECT * FROM carrier_service WHERE carrier_id=? ORDER BY name", (carrier_id,)).fetchall()]
        carrier["rates"] = [
            _dict(r)
            for r in conn.execute(
                """
                SELECT r.*, z.code AS zone_code, z.name AS zone_name, s.name AS service_name
                FROM carrier_rate r
                LEFT JOIN carrier_zone z ON z.id=r.zone_id
                LEFT JOIN carrier_service s ON s.id=r.service_id
                WHERE r.carrier_id=?
                ORDER BY z.code, r.weight_to, s.name
                """,
                (carrier_id,),
            ).fetchall()
        ]
        return carrier


@router.post("/zones")
def tarifador_zone_save(payload: TarifadorZoneIn, user=Depends(require_user)):
    with _conn() as conn:
        if payload.id:
            conn.execute(
                "UPDATE carrier_zone SET carrier_id=?, code=?, name=?, description=?, states=? WHERE id=?",
                (payload.carrier_id, payload.code, payload.name, payload.description, payload.states, payload.id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO carrier_zone (carrier_id, code, name, description, states) VALUES (?, ?, ?, ?, ?)",
                (payload.carrier_id, payload.code, payload.name, payload.description, payload.states),
            )
            payload.id = cur.lastrowid
        conn.commit()
        return _dict(conn.execute("SELECT * FROM carrier_zone WHERE id=?", (payload.id,)).fetchone())


@router.post("/services")
def tarifador_service_save(payload: TarifadorServiceIn, user=Depends(require_user)):
    with _conn() as conn:
        if payload.id:
            conn.execute(
                "UPDATE carrier_service SET carrier_id=?, name=?, code=?, description=?, delivery_time=?, is_active=? WHERE id=?",
                (payload.carrier_id, payload.name, payload.code, payload.description, payload.delivery_time, _bool(payload.is_active), payload.id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO carrier_service (carrier_id, name, code, description, delivery_time, is_active) VALUES (?, ?, ?, ?, ?, ?)",
                (payload.carrier_id, payload.name, payload.code, payload.description, payload.delivery_time, _bool(payload.is_active)),
            )
            payload.id = cur.lastrowid
        conn.commit()
        return _dict(conn.execute("SELECT * FROM carrier_service WHERE id=?", (payload.id,)).fetchone())


@router.post("/rates")
def tarifador_rate_save(payload: TarifadorRateIn, user=Depends(require_user)):
    with _conn() as conn:
        if payload.id:
            conn.execute(
                "UPDATE carrier_rate SET carrier_id=?, service_id=?, zone_id=?, weight_from=?, weight_to=?, price=?, is_active=? WHERE id=?",
                (payload.carrier_id, payload.service_id, payload.zone_id, payload.weight_from, payload.weight_to, payload.price, _bool(payload.is_active), payload.id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO carrier_rate (carrier_id, service_id, zone_id, weight_from, weight_to, price, is_active) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (payload.carrier_id, payload.service_id, payload.zone_id, payload.weight_from, payload.weight_to, payload.price, _bool(payload.is_active)),
            )
            payload.id = cur.lastrowid
        conn.commit()
        return _dict(conn.execute("SELECT * FROM carrier_rate WHERE id=?", (payload.id,)).fetchone())


@router.delete("/{table}/{item_id}")
def tarifador_delete(table: str, item_id: int, user=Depends(require_user)):
    allowed = {"zones": "carrier_zone", "services": "carrier_service", "rates": "carrier_rate", "origins": "origin", "boxes": "box_type", "shipments": "shipment"}
    if table not in allowed:
        raise HTTPException(status_code=404, detail="Catalogo no soportado.")
    with _conn() as conn:
        conn.execute(f"DELETE FROM {allowed[table]} WHERE id=?", (item_id,))
        conn.commit()
        return {"ok": True}


@router.post("/origins")
def tarifador_origin_save(payload: TarifadorOriginIn, user=Depends(require_user)):
    with _conn() as conn:
        now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
        if payload.id:
            conn.execute(
                "UPDATE origin SET name=?, address=?, city=?, state=?, zip_code=?, country=?, contact=?, phone=?, email=?, notes=?, is_active=? WHERE id=?",
                (payload.name, payload.address, payload.city, payload.state, payload.zip_code, payload.country, payload.contact, payload.phone, payload.email, payload.notes, _bool(payload.is_active), payload.id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO origin (name, address, city, state, zip_code, country, contact, phone, email, notes, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (payload.name, payload.address, payload.city, payload.state, payload.zip_code, payload.country, payload.contact, payload.phone, payload.email, payload.notes, _bool(payload.is_active), now),
            )
            payload.id = cur.lastrowid
        conn.commit()
        return _dict(conn.execute("SELECT * FROM origin WHERE id=?", (payload.id,)).fetchone())


@router.post("/boxes")
def tarifador_box_save(payload: TarifadorBoxIn, user=Depends(require_user)):
    with _conn() as conn:
        now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
        if payload.id:
            conn.execute(
                "UPDATE box_type SET name=?, length_cm=?, width_cm=?, height_cm=?, weight_kg=?, notes=?, is_active=? WHERE id=?",
                (payload.name, payload.length_cm, payload.width_cm, payload.height_cm, payload.weight_kg, payload.notes, _bool(payload.is_active), payload.id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO box_type (name, length_cm, width_cm, height_cm, weight_kg, notes, is_active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (payload.name, payload.length_cm, payload.width_cm, payload.height_cm, payload.weight_kg, payload.notes, _bool(payload.is_active), now),
            )
            payload.id = cur.lastrowid
        conn.commit()
        return _dict(conn.execute("SELECT * FROM box_type WHERE id=?", (payload.id,)).fetchone())


@router.get("/shipments")
def tarifador_shipments(limit: int = Query(500, ge=1, le=5000), user=Depends(require_user)):
    with _conn() as conn:
        return [
            _dict(r)
            for r in conn.execute(
                """
                SELECT s.*, c.name AS client_name, ca.name AS carrier_name, o.name AS origin_name
                FROM shipment s
                JOIN client c ON c.id=s.client_id
                JOIN carrier ca ON ca.id=s.carrier_id
                LEFT JOIN origin o ON o.id=s.origin_id
                ORDER BY COALESCE(s.date, s.created_at) DESC, s.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        ]


@router.post("/shipments")
def tarifador_shipment_save(payload: TarifadorShipmentIn, user=Depends(require_user)):
    with _conn() as conn:
        carrier = _carrier(conn, payload.carrier_id)
        vol = _volumetric_weight(payload.length_cm, payload.width_cm, payload.height_cm, carrier.get("volumetric_factor") or 5000)
        fecha = payload.date or date.today().isoformat()
        now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
        if payload.id:
            conn.execute(
                """
                UPDATE shipment SET client_id=?, carrier_id=?, origin_id=?, weight_kg=?, length_cm=?, width_cm=?, height_cm=?,
                    volumetric_weight_kg=?, shipping_cost=?, origin=?, destination=?, date=? WHERE id=?
                """,
                (payload.client_id, payload.carrier_id, payload.origin_id, payload.weight_kg, payload.length_cm, payload.width_cm, payload.height_cm, vol, payload.shipping_cost, payload.origin, payload.destination, fecha, payload.id),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO shipment (client_id, carrier_id, origin_id, weight_kg, length_cm, width_cm, height_cm,
                    volumetric_weight_kg, shipping_cost, origin, destination, date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (payload.client_id, payload.carrier_id, payload.origin_id, payload.weight_kg, payload.length_cm, payload.width_cm, payload.height_cm, vol, payload.shipping_cost, payload.origin, payload.destination, fecha, now),
            )
            payload.id = cur.lastrowid
        conn.commit()
        return {"ok": True, "id": payload.id, "volumetric_weight_kg": round(vol, 2)}


@router.post("/quote")
def tarifador_quote(payload: TarifadorQuoteIn, user=Depends(require_user)):
    with _conn() as conn:
        carrier = _carrier(conn, payload.carrier_id)
        factor = carrier.get("volumetric_factor") or 5000
        total_real = sum(item.weight_kg * item.qty for item in payload.items) or payload.weight_kg
        total_vol_cm3 = sum(item.length_cm * item.width_cm * item.height_cm * item.qty for item in payload.items)
        vol_weight = (total_vol_cm3 / factor) if total_vol_cm3 else _volumetric_weight(payload.length_cm, payload.width_cm, payload.height_cm, factor)
        chargeable = max(total_real, vol_weight)
        zone = _dict(conn.execute("SELECT * FROM carrier_zone WHERE id=?", (payload.zone_id,)).fetchone()) if payload.zone_id else _zone_match(conn, payload.carrier_id, payload.destination)
        if not zone:
            raise HTTPException(status_code=404, detail="No hay zonas configuradas para el transportista.")
        rate = _rate_for(conn, payload.carrier_id, zone["id"], payload.service_id, chargeable)
        service = _dict(conn.execute("SELECT * FROM carrier_service WHERE id=?", (payload.service_id,)).fetchone()) if payload.service_id else None
        origin = _dict(conn.execute("SELECT * FROM origin WHERE id=?", (payload.origin_id,)).fetchone()) if payload.origin_id else None
        return {
            "carrier": carrier["name"],
            "service_name": service["name"] if service else (rate or {}).get("service_name"),
            "origin": f"{origin['name']}, {origin['city']}" if origin else "",
            "destination": payload.destination,
            "zone": zone.get("name") or zone.get("code"),
            "zone_description": zone.get("description") or "",
            "real_weight": round(total_real, 2),
            "vol_weight": round(vol_weight, 2),
            "vol_cm3": round(total_vol_cm3, 2),
            "vol_factor": factor,
            "chargeable_weight": round(chargeable, 2),
            "rate": round(float(rate["price"]), 2) if rate else None,
            "rate_id": rate["id"] if rate else None,
            "items": [item.dict() for item in payload.items],
        }


@router.post("/rates/calculate")
def tarifador_rates_calculate(payload: TarifadorRatesIn, user=Depends(require_user)):
    ranges = [(0, 1), (1, 3), (3, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 999999)]
    with _conn() as conn:
        params = []
        sql = """
            SELECT s.*, ca.volumetric_factor
            FROM shipment s
            JOIN carrier ca ON ca.id=s.carrier_id
            WHERE 1=1
        """
        if payload.client_id:
            sql += " AND s.client_id=?"
            params.append(payload.client_id)
        if payload.carrier_id:
            sql += " AND s.carrier_id=?"
            params.append(payload.carrier_id)
        shipments = [_dict(r) for r in conn.execute(sql, params).fetchall()]
        markup = payload.markup / 100
        results = []
        for lo, hi in ranges:
            filtered = []
            for row in shipments:
                vol = float(row.get("volumetric_weight_kg") or 0) or _volumetric_weight(row.get("length_cm") or 0, row.get("width_cm") or 0, row.get("height_cm") or 0, row.get("volumetric_factor") or 5000)
                chargeable = max(float(row.get("weight_kg") or 0), vol)
                if lo < chargeable <= hi:
                    row["chargeable_weight"] = chargeable
                    filtered.append(row)
            if not filtered:
                continue
            costs = sorted(float(row.get("shipping_cost") or 0) for row in filtered)
            weights = [float(row.get("chargeable_weight") or 0) for row in filtered]
            p25 = costs[len(costs) // 4] if len(costs) >= 4 else min(costs)
            p75 = costs[3 * len(costs) // 4] if len(costs) >= 4 else max(costs)
            avg_cost = sum(costs) / len(costs)
            results.append(
                {
                    "range": f"{lo:.0f} - {'+' if hi >= 999999 else f'{hi:.0f}'} kg",
                    "count": len(filtered),
                    "avg_weight": round(sum(weights) / len(weights), 2),
                    "min_cost": round(min(costs), 2),
                    "max_cost": round(max(costs), 2),
                    "avg_cost": round(avg_cost, 2),
                    "p25": round(p25, 2),
                    "p75": round(p75, 2),
                    "suggested_rate": round(avg_cost * (1 + markup), 2),
                    "suggested_rate_p75": round(p75 * (1 + markup), 2),
                }
            )
        return {"results": results, "shipments": len(shipments), "markup": payload.markup}
