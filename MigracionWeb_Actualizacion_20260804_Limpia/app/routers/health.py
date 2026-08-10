from fastapi import APIRouter

from app.core.config import settings
from app.legacy_db import LEGACY_CFG, get_legacy_connection, _host_is_reachable, _mysql_hosts, _parse_host


router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.version,
        "port": settings.port,
    }


@router.get("/health/legacy-mysql")
def legacy_mysql_health():
    hosts = []
    for entry in _mysql_hosts():
        host, port = _parse_host(entry)
        reachable = _host_is_reachable(host, port, timeout=1.5)
        hosts.append({
            "entry": entry,
            "host": host,
            "port": port,
            "reachable": reachable,
            "tipo": "local" if host.startswith("192.168.") else ("tailscale" if host.startswith("100.") else "otro"),
        })
    conectado = False
    activo = {}
    error = ""
    try:
        conn = get_legacy_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT DATABASE() AS database_name, @@hostname AS server_hostname, CONNECTION_ID() AS connection_id")
            activo = cur.fetchone() or {}
        finally:
            cur.close()
            conn.close()
        conectado = True
    except Exception as exc:
        error = str(exc)
    return {
        "ok": conectado,
        "database": "comandas_db",
        "usuario": LEGACY_CFG.get("mysql_user"),
        "hosts": hosts,
        "activo": activo,
        "error": error,
    }


@router.get("/version")
def version():
    return {
        "app": settings.app_name,
        "version": settings.version,
        "api_prefix": settings.api_prefix,
    }


@router.get("/api/client-config")
def client_config():
    return {
        "api_urls": settings.api_urls,
        "api_port": settings.port,
        "api_prefix": settings.api_prefix,
    }
