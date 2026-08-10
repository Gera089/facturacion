import bcrypt

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import authenticate_user as _sqlite_auth, create_session, ensure_user, get_user_sections, is_user_blocked
from app.legacy_db import get_legacy_connection

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginPayload(BaseModel):
    username: str
    password: str


def _authenticate_mysql(username: str, password: str):
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT usuario, password, rol FROM usuarios WHERE TRIM(usuario) = %s",
            (username.strip(),),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return None

        hash_db = row.get("password")
        if not hash_db:
            return None

        if isinstance(hash_db, str):
            hash_bytes = hash_db.encode("utf-8")
        else:
            hash_bytes = bytes(hash_db)

        if not bcrypt.checkpw(password.encode("utf-8"), hash_bytes):
            return None

        return {
            "username": row["usuario"],
            "full_name": row["usuario"],
            "role": row.get("rol", "usuario"),
        }
    except Exception:
        return None


@router.post("/login")
def login(payload: LoginPayload):
    user_data = _authenticate_mysql(payload.username, payload.password)
    if not user_data:
        user_data = _sqlite_auth(payload.username, payload.password)
    if not user_data:
        raise HTTPException(status_code=401, detail="Usuario o password incorrectos.")
    local_user = ensure_user(
        user_data["username"],
        user_data["full_name"],
        user_data["role"],
    )
    if is_user_blocked(local_user["id"]):
        raise HTTPException(status_code=403, detail="Usuario bloqueado. Contacta al administrador.")
    token = create_session(local_user["id"])
    return {
        "token": token,
        "user": {
            "id": local_user["id"],
            "username": local_user["username"],
            "full_name": local_user["full_name"],
            "role": local_user["role"],
            "sections": get_user_sections(local_user["username"]),
        },
    }
