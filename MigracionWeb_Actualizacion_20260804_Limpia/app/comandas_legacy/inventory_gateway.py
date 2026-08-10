from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field


ALLOWED_DATABASES = {"inventarios", "comandas_editor_db", "comandas_db"}
ALLOWED_SQL_VERBS = {
    "SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN",
    "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "USE",
}
SESSION_TTL_SECONDS = 10 * 60
SIGNATURE_TOLERANCE_SECONDS = 90


class OpenSessionIn(BaseModel):
    database: str


class ExecuteIn(BaseModel):
    session_id: str
    sql: str
    params: Any = Field(default_factory=list)
    many: bool = False
    dict_rows: bool = True


class SessionIn(BaseModel):
    session_id: str


class _GatewaySession:
    def __init__(self, database: str, connection: Any):
        self.database = database
        self.connection = connection
        self.lock = threading.RLock()
        self.last_used = time.time()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return {"__bytes_b64__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _sql_verb(sql: str) -> str:
    cleaned = str(sql or "").lstrip()
    while cleaned.startswith("--"):
        cleaned = cleaned.split("\n", 1)[1].lstrip() if "\n" in cleaned else ""
    return cleaned.split(None, 1)[0].upper() if cleaned else ""


def create_inventory_gateway_router(
    connect_database: Callable[[str], Any],
    secret_provider: Callable[[], str],
) -> APIRouter:
    router = APIRouter(prefix="/inventory-gateway", tags=["inventory-gateway"])
    sessions: dict[str, _GatewaySession] = {}
    sessions_lock = threading.RLock()
    used_nonces: dict[str, float] = {}

    def cleanup() -> None:
        now = time.time()
        stale = []
        with sessions_lock:
            for session_id, session in sessions.items():
                if now - session.last_used > SESSION_TTL_SECONDS:
                    stale.append((session_id, session))
            for session_id, _ in stale:
                sessions.pop(session_id, None)
            expired_nonces = [n for n, used_at in used_nonces.items() if now - used_at > SIGNATURE_TOLERANCE_SECONDS * 2]
            for nonce in expired_nonces:
                used_nonces.pop(nonce, None)
        for _, session in stale:
            try:
                session.connection.close()
            except Exception:
                pass

    async def verify_request(request: Request) -> None:
        secret = str(secret_provider() or "")
        if not secret:
            raise HTTPException(status_code=503, detail="Inventory gateway secret is not configured")
        timestamp_text = request.headers.get("X-Inventory-Timestamp", "")
        nonce = request.headers.get("X-Inventory-Nonce", "")
        supplied = request.headers.get("X-Inventory-Signature", "")
        try:
            timestamp = int(timestamp_text)
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid gateway timestamp")
        now = int(time.time())
        if abs(now - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
            raise HTTPException(status_code=401, detail="Expired gateway request")
        if not nonce or len(nonce) > 100:
            raise HTTPException(status_code=401, detail="Invalid gateway nonce")
        body = await request.body()
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{timestamp_text}\n{nonce}\n{request.url.path}\n{body_hash}".encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Invalid gateway signature")
        with sessions_lock:
            if nonce in used_nonces:
                raise HTTPException(status_code=401, detail="Repeated gateway request")
            used_nonces[nonce] = time.time()
        cleanup()

    def get_session(session_id: str) -> _GatewaySession:
        with sessions_lock:
            session = sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Gateway session not found or expired")
        session.last_used = time.time()
        return session

    @router.post("/session/open", dependencies=[Depends(verify_request)])
    def open_session(payload: OpenSessionIn):
        database = payload.database.strip()
        if database not in ALLOWED_DATABASES:
            raise HTTPException(status_code=400, detail="Database is not allowed")
        connection = connect_database(database)
        if connection is None:
            raise HTTPException(status_code=503, detail=f"Database {database} is unavailable")
        session_id = secrets.token_urlsafe(32)
        with sessions_lock:
            sessions[session_id] = _GatewaySession(database, connection)
        return {"session_id": session_id, "database": database}

    @router.post("/execute", dependencies=[Depends(verify_request)])
    def execute(payload: ExecuteIn):
        session = get_session(payload.session_id)
        verb = _sql_verb(payload.sql)
        if verb not in ALLOWED_SQL_VERBS:
            raise HTTPException(status_code=400, detail=f"SQL operation {verb or 'empty'} is not allowed")
        if verb == "USE":
            requested = payload.sql.strip().rstrip(";").split(None, 1)[-1].strip(" `")
            if requested != session.database:
                raise HTTPException(status_code=400, detail="Cannot change the gateway database")
        try:
            with session.lock:
                cursor = session.connection.cursor(dictionary=payload.dict_rows)
                try:
                    if payload.many:
                        cursor.executemany(payload.sql, payload.params or [])
                    else:
                        cursor.execute(payload.sql, payload.params or ())
                    rows = cursor.fetchall() if getattr(cursor, "with_rows", False) else []
                    return {
                        "rows": _json_safe(rows),
                        "rowcount": int(cursor.rowcount or 0),
                        "lastrowid": _json_safe(getattr(cursor, "lastrowid", None)),
                    }
                finally:
                    cursor.close()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Database operation failed: {exc}")

    def transaction_action(payload: SessionIn, action: str):
        session = get_session(payload.session_id)
        try:
            with session.lock:
                getattr(session.connection, action)()
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Database {action} failed: {exc}")

    @router.post("/commit", dependencies=[Depends(verify_request)])
    def commit(payload: SessionIn):
        return transaction_action(payload, "commit")

    @router.post("/rollback", dependencies=[Depends(verify_request)])
    def rollback(payload: SessionIn):
        return transaction_action(payload, "rollback")

    @router.post("/session/close", dependencies=[Depends(verify_request)])
    def close_session(payload: SessionIn):
        with sessions_lock:
            session = sessions.pop(payload.session_id, None)
        if session is not None:
            try:
                session.connection.close()
            except Exception:
                pass
        return {"ok": True}

    return router
