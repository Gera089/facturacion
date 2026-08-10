import json
import os
import socket
from pathlib import Path

import mysql.connector
from mysql.connector import ClientFlag, errorcode


_DEFAULT_CFG = {
    "mysql_hosts": (
        os.environ.get("MYSQL_HOSTS", "").split(",")
        if os.environ.get("MYSQL_HOSTS")
        # En oficina se usa la IP LAN; fuera de ella se recurre a Tailscale.
        else ["192.168.1.146:3307", "100.69.142.19:3307", "127.0.0.1:3307"]
    ),
    "mysql_user": os.environ.get("MYSQL_USER") or "Facturacion",
    "mysql_pass": os.environ.get("MYSQL_PASS") or "ALD2013*",
    "mysql_port": int(os.environ.get("MYSQL_PORT") or 3307),
    "mysql_allow_local_fallback": os.environ.get("MYSQL_ALLOW_LOCAL") == "1",
}

_HOST_CACHE = None


def _load_cfg() -> dict:
    cfg = dict(_DEFAULT_CFG)
    app_dir = Path(__file__).resolve().parents[1]
    project_dir = app_dir.parent
    candidates = [
        Path(os.environ["FACTURACION_CFG"]) if os.environ.get("FACTURACION_CFG") else None,
        app_dir / "config.json",
        project_dir / "AspelAPI" / "config.json",
        project_dir / "config.json",
        # La aplicación web vive en MigracionWeb y el legado es un proyecto hermano.
        # Incluimos ambas configuraciones compartidas para respetar el puerto central.
        project_dir.parent / "AspelAPI" / "config.json",
        project_dir.parent / "config.json",
    ]

    for path in candidates:
        try:
            if path is None:
                continue
            if path.exists():
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh) or {}
                if isinstance(data.get("mysql_hosts"), list) and data["mysql_hosts"]:
                    cfg["mysql_hosts"] = _unique_hosts(data["mysql_hosts"])
                for key in ("mysql_user", "mysql_pass", "mysql_port", "mysql_allow_local_fallback"):
                    if data.get(key) is not None:
                        cfg[key] = data[key]
        except Exception:
            pass
    return cfg


def _parse_host(entry: str):
    """Parse 'host:port' or 'host' into (host, port)."""
    entry = str(entry or "").strip()
    if ":" in entry:
        parts = entry.rsplit(":", 1)
        return parts[0], int(parts[1])
    return entry, int(LEGACY_CFG["mysql_port"])


def _unique_hosts(hosts):
    unique = []
    seen = set()
    for entry in hosts:
        if entry is None:
            continue
        entry = str(entry or "").strip()
        if not entry or entry.lower() in {"none", "null"}:
            continue
        # _load_cfg() se ejecuta antes de que exista LEGACY_CFG; no uses
        # _parse_host aquí porque para hosts sin puerto consulta ese global.
        host = entry.rsplit(":", 1)[0] if ":" in entry else entry
        if host not in seen:
            seen.add(host)
            unique.append(entry)
    return unique


def _host_is_reachable(host: str, port: int, timeout: float = 1.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_local_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _mysql_hosts() -> list[str]:
    hosts = _unique_hosts(LEGACY_CFG["mysql_hosts"])
    allow_local = bool(LEGACY_CFG.get("mysql_allow_local_fallback"))
    if allow_local:
        return hosts

    remote_hosts = [host for host in hosts if not _is_local_host(_parse_host(host)[0])]
    return remote_hosts or hosts


LEGACY_CFG = _load_cfg()


def _connect_to_host(host: str, port: int):
    conn = mysql.connector.connect(
        host=host,
        user=LEGACY_CFG["mysql_user"],
        password=LEGACY_CFG["mysql_pass"],
        database="comandas_db",
        port=port,
        charset="utf8mb4",
        use_pure=True,
        connection_timeout=10,
        client_flags=[ClientFlag.FOUND_ROWS],
    )
    cur = conn.cursor()
    try:
        cur.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute("SET collation_connection = 'utf8mb4_unicode_ci'")
    except mysql.connector.Error:
        pass
    cur.close()
    return conn


def get_legacy_connection():
    global _HOST_CACHE

    last_error = None
    errores = []
    hosts = _unique_hosts([_HOST_CACHE, *_mysql_hosts()])

    for entry in hosts:
        host, port = _parse_host(entry)
        try:
            if not _host_is_reachable(host, port):
                last_error = f"{host}:{port} no responde"
                errores.append(last_error)
                if entry == _HOST_CACHE:
                    _HOST_CACHE = None
                continue

            conn = _connect_to_host(host, port)
            _HOST_CACHE = entry
            return conn
        except Exception as exc:
            if getattr(exc, "errno", None) == errorcode.ER_ACCESS_DENIED_ERROR:
                last_error = (
                    f"{host}:{port} rechazo al usuario '{LEGACY_CFG['mysql_user']}'. "
                    "Autoriza ese usuario en MySQL para conexiones desde Tailscale."
                )
                errores.append(last_error)
                if entry == _HOST_CACHE:
                    _HOST_CACHE = None
                continue
            last_error = f"{host}:{port} - {exc}"
            errores.append(last_error)
            if entry == _HOST_CACHE:
                _HOST_CACHE = None

    detalle = "; ".join(errores) if errores else str(last_error or "sin detalle")
    raise RuntimeError(f"No se pudo conectar a MySQL legado. Intentos: {detalle}")


def _connect_morosos_to_host(host: str, port: int):
    conn = mysql.connector.connect(
        host=host,
        user=LEGACY_CFG["mysql_user"],
        password=LEGACY_CFG["mysql_pass"],
        port=port,
        charset="utf8mb4",
        use_pure=True,
        connection_timeout=10,
        client_flags=[ClientFlag.FOUND_ROWS],
    )
    cur = conn.cursor()
    try:
        cur.execute("CREATE DATABASE IF NOT EXISTS clientes_morosos CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.database = "clientes_morosos"
        cur.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute("SET collation_connection = 'utf8mb4_unicode_ci'")
    except mysql.connector.Error:
        pass
    cur.close()
    return conn


def _connect_editor_to_host(host: str, port: int):
    conn = mysql.connector.connect(
        host=host,
        user=LEGACY_CFG["mysql_user"],
        password=LEGACY_CFG["mysql_pass"],
        database="comandas_editor_db",
        port=port,
        charset="utf8mb4",
        use_pure=True,
        connection_timeout=10,
        client_flags=[ClientFlag.FOUND_ROWS],
    )
    cur = conn.cursor()
    try:
        cur.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute("SET collation_connection = 'utf8mb4_unicode_ci'")
    except mysql.connector.Error:
        pass
    cur.close()
    return conn


_EDITOR_CACHE = None


def get_editor_connection():
    global _EDITOR_CACHE
    last_error = None
    errores = []
    hosts = _unique_hosts([_EDITOR_CACHE, *_mysql_hosts()])
    for entry in hosts:
        host, port = _parse_host(entry)
        try:
            if not _host_is_reachable(host, port):
                last_error = f"{host}:{port} no responde"
                errores.append(last_error)
                if entry == _EDITOR_CACHE:
                    _EDITOR_CACHE = None
                continue
            conn = _connect_editor_to_host(host, port)
            _EDITOR_CACHE = entry
            return conn
        except Exception as exc:
            last_error = f"{host}:{port} - {exc}"
            errores.append(last_error)
            if entry == _EDITOR_CACHE:
                _EDITOR_CACHE = None
    detalle = "; ".join(errores) if errores else str(last_error or "sin detalle")
    raise RuntimeError(f"No se pudo conectar a comandas_editor_db. Intentos: {detalle}")


_MOROSOS_CACHE = None


def get_morosos_connection():
    global _MOROSOS_CACHE
    last_error = None
    errores = []
    hosts = _unique_hosts([_MOROSOS_CACHE, *_mysql_hosts()])
    for entry in hosts:
        host, port = _parse_host(entry)
        try:
            if not _host_is_reachable(host, port):
                last_error = f"{host}:{port} no responde"
                errores.append(last_error)
                if entry == _MOROSOS_CACHE:
                    _MOROSOS_CACHE = None
                continue
            conn = _connect_morosos_to_host(host, port)
            _MOROSOS_CACHE = entry
            return conn
        except Exception as exc:
            last_error = f"{host}:{port} - {exc}"
            errores.append(last_error)
            if entry == _MOROSOS_CACHE:
                _MOROSOS_CACHE = None
    detalle = "; ".join(errores) if errores else str(last_error or "sin detalle")
    raise RuntimeError(f"No se pudo conectar a clientes_morosos. Intentos: {detalle}")
