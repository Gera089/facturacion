from .base import Migration

MODULES: dict[str, Migration] = {}


def register(m: Migration):
    MODULES[m.key] = m


def get_migration(key: str) -> Migration | None:
    return MODULES.get(key)


def list_migrations() -> list[dict]:
    return [
        {"key": m.key, "title": m.title, "description": m.description}
        for m in MODULES.values()
    ]


# Import modules so they register themselves
from . import clientes, productos, facturacion, impresion, cobranza, timbrado, reportes
