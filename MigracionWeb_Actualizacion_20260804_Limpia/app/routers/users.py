import bcrypt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import require_admin
from app.db import delete_user_sections, get_user_sections, set_user_sections
from app.legacy_db import get_legacy_connection

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str = ""
    role: str = "consulta"
    sections: list[str] | None = None


class UserUpdate(BaseModel):
    full_name: str = ""
    role: str = "consulta"
    active: int = 1
    sections: list[str] | None = None


class UserPassword(BaseModel):
    password: str


@router.get("")
def list_users(user=Depends(require_admin)):
    conn = get_legacy_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, usuario AS username, usuario AS full_name, rol AS role, activo AS active, fecha_creacion AS created_at "
            "FROM usuarios ORDER BY usuario"
        )
        rows = cursor.fetchall()
        for row in rows:
            row["sections"] = get_user_sections(row.get("username"))
        return rows
    except Exception as e:
        raise HTTPException(500, f"listar usuarios: {e}")
    finally:
        conn.close()


@router.post("")
def create_user(payload: UserCreate, user=Depends(require_admin)):
    conn = get_legacy_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM usuarios WHERE usuario = %s", (payload.username.strip(),))
        if cursor.fetchone():
            raise HTTPException(400, "El usuario ya existe")
        pw_hash = bcrypt.hashpw(payload.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        cursor.execute(
            "INSERT INTO usuarios (usuario, password, rol, activo) VALUES (%s, %s, %s, 1)",
            (payload.username.strip(), pw_hash, payload.role),
        )
        conn.commit()
        if payload.sections is not None:
            set_user_sections(payload.username, payload.sections)
        return {"id": cursor.lastrowid, "mensaje": "Usuario creado"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"crear usuario: {e}")
    finally:
        conn.close()


@router.put("/{user_id}")
def update_user(user_id: int, payload: UserUpdate, user=Depends(require_admin)):
    conn = get_legacy_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, usuario FROM usuarios WHERE id = %s", (user_id,))
        existing = cursor.fetchone()
        if not existing:
            raise HTTPException(404, "Usuario no encontrado")
        cursor.execute(
            "UPDATE usuarios SET rol = %s, activo = %s WHERE id = %s",
            (payload.role, payload.active, user_id),
        )
        conn.commit()
        if payload.sections is not None:
            set_user_sections(existing["usuario"], payload.sections)
        return {"mensaje": "Usuario actualizado"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"actualizar usuario: {e}")
    finally:
        conn.close()


@router.put("/{user_id}/password")
def reset_password(user_id: int, payload: UserPassword, user=Depends(require_admin)):
    conn = get_legacy_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id FROM usuarios WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            raise HTTPException(404, "Usuario no encontrado")
        pw_hash = bcrypt.hashpw(payload.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        cursor.execute("UPDATE usuarios SET password = %s WHERE id = %s", (pw_hash, user_id))
        conn.commit()
        return {"mensaje": "Contrasena actualizada"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"cambiar contrasena: {e}")
    finally:
        conn.close()


@router.delete("/{user_id}")
def delete_user(user_id: int, user=Depends(require_admin)):
    conn = get_legacy_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, usuario FROM usuarios WHERE id = %s", (user_id,))
        existing = cursor.fetchone()
        if not existing:
            raise HTTPException(404, "Usuario no encontrado")
        if existing["usuario"].lower() == "admin":
            raise HTTPException(400, "No se puede eliminar el usuario admin")
        cursor.execute("DELETE FROM usuarios WHERE id = %s", (user_id,))
        conn.commit()
        delete_user_sections(existing["usuario"])
        return {"mensaje": "Usuario eliminado"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, f"eliminar usuario: {e}")
    finally:
        conn.close()
