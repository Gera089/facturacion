from fastapi import Depends, Header, HTTPException, Query

from app.db import get_user_by_token


def require_user(authorization: str | None = Header(default=None), token: str | None = Query(default=None)):
    token_str = token
    if authorization and authorization.lower().startswith("bearer "):
        token_str = authorization.split(" ", 1)[1].strip()
    if not token_str:
        raise HTTPException(status_code=401, detail="Sesion no valida.")
    user = get_user_by_token(token_str)
    if not user:
        raise HTTPException(status_code=401, detail="Sesion expirada o no encontrada.")
    return user


def require_admin(user: dict = Depends(require_user)):
    if str(user.get("role") or "").strip().lower() != "admin":
        raise HTTPException(status_code=403, detail="Se requiere perfil administrador.")
    return user
