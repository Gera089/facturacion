from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import require_admin, require_user
from app.db import (
    block_user,
    ensure_user,
    get_user_activity,
    is_user_blocked,
    list_blocked_users,
    log_user_activity,
    unblock_user,
)
from app.legacy_db import get_legacy_connection

router = APIRouter(prefix="/api/support", tags=["support"])


class ActivityLog(BaseModel):
    user_id: int
    action: str
    detail: str = ""
    ip_address: str = ""


class BlockUser(BaseModel):
    reason: str = ""


def _local_user_from_legacy_id(legacy_user_id: int) -> dict:
    """Convierte el ID de usuarios MySQL que usa la interfaz al ID local."""
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT usuario, rol FROM usuarios WHERE id = %s LIMIT 1",
            (legacy_user_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        username = str(row.get("usuario") or "").strip()
        if not username:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")
        return ensure_user(username, username, str(row.get("rol") or "consulta"))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/activity/{user_id}")
def list_activity(user_id: int, limit: int = 50, user=Depends(require_user)):
    return get_user_activity(user_id, limit)


@router.post("/activity")
def log_activity(payload: ActivityLog, user=Depends(require_user)):
    log_user_activity(payload.user_id, payload.action, payload.detail, payload.ip_address)
    return {"mensaje": "Actividad registrada"}


@router.post("/block/{user_id}")
def block(user_id: int, payload: BlockUser = BlockUser(), current_user=Depends(require_admin)):
    target_user = _local_user_from_legacy_id(user_id)
    if target_user["id"] == current_user["id"]:
        raise HTTPException(400, "No puedes bloquear tu propia cuenta.")
    if is_user_blocked(target_user["id"]):
        raise HTTPException(400, "El usuario ya esta bloqueado")
    block_user(target_user["id"], current_user["id"], payload.reason)
    log_user_activity(current_user["id"], "bloqueo", f"Bloqueo al usuario {target_user['username']}: {payload.reason}")
    return {"mensaje": "Usuario bloqueado"}


@router.post("/unblock/{user_id}")
def unblock(user_id: int, current_user=Depends(require_admin)):
    target_user = _local_user_from_legacy_id(user_id)
    if not is_user_blocked(target_user["id"]):
        raise HTTPException(400, "El usuario no esta bloqueado")
    unblock_user(target_user["id"])
    log_user_activity(current_user["id"], "desbloqueo", f"Desbloqueo al usuario {target_user['username']}")
    return {"mensaje": "Usuario desbloqueado"}


@router.get("/blocked")
def blocked_list(user=Depends(require_admin)):
    return list_blocked_users()
