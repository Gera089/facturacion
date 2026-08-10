import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BUILD_ID = "2026.08.10-folios-ez-fe-sin-ceros-1"


def _unique_urls(urls: list[str]) -> list[str]:
    unique = []
    for url in urls:
        clean_url = str(url or "").strip().rstrip("/")
        if clean_url and clean_url not in unique:
            unique.append(clean_url)
    return unique


def _load_external_config(base_dir: Path, data_dir: Path | None = None) -> dict:
    env_path = os.environ.get("FACTURACION_CFG")
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    if data_dir and data_dir != base_dir:
        candidates.append(data_dir / "config.json")
    candidates.extend(
        [
            base_dir / "config.json",
            base_dir.parent / "config.json",
            base_dir.parent / "AspelAPI" / "config.json",
        ]
    )

    for path in candidates:
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh) or {}
        except Exception:
            pass
    return {}


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("FACTURACION_DATA_DIR") or BASE_DIR).resolve()
EXTERNAL_CFG = _load_external_config(BASE_DIR, DATA_DIR)


@dataclass(frozen=True)
class Settings:
    app_name: str = "Galacticos Web API"
    version: str = BUILD_ID
    host: str = str(EXTERNAL_CFG.get("migracion_web_host") or os.environ.get("MIGRACION_WEB_HOST") or "0.0.0.0")
    port: int = int(os.environ.get("MIGRACION_WEB_PORT") or EXTERNAL_CFG.get("migracion_web_port") or 8010)
    api_prefix: str = "/api"
    secret_key: str = EXTERNAL_CFG.get("secret_key") or os.environ.get("SECRET_KEY") or "migracion-web-galacticos-2026"
    base_dir: Path = BASE_DIR
    storage_dir: Path = DATA_DIR / "storage"
    db_path: Path = DATA_DIR / "storage" / "migracion_web.sqlite3"
    api_urls: list[str] = field(default_factory=list)
    crm_mysql_host: str = ""
    crm_mysql_port: int = 3307
    crm_mysql_user: str = ""
    crm_mysql_pass: str = ""
    crm_mysql_database: str = ""
    crm_db_path: str = ""
    catalog_vps_url: str = ""
    catalog_vps_email: str = ""
    catalog_vps_password: str = ""

    def __post_init__(self):
        raw_urls = EXTERNAL_CFG.get("migracion_web_api_urls") or EXTERNAL_CFG.get("api_web_urls") or []
        if isinstance(raw_urls, str):
            raw_urls = [raw_urls]
        primary_url = EXTERNAL_CFG.get("migracion_web_api_url") or EXTERNAL_CFG.get("api_web_url")
        defaults = [
            f"http://127.0.0.1:{self.port}",
            f"http://192.168.1.105:{self.port}",
            f"http://100.69.142.19:{self.port}",
        ]
        object.__setattr__(self, "api_urls", _unique_urls([primary_url, *raw_urls, *defaults]))
        object.__setattr__(self, "crm_mysql_host", EXTERNAL_CFG.get("mysql_host") or "")
        object.__setattr__(self, "crm_mysql_port", int(EXTERNAL_CFG.get("mysql_port") or 3307))
        object.__setattr__(self, "crm_mysql_user", EXTERNAL_CFG.get("mysql_user") or "")
        object.__setattr__(self, "crm_mysql_pass", EXTERNAL_CFG.get("mysql_pass") or "")
        object.__setattr__(self, "crm_mysql_database", EXTERNAL_CFG.get("mysql_database") or "comandas_db")
        object.__setattr__(self, "crm_db_path", EXTERNAL_CFG.get("crm_db_path") or "")
        object.__setattr__(self, "catalog_vps_url", (EXTERNAL_CFG.get("catalog_vps_url") or os.environ.get("CATALOGO_VPS_URL") or "").rstrip("/"))
        object.__setattr__(self, "catalog_vps_email", EXTERNAL_CFG.get("catalog_vps_email") or os.environ.get("CATALOGO_VPS_EMAIL") or "")
        object.__setattr__(self, "catalog_vps_password", EXTERNAL_CFG.get("catalog_vps_password") or os.environ.get("CATALOGO_VPS_PASSWORD") or "")


settings = Settings()
