import io
import json
import mimetypes
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pandas as pd
import fitz
from PIL import Image
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.core.config import settings
from app.dependencies import require_admin, require_user
from app.legacy_db import get_legacy_connection
from app.comandas_legacy import api_vendedor as catalogo_legacy
from app.comandas_legacy.api_vendedor import (
    CatalogoPdfIn,
    ProductoFichaIn,
    SeleccionarPrincipalIn,
    get_ficha_pdf,
    listar_productos_catalogo,
    normalizar_nombre_empresa_catalogo,
    post_catalogo_pdf,
)


router = APIRouter(prefix="/api/products", tags=["products"])
REMOTE_CATALOG_BASE = os.environ.get("FACTURACION_CATALOGOS_REMOTE_URL", "http://100.69.142.19:8000").rstrip("/")
REMOTE_CATALOG_ENABLED = os.environ.get("FACTURACION_CATALOGOS_REMOTE_ENABLED", "").strip().lower() in {"1", "true", "si", "sí", "yes"}
CATALOG_VPS_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CATALOG_VPS_SYNC_JOBS: dict[str, dict] = {}
CATALOG_VPS_SYNC_LOCK = threading.Lock()


def _dict_rows(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _to_float(value) -> float:
    try:
        text = str(value or "").strip().replace("$", "").replace(",", "")
        if not text or text.lower() == "nan":
            return 0.0
        return float(text)
    except Exception:
        return 0.0


def _normalize_text(value) -> str:
    text = str(value or "").strip().lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    text = text.replace(".", "").replace("_", "").replace("-", "")
    return re.sub(r"\s+", "", text)


def _normalize_iva(value) -> str:
    text = _normalize_text(value)
    if text in {"si", "s", "1", "true", "t", "yes", "y", "x", "16", "16%", "gravado", "coniva"}:
        return "Sí"
    return "No"


def _remote_catalog_url(path: str, params: dict | None = None) -> str:
    url = f"{REMOTE_CATALOG_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def _remote_json(path: str, params: dict | None = None):
    if not REMOTE_CATALOG_ENABLED:
        return None
    try:
        with urllib.request.urlopen(_remote_catalog_url(path, params), timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _remote_response(path: str, params: dict | None = None, payload: dict | None = None) -> Response | None:
    remote = _remote_bytes(path, params=params, payload=payload)
    if remote is None:
        return None
    content, content_type, response_headers = remote
    return Response(content=content, media_type=content_type, headers=response_headers)


def _remote_bytes(path: str, params: dict | None = None, payload: dict | None = None) -> tuple[bytes, str, dict] | None:
    if not REMOTE_CATALOG_ENABLED:
        return None
    try:
        data = None
        headers = {}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        req = urllib.request.Request(_remote_catalog_url(path, params), data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            content_type = resp.headers.get("Content-Type") or "application/octet-stream"
            disposition = resp.headers.get("Content-Disposition")
            response_headers = {}
            if disposition:
                response_headers["Content-Disposition"] = disposition
            return content, content_type, response_headers
    except Exception:
        return None


def _catalog_vps_request(
    opener: urllib.request.OpenerDirector,
    path_or_url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: int = 40,
) -> tuple[bytes, str]:
    if path_or_url.lower().startswith(("http://", "https://")):
        url = path_or_url
    else:
        url = f"{settings.catalog_vps_url}{path_or_url if path_or_url.startswith('/') else '/' + path_or_url}"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type") or "application/octet-stream"


def _catalog_vps_opener() -> urllib.request.OpenerDirector:
    if not settings.catalog_vps_url or not settings.catalog_vps_email or not settings.catalog_vps_password:
        raise HTTPException(
            status_code=400,
            detail="Configura catalog_vps_url, catalog_vps_email y catalog_vps_password en config.json.",
        )
    cookie_jar = urllib.request.HTTPCookieProcessor()
    opener = urllib.request.build_opener(cookie_jar)
    try:
        _catalog_vps_request(
            opener,
            "/api/auth/login",
            method="POST",
            payload={"email": settings.catalog_vps_email, "password": settings.catalog_vps_password},
            timeout=20,
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore") or str(exc)
        raise HTTPException(status_code=502, detail=f"No se pudo iniciar sesion en el catalogo VPS: {detail}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo conectar al catalogo VPS: {exc}")
    return opener


def _catalog_vps_json(opener: urllib.request.OpenerDirector, path: str):
    try:
        raw, _ = _catalog_vps_request(opener, path, timeout=60)
        return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore") or str(exc)
        raise HTTPException(status_code=502, detail=f"El catalogo VPS rechazo la consulta: {detail}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo leer el catalogo VPS: {exc}")


def _catalog_vps_absolute_url(url: str | None) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    return urllib.parse.urljoin(settings.catalog_vps_url + "/", text.lstrip("/"))


def _catalog_vps_clean_text(value) -> str:
    return " ".join(str(value or "").strip().split())


def _catalog_vps_image_extension(url: str, content_type: str) -> str:
    guessed = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip()) or ""
    if guessed.lower() == ".jpe":
        guessed = ".jpg"
    if guessed.lower() in CATALOG_VPS_ALLOWED_IMAGE_EXTENSIONS:
        return guessed.lower()
    path_ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    if path_ext in CATALOG_VPS_ALLOWED_IMAGE_EXTENSIONS:
        return path_ext
    return ".jpg"


def _catalog_vps_download_image(opener: urllib.request.OpenerDirector, image_url: str, empresa: str, cip: str) -> tuple[str, str, bool] | None:
    absolute_url = _catalog_vps_absolute_url(image_url)
    if not absolute_url:
        return None
    carpeta = catalogo_legacy.ruta_fichas(catalogo_legacy.normalizar_empresa_para_carpeta(empresa), str(cip).strip())
    marker_path = os.path.join(carpeta, "principal.source_url.txt")
    try:
        if os.path.isdir(carpeta) and os.path.isfile(marker_path):
            previous_url = open(marker_path, "r", encoding="utf-8").read().strip()
            if previous_url == absolute_url:
                for ext in CATALOG_VPS_ALLOWED_IMAGE_EXTENSIONS:
                    existing_path = os.path.join(carpeta, f"principal{ext}")
                    if os.path.isfile(existing_path):
                        return existing_path, ext, False
    except Exception:
        pass
    try:
        raw, content_type = _catalog_vps_request(opener, absolute_url, timeout=45)
    except Exception:
        return None
    if not raw:
        return None
    ext = _catalog_vps_image_extension(absolute_url, content_type)
    os.makedirs(carpeta, exist_ok=True)
    destino = os.path.join(carpeta, f"principal{ext}")
    with open(destino, "wb") as fh:
        fh.write(raw)
    try:
        with open(marker_path, "w", encoding="utf-8") as fh:
            fh.write(absolute_url)
    except Exception:
        pass
    return destino, ext, True


def _catalog_vps_local_product_exists(cip: str) -> bool:
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM productos WHERE TRIM(cip) = TRIM(%s) LIMIT 1", (str(cip).strip(),))
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _catalog_vps_local_product_cips() -> set[str]:
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT cip FROM productos")
        return {str(row[0] or "").strip() for row in cursor.fetchall() if str(row[0] or "").strip()}
    except Exception:
        return set()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _catalog_vps_first_image(product: dict) -> str:
    direct = str(product.get("image_url") or "").strip()
    if direct:
        return direct
    images = product.get("images")
    if isinstance(images, list):
        for item in images:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                for key in ("url", "image_url", "src", "path"):
                    value = str(item.get(key) or "").strip()
                    if value:
                        return value
    return ""


def _catalog_vps_payload(empresa: str, product: dict, existing: dict | None, image_info: tuple[str, str, bool] | None) -> ProductoFichaIn:
    existing = existing or {}
    sku = _catalog_vps_clean_text(product.get("sku"))
    name = _catalog_vps_clean_text(product.get("name"))
    category = _catalog_vps_clean_text(product.get("category"))
    brand = _catalog_vps_clean_text(product.get("brand"))
    short_description = _catalog_vps_clean_text(product.get("short_description"))
    long_description = _catalog_vps_clean_text(product.get("long_description"))
    presentation = _catalog_vps_clean_text(product.get("presentation"))
    sale_unit = _catalog_vps_clean_text(product.get("sale_unit"))
    master_case = _catalog_vps_clean_text(product.get("master_case"))
    origin = _catalog_vps_clean_text(product.get("origin"))
    content_parts = [part for part in (presentation, sale_unit, master_case) if part]
    contenido = " | ".join(content_parts)
    return ProductoFichaIn(
        empresa=empresa,
        cip=sku,
        empresas_relacionadas=[empresa],
        extension=image_info[1] if image_info else existing.get("extension"),
        nombre_producto=name or existing.get("nombre_producto"),
        titulo_ficha=name or existing.get("titulo_ficha"),
        marca=brand or existing.get("marca"),
        subtitulo=brand or existing.get("subtitulo"),
        categoria=category or existing.get("categoria"),
        tipo_producto=category or existing.get("tipo_producto"),
        contenido_neto=contenido or existing.get("contenido_neto"),
        presentacion=presentation or existing.get("presentacion"),
        origen=origin or existing.get("origen"),
        descripcion_corta=short_description or existing.get("descripcion_corta"),
        texto_comercial=long_description or existing.get("texto_comercial"),
        observaciones_ficha=long_description or existing.get("observaciones_ficha"),
        ingredientes=existing.get("ingredientes"),
        conservacion=existing.get("conservacion"),
        maduracion=existing.get("maduracion"),
        peso_aprox=existing.get("peso_aprox"),
        ean=existing.get("ean"),
        imagen_path=image_info[0] if image_info else existing.get("imagen_path"),
        badge_1=existing.get("badge_1"),
        badge_2=existing.get("badge_2"),
        badge_3=existing.get("badge_3"),
        etiquetas_retail=existing.get("etiquetas_retail") or [],
        premium_sort=int(existing.get("premium_sort") or 0),
        premium_activo=int(existing.get("premium_activo") or 1),
        activo=1 if product.get("is_active", True) is not False else 0,
    )


def _catalog_vps_existing_fichas(empresa: str, cips: list[str]) -> dict[str, dict]:
    cips = [str(cip or "").strip() for cip in cips if str(cip or "").strip()]
    if not cips:
        return {}
    conn = catalogo_legacy.conectar_mysql()
    if not conn:
        return {}
    try:
        placeholders = ",".join(["%s"] * len(cips))
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"""
            SELECT *
            FROM productos_ficha
            WHERE TRIM(COALESCE(empresa, '')) = TRIM(%s)
              AND TRIM(COALESCE(cip, '')) IN ({placeholders})
            """,
            [empresa, *cips],
        )
        rows = cur.fetchall() or []
        cur.close()
        return {str(row.get("cip") or "").strip(): row for row in rows if str(row.get("cip") or "").strip()}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _catalog_vps_save_ficha_fast(cursor, payload: ProductoFichaIn):
    def clean(value):
        return catalogo_legacy.texto_seguro(value) or None

    nombre_producto = clean(payload.nombre_producto) or clean(payload.titulo_ficha)
    marca = clean(payload.marca) or clean(payload.subtitulo)
    categoria = clean(payload.categoria) or clean(payload.tipo_producto)
    contenido_neto = clean(payload.contenido_neto) or clean(payload.peso_aprox)
    observaciones_ficha = clean(payload.observaciones_ficha) or clean(payload.texto_comercial)
    titulo_ficha = clean(payload.titulo_ficha) or nombre_producto
    subtitulo = clean(payload.subtitulo) or marca
    tipo_producto = clean(payload.tipo_producto) or categoria
    peso_aprox = clean(payload.peso_aprox) or contenido_neto
    texto_comercial = clean(payload.texto_comercial) or observaciones_ficha
    etiquetas_retail = catalogo_legacy.lista_a_csv(payload.etiquetas_retail)
    cursor.execute(
        """
        INSERT INTO productos_ficha (
            empresa, cip, extension,
            nombre_producto, marca, categoria, contenido_neto, presentacion,
            ingredientes, conservacion, origen, ean,
            descripcion_corta, observaciones_ficha,
            titulo_ficha, subtitulo, tipo_producto, maduracion,
            peso_aprox, texto_comercial, imagen_path,
            badge_1, badge_2, badge_3, etiquetas_retail, premium_sort, premium_activo,
            activo, fecha_actualizacion
        )
        VALUES (
            %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, NOW()
        )
        ON DUPLICATE KEY UPDATE
            extension = VALUES(extension),
            nombre_producto = VALUES(nombre_producto),
            marca = VALUES(marca),
            categoria = VALUES(categoria),
            contenido_neto = VALUES(contenido_neto),
            presentacion = VALUES(presentacion),
            ingredientes = VALUES(ingredientes),
            conservacion = VALUES(conservacion),
            origen = VALUES(origen),
            ean = VALUES(ean),
            descripcion_corta = VALUES(descripcion_corta),
            observaciones_ficha = VALUES(observaciones_ficha),
            titulo_ficha = VALUES(titulo_ficha),
            subtitulo = VALUES(subtitulo),
            tipo_producto = VALUES(tipo_producto),
            maduracion = VALUES(maduracion),
            peso_aprox = VALUES(peso_aprox),
            texto_comercial = VALUES(texto_comercial),
            imagen_path = VALUES(imagen_path),
            badge_1 = VALUES(badge_1),
            badge_2 = VALUES(badge_2),
            badge_3 = VALUES(badge_3),
            etiquetas_retail = VALUES(etiquetas_retail),
            premium_sort = VALUES(premium_sort),
            premium_activo = VALUES(premium_activo),
            activo = VALUES(activo),
            fecha_actualizacion = NOW()
        """,
        (
            clean(payload.empresa),
            clean(payload.cip),
            clean(payload.extension),
            nombre_producto,
            marca,
            categoria,
            contenido_neto,
            clean(payload.presentacion),
            clean(payload.ingredientes),
            clean(payload.conservacion),
            clean(payload.origen),
            clean(payload.ean),
            clean(payload.descripcion_corta),
            observaciones_ficha,
            titulo_ficha,
            subtitulo,
            tipo_producto,
            clean(payload.maduracion),
            peso_aprox,
            texto_comercial,
            clean(payload.imagen_path),
            clean(payload.badge_1),
            clean(payload.badge_2),
            clean(payload.badge_3),
            etiquetas_retail,
            int(payload.premium_sort or 0),
            int(payload.premium_activo or 0),
            int(payload.activo or 0),
        ),
    )


def _compress_pdf_embedded_images(
    pdf_bytes: bytes,
    max_dimension: int = 1200,
    jpeg_quality: int = 72,
    min_dimension_to_compress: int = 420,
) -> bytes:
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        return pdf_bytes
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    replaced: set[int] = set()
    try:
        for page in doc:
            for image_info in page.get_images(full=True):
                xref = int(image_info[0])
                if xref in replaced:
                    continue
                try:
                    extracted = doc.extract_image(xref)
                    source = extracted.get("image") or b""
                    if not source:
                        continue
                    with Image.open(io.BytesIO(source)) as img:
                        if max(img.size) < min_dimension_to_compress:
                            continue
                        work = img.convert("RGBA") if img.mode in {"RGBA", "LA", "P"} else img.convert("RGB")
                        if work.mode == "RGBA":
                            bg = Image.new("RGB", work.size, (255, 255, 255))
                            bg.paste(work, mask=work.getchannel("A"))
                            work = bg
                        if max(work.size) > max_dimension:
                            work.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                        out = io.BytesIO()
                        work.save(out, format="JPEG", quality=jpeg_quality, optimize=True, progressive=True)
                        optimized = out.getvalue()
                    if len(optimized) < len(source):
                        page.replace_image(xref, stream=optimized)
                        replaced.add(xref)
                except Exception:
                    continue
        compressed = doc.tobytes(garbage=4, deflate=True, clean=True)
        return compressed if len(compressed) < len(pdf_bytes) else pdf_bytes
    finally:
        doc.close()


def _load_price_lists(cursor):
    cursor.execute(
        """
        SELECT id, nombre, IFNULL(descripcion, '') AS descripcion
        FROM listas_precios
        ORDER BY id
        """
    )
    return _dict_rows(cursor)


def _load_products():
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                p.cip,
                p.descripcion,
                p.unidad,
                IFNULL(p.iva, 'No') AS iva,
                IFNULL(p.descuento, 'No') AS descuento,
                IFNULL(p.codigo_barras, '') AS codigo_barras
            FROM productos p
            ORDER BY p.cip ASC
            """
        )
        products = _dict_rows(cursor)
        product_map = {str(item["cip"]): item for item in products}

        cursor.execute(
            """
            SELECT
                pp.cip,
                lp.nombre AS lista_nombre,
                pp.precio,
                COALESCE(pp.codigo_barras, '') AS codigo_barras
            FROM precios_productos pp
            INNER JOIN listas_precios lp ON lp.id = pp.lista_id
            """
        )
        prices = _dict_rows(cursor)
        price_lists = _load_price_lists(cursor)

        detected_lists = set()
        lists_with_barcodes = set()
        for row in prices:
            cip = str(row.get("cip") or "")
            lista = str(row.get("lista_nombre") or "")
            if cip not in product_map or not lista:
                continue
            detected_lists.add(lista)
            barcode = str(row.get("codigo_barras") or "").strip()
            if barcode:
                lists_with_barcodes.add(lista)
            product_map[cip].setdefault("precios", {})[lista] = {
                "precio": float(row.get("precio") or 0),
                "codigo_barras": barcode,
            }

        return {
            "items": list(product_map.values()),
            "lists": sorted(detected_lists),
            "price_lists": price_lists,
            "lists_with_barcodes": sorted(lists_with_barcodes),
        }
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _list_catalog_products_fast(q: str, empresa: str, limit: int) -> list[dict]:
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
            cursor.execute("SET collation_connection = 'utf8mb4_unicode_ci'")
        except Exception:
            pass
        query = """
            SELECT
                p.cip,
                p.descripcion,
                p.unidad,
                MAX(CASE WHEN COALESCE(pf.activo, 0) = 1 THEN 1 ELSE 0 END) AS tiene_ficha
            FROM productos p
            LEFT JOIN productos_ficha pf
                ON TRIM(COALESCE(pf.cip, '')) = TRIM(COALESCE(p.cip, ''))
               AND TRIM(COALESCE(pf.empresa, '')) = TRIM(%s)
            WHERE 1=1
        """
        params = [empresa]
        search = str(q or "").strip()
        if search:
            query += """
                AND (
                    p.cip LIKE %s
                    OR p.descripcion LIKE %s
                )
            """
            params.extend([f"%{search}%", f"%{search}%"])
        query += """
            GROUP BY p.cip, p.descripcion, p.unidad
            ORDER BY p.descripcion
            LIMIT %s
        """
        params.append(int(limit or 200))
        cursor.execute(query, params)
        rows = _dict_rows(cursor)
        return [
            {
                "cip": row.get("cip"),
                "descripcion": row.get("descripcion"),
                "unidad": row.get("unidad"),
                "tieneFicha": bool(row.get("tiene_ficha")),
                "fichaUrl": f"/catalogos/ficha?empresa={empresa}&cip={row.get('cip')}" if bool(row.get("tiene_ficha")) else None,
            }
            for row in rows
        ]
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("")
def list_products(
    user=Depends(require_user),
    q: str | None = Query(default=None),
):
    try:
        payload = _load_products()
        query_text = (q or "").strip().lower()
        items = payload["items"]
        if query_text:
            items = [
                item for item in items
                if query_text in str(item.get("cip") or "").lower()
                or query_text in str(item.get("descripcion") or "").lower()
            ]
        return {
            "items": items,
            "count": len(items),
            "lists": payload["lists"],
            "price_lists": payload["price_lists"],
            "lists_with_barcodes": payload["lists_with_barcodes"],
            "filters": {"q": query_text},
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/lists")
def list_price_lists(user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        return {"items": _load_price_lists(cursor)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/catalogo/empresas")
def catalogo_empresas(user=Depends(require_user)):
    base_empresas = ["Gourmet España", "Ibersur", "EZA2007", "Alimentos Europeos", "Aldeu"]
    conn = None
    cursor = None
    empresas = []
    vistos = set()

    def add(nombre):
        nombre = normalizar_nombre_empresa_catalogo(str(nombre or "").strip())
        key = _normalize_text(nombre)
        if nombre and key not in vistos:
            vistos.add(key)
            empresas.append(nombre)

    for nombre in base_empresas:
        add(nombre)

    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        for sql in (
            "SELECT DISTINCT empresa FROM productos_ficha WHERE TRIM(COALESCE(empresa,'')) <> '' ORDER BY empresa",
            "SELECT DISTINCT empresa FROM clientes WHERE TRIM(COALESCE(empresa,'')) <> '' ORDER BY empresa",
        ):
            try:
                cursor.execute(sql)
                for row in cursor.fetchall():
                    add(row[0])
            except Exception:
                continue
        return empresas
    except Exception:
        return empresas
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/catalogo/productos")
def catalogo_productos(
    empresa: str = Query(..., min_length=1),
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=200, ge=1, le=1000),
    user=Depends(require_user),
):
    try:
        remote = _remote_json("/catalogos/productos", {"empresa": empresa, "q": q, "limit": limit})
        if isinstance(remote, list):
            return remote
        return _list_catalog_products_fast(q=q, empresa=empresa, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/catalogo/ficha-pdf")
def catalogo_ficha_pdf(
    empresa: str = Query(..., min_length=1),
    cip: str = Query(..., min_length=1),
    user=Depends(require_user),
):
    # Priorizar una ficha local cuando su imagen está disponible. Así se evita
    # devolver un PDF remoto incompleto cuando el catálogo remoto no puede leer
    # la carpeta compartida de fotografías.
    try:
        ficha_local = catalogo_legacy.obtener_ficha_producto_nueva(empresa, cip)
        if ficha_local and catalogo_legacy.resolver_imagen_producto(ficha_local):
            return get_ficha_pdf(empresa=empresa, cip=cip)
    except Exception:
        pass
    remote = _remote_response("/catalogos/ficha-pdf", {"empresa": empresa, "cip": cip})
    if remote is not None:
        return remote
    return get_ficha_pdf(empresa=empresa, cip=cip)


@router.post("/catalogo/catalogo-pdf")
def catalogo_pdf(payload: dict = Body(...), user=Depends(require_user)):
    try:
        # Entregar el PDF original, igual que "Ver ficha PDF". La recompresión
        # posterior perdía la máscara alfa de logos/imágenes transparentes.
        return post_catalogo_pdf(CatalogoPdfIn(**payload))
        remote = _remote_bytes("/catalogos/catalogo-pdf", payload=payload)
        if remote is not None:
            content, content_type, response_headers = remote
            if "pdf" in content_type.lower():
                original_size = len(content)
                content = _compress_pdf_embedded_images(content)
                # El contenido cambia al comprimir; no reenvíes el tamaño del
                # PDF original porque Uvicorn corta la respuesta y el navegador
                # reporta “Failed to fetch”.
                response_headers.pop("content-length", None)
                response_headers.pop("Content-Length", None)
                response_headers["X-Original-Pdf-Bytes"] = str(original_size)
                response_headers["X-Optimized-Pdf-Bytes"] = str(len(content))
                response_headers["X-Pdf-Optimization"] = "embedded-images"
            return Response(content=content, media_type=content_type, headers=response_headers)
        local = post_catalogo_pdf(CatalogoPdfIn(**payload))
        if getattr(local, "media_type", "") == "application/pdf" and hasattr(local, "body"):
            original_size = len(local.body)
            content = _compress_pdf_embedded_images(local.body)
            headers = dict(getattr(local, "headers", {}) or {})
            headers.pop("content-length", None)
            headers.pop("Content-Length", None)
            headers["X-Original-Pdf-Bytes"] = str(original_size)
            headers["X-Optimized-Pdf-Bytes"] = str(len(content))
            headers["X-Pdf-Optimization"] = "embedded-images"
            return Response(content=content, media_type="application/pdf", headers=headers)
        return local
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/catalogo/admin/ficha")
def catalogo_admin_ficha(
    empresa: str = Query(..., min_length=1),
    cip: str = Query(..., min_length=1),
    user=Depends(require_user),
):
    try:
        # La ficha guardada por Administración vive en la base local. Esta es
        # siempre la fuente principal al abrir el formulario de edición.
        return catalogo_legacy._resolver_ficha_admin_para_empresa(empresa, cip)
    except HTTPException:
        remote = _remote_json("/catalogos/ficha-data", {"empresa": empresa, "cip": cip})
        if isinstance(remote, dict):
            return remote
        raise


@router.put("/catalogo/admin/ficha")
def catalogo_admin_guardar_ficha(payload: dict = Body(...), request: Request = None, user=Depends(require_user)):
    try:
        data = ProductoFichaIn(**payload)
        if not data.empresas_relacionadas:
            data = data.model_copy(update={"empresas_relacionadas": [data.empresa]})
        resultado = catalogo_legacy._upsert_ficha_data_impl(data)
        catalogo_legacy.registrar_bitacora(
            user,
            "EDITAR_FICHA",
            empresa=data.empresa,
            cip=data.cip,
            detalle="Guardado desde Productos > Catálogo",
            request=request,
        )
        return catalogo_legacy._adjuntar_empresas_relacionadas_ficha(resultado, data.empresa)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/catalogo/admin/imagen")
def catalogo_admin_imagen(
    empresa: str = Query(..., min_length=1),
    cip: str = Query(..., min_length=1),
    archivo: str = Query(..., min_length=1),
    user=Depends(require_user),
):
    safe_file = os.path.basename(str(archivo or "").strip())
    carpeta = catalogo_legacy.ruta_fichas(catalogo_legacy.normalizar_empresa_para_carpeta(empresa), str(cip).strip())
    ruta = os.path.join(carpeta, safe_file)
    if os.path.isfile(ruta):
        return FileResponse(ruta, media_type=mimetypes.guess_type(ruta)[0] or "application/octet-stream")
    remote = _remote_response("/catalogos/ficha", {"empresa": empresa, "cip": cip})
    if remote is not None:
        return remote
    raise HTTPException(status_code=404, detail="La imagen no existe")


@router.post("/catalogo/admin/imagen/subir")
def catalogo_admin_subir_imagen(
    request: Request,
    empresa: str = Form(...),
    cip: str = Form(...),
    tipo_imagen: str = Form("principal"),
    indice: int = Form(1),
    archivo: UploadFile = File(...),
    user=Depends(require_user),
):
    try:
        return catalogo_legacy.admin_api_subir_imagen(
            request=request,
            empresa=empresa,
            cip=cip,
            tipo_imagen=tipo_imagen,
            indice=indice,
            archivo=archivo,
            usuario=user,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/catalogo/admin/imagen/eliminar")
def catalogo_admin_eliminar_imagen(payload: dict = Body(...), request: Request = None, user=Depends(require_user)):
    try:
        return catalogo_legacy.admin_api_eliminar_imagen(
            payload=SeleccionarPrincipalIn(**payload),
            request=request,
            usuario=user,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/catalogo/admin/bitacora")
def catalogo_admin_bitacora(
    usuario: str | None = Query(default=None),
    accion: str | None = Query(default=None),
    empresa: str | None = Query(default=None),
    cip: str | None = Query(default=None),
    fecha_inicio: str | None = Query(default=None),
    fecha_fin: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    user=Depends(require_user),
):
    return catalogo_legacy.admin_api_bitacora(
        usuario_filtro=usuario,
        accion=accion,
        empresa=empresa,
        cip=cip,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        limit=limit,
        usuario=user,
    )


def _catalog_vps_job_update(job_id: str | None, **values):
    if not job_id:
        return
    with CATALOG_VPS_SYNC_LOCK:
        job = CATALOG_VPS_SYNC_JOBS.get(job_id)
        if not job:
            return
        job.update(values)
        job["updated_at"] = time.time()


def _catalog_vps_job_snapshot(job_id: str) -> dict | None:
    with CATALOG_VPS_SYNC_LOCK:
        job = CATALOG_VPS_SYNC_JOBS.get(job_id)
        return dict(job) if job else None


def _catalog_vps_sync_impl(payload: dict, user: dict, request: Request | None = None, job_id: str | None = None):
    empresa = normalizar_nombre_empresa_catalogo(str(payload.get("empresa") or "").strip())
    if not empresa:
        raise HTTPException(status_code=400, detail="Selecciona la empresa local que recibira la actualizacion.")
    dry_run = bool(payload.get("dry_run") or False)
    limit = int(payload.get("limit") or 0)
    _catalog_vps_job_update(job_id, status="running", etapa="Conectando al catalogo VPS", empresa=empresa)
    opener = _catalog_vps_opener()
    _catalog_vps_job_update(job_id, etapa="Leyendo productos del VPS")
    products = _catalog_vps_json(opener, "/api/admin/products")
    if not isinstance(products, list):
        raise HTTPException(status_code=502, detail="El catalogo VPS no devolvio una lista de productos.")
    if limit > 0:
        products = products[:limit]

    summary = {
        "empresa": empresa,
        "dry_run": dry_run,
        "total_remoto": len(products),
        "con_sku": 0,
        "encontrados_local": 0,
        "actualizados": 0,
        "imagenes_descargadas": 0,
        "omitidos_sin_sku": 0,
        "omitidos_no_local": 0,
        "procesadas": 0,
        "faltantes": 0,
        "errores": [],
    }
    updated_cips: list[str] = []
    _catalog_vps_job_update(job_id, etapa="Leyendo productos locales")
    local_cips = _catalog_vps_local_product_cips()
    remote_skus = [
        _catalog_vps_clean_text(product.get("sku"))
        for product in products
        if isinstance(product, dict) and _catalog_vps_clean_text(product.get("sku"))
    ]
    total_a_actualizar = len([sku for sku in remote_skus if sku in local_cips])
    summary["encontrados_local"] = total_a_actualizar
    summary["faltantes"] = total_a_actualizar
    _catalog_vps_job_update(
        job_id,
        etapa="Preparando fichas existentes",
        total_remoto=len(products),
        total_a_actualizar=total_a_actualizar,
        actualizados=0,
        procesadas=0,
        faltantes=total_a_actualizar,
        imagenes_descargadas=0,
        omitidos_no_local=max(len(remote_skus) - total_a_actualizar, 0),
        errores=[],
    )
    existing_by_cip = _catalog_vps_existing_fichas(empresa, remote_skus) if not dry_run else {}
    conn = None
    cursor = None
    if not dry_run:
        catalogo_legacy.asegurar_tabla_productos_ficha()
        conn = catalogo_legacy.conectar_mysql()
        if not conn:
            raise HTTPException(status_code=500, detail="No se pudo conectar a MySQL para actualizar el catalogo.")
        cursor = conn.cursor()

    try:
        processed_local = 0
        for product in products:
            if not isinstance(product, dict):
                continue
            sku = _catalog_vps_clean_text(product.get("sku"))
            if not sku:
                summary["omitidos_sin_sku"] += 1
                continue
            summary["con_sku"] += 1
            if sku not in local_cips:
                summary["omitidos_no_local"] += 1
                continue
            if dry_run:
                processed_local += 1
                summary["procesadas"] = processed_local
                summary["faltantes"] = max(total_a_actualizar - processed_local, 0)
                _catalog_vps_job_update(job_id, **summary, etapa=f"Validando CIP {sku}")
                continue
            try:
                existing = existing_by_cip.get(sku, {})
                image_info = None
                image_url = _catalog_vps_first_image(product)
                if image_url:
                    image_info = _catalog_vps_download_image(opener, image_url, empresa, sku)
                    if image_info and image_info[2]:
                        summary["imagenes_descargadas"] += 1
                ficha = _catalog_vps_payload(empresa, product, existing, image_info)
                _catalog_vps_save_ficha_fast(cursor, ficha)
                summary["actualizados"] += 1
                updated_cips.append(sku)
            except Exception as exc:
                summary["errores"].append({"cip": sku, "error": str(exc)})
            processed_local += 1
            summary["procesadas"] = processed_local
            summary["faltantes"] = max(total_a_actualizar - processed_local, 0)
            _catalog_vps_job_update(job_id, **summary, etapa=f"Actualizando CIP {sku}")
        if conn:
            _catalog_vps_job_update(job_id, etapa="Guardando cambios en MySQL")
            conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    if not dry_run:
        catalogo_legacy.registrar_bitacora(
            user,
            "SYNC_VPS_CATALOGO",
            empresa=empresa,
            cips=updated_cips[:200],
            detalle=(
                f"Sincronizacion desde {settings.catalog_vps_url}: "
                f"{summary['actualizados']} fichas, {summary['imagenes_descargadas']} imagenes."
            ),
            request=request,
        )
    _catalog_vps_job_update(job_id, **summary, status="done", etapa="Sincronizacion terminada", finished_at=time.time())
    return summary


def _catalog_vps_sync_worker(job_id: str, payload: dict, user: dict):
    try:
        _catalog_vps_sync_impl(payload, user, request=None, job_id=job_id)
    except HTTPException as exc:
        _catalog_vps_job_update(job_id, status="error", etapa="Sincronizacion detenida", error=str(exc.detail), finished_at=time.time())
    except Exception as exc:
        _catalog_vps_job_update(job_id, status="error", etapa="Sincronizacion detenida", error=str(exc), finished_at=time.time())


@router.post("/catalogo/sync-vps")
def catalogo_sync_vps(payload: dict = Body(default={}), user=Depends(require_admin)):
    job_id = uuid.uuid4().hex
    empresa = normalizar_nombre_empresa_catalogo(str(payload.get("empresa") or "").strip())
    with CATALOG_VPS_SYNC_LOCK:
        CATALOG_VPS_SYNC_JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "etapa": "En cola",
            "empresa": empresa,
            "total_remoto": 0,
            "total_a_actualizar": 0,
            "actualizados": 0,
            "procesadas": 0,
            "faltantes": 0,
            "imagenes_descargadas": 0,
            "omitidos_no_local": 0,
            "errores": [],
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    threading.Thread(
        target=_catalog_vps_sync_worker,
        args=(job_id, dict(payload or {}), dict(user or {})),
        name=f"catalog-vps-sync-{job_id[:8]}",
        daemon=True,
    ).start()
    return {"job_id": job_id, "status": "queued"}


@router.get("/catalogo/sync-vps/{job_id}")
def catalogo_sync_vps_status(job_id: str, user=Depends(require_admin)):
    job = _catalog_vps_job_snapshot(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sincronizacion no encontrada.")
    return job


@router.post("/lists")
def create_price_list(payload: dict, user=Depends(require_user)):
    name = str(payload.get("nombre") or payload.get("name") or "").strip()
    description = str(payload.get("descripcion") or payload.get("description") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Captura el nombre de la lista.")

    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO listas_precios (nombre, descripcion) VALUES (%s, %s)",
            (name, description),
        )
        list_id = cursor.lastrowid
        cursor.execute("SELECT cip FROM productos")
        for (cip,) in cursor.fetchall():
            cursor.execute(
                """
                INSERT IGNORE INTO precios_productos (lista_id, cip, precio, codigo_barras)
                VALUES (%s, %s, 0.00, '')
                """,
                (list_id, cip),
            )
        conn.commit()
        return {"message": "Lista creada", "lista_id": list_id}
    except Exception as exc:
        if conn:
            conn.rollback()
        if "Duplicate entry" in str(exc):
            raise HTTPException(status_code=400, detail="La lista ya existe.")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.delete("/lists/{list_id}")
def delete_price_list(list_id: int, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM listas_precios WHERE id = %s", (list_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="No existe la lista.")
        conn.commit()
        return {"message": "Lista eliminada"}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/export")
def export_products_first(
    user=Depends(require_user),
    q: str | None = Query(default=None),
):
    return _export_products(user=user, q=q)




@router.get("/{cip}")
def get_product(cip: str, user=Depends(require_user)):
    payload = _load_products()
    product = next((item for item in payload["items"] if str(item.get("cip")) == str(cip)), None)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    return {"item": product, "price_lists": payload["price_lists"]}


def _save_product(product: dict, original_cip: str | None = None):
    cip = str(product.get("cip") or original_cip or "").strip()
    description = str(product.get("descripcion") or "").strip()
    unit = str(product.get("unidad") or "").strip()
    if not cip or not description or not unit:
        raise HTTPException(status_code=400, detail="CIP, descripcion y unidad son obligatorios.")

    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        iva = _normalize_iva(product.get("iva"))
        descuento = str(product.get("descuento") or "No").strip()
        codigo = str(product.get("codigo_barras") or "").strip()

        if original_cip:
            cursor.execute(
                """
                UPDATE productos
                SET descripcion=%s, unidad=%s, iva=%s, descuento=%s, codigo_barras=%s
                WHERE cip=%s
                """,
                (description, unit, iva, descuento, codigo, original_cip),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Producto no encontrado.")
        else:
            cursor.execute(
                """
                INSERT INTO productos (cip, descripcion, unidad, iva, descuento, codigo_barras)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    descripcion=VALUES(descripcion),
                    unidad=VALUES(unidad),
                    iva=VALUES(iva),
                    descuento=VALUES(descuento),
                    codigo_barras=VALUES(codigo_barras)
                """,
                (cip, description, unit, iva, descuento, codigo),
            )

        for list_id, data in (product.get("precios") or {}).items():
            try:
                clean_list_id = int(list_id)
            except (TypeError, ValueError):
                continue
            price = _to_float((data or {}).get("precio"))
            barcode = str((data or {}).get("codigo_barras") or "").strip()
            cursor.execute(
                """
                INSERT INTO precios_productos (lista_id, cip, precio, codigo_barras)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE precio=%s, codigo_barras=%s
                """,
                (clean_list_id, cip, price, barcode, price, barcode),
            )

        conn.commit()
        return {"message": "Producto guardado"}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.post("")
def create_product(product: dict, user=Depends(require_user)):
    return _save_product(product)


@router.put("/{cip}")
def update_product(cip: str, product: dict, user=Depends(require_user)):
    return _save_product(product, original_cip=cip)


@router.delete("/{cip}")
def delete_product(cip: str, user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        conn = get_legacy_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM productos WHERE cip=%s", (cip,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Producto no encontrado.")
        conn.commit()
        return {"message": "Producto eliminado"}
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.put("/prices/bulk")
def update_prices_bulk(payload: object = Body(...), user=Depends(require_user)):
    conn = None
    cursor = None
    try:
        if isinstance(payload, dict):
            items = payload.get("prices") or []
            products = payload.get("products") or []
        else:
            items = payload or []
            products = []

        conn = get_legacy_connection()
        cursor = conn.cursor()
        for product in products:
            cip = str(product.get("cip") or "").strip()
            if not cip:
                continue
            cursor.execute(
                """
                UPDATE productos
                SET iva=%s, descuento=%s
                WHERE cip=%s
                """,
                (
                    _normalize_iva(product.get("iva")),
                    str(product.get("descuento") or "No").strip(),
                    cip,
                ),
            )

        for item in items:
            list_id = int(item.get("lista_id"))
            cip = str(item.get("cip") or "").strip()
            price = _to_float(item.get("precio"))
            barcode = str(item.get("codigo_barras") or "").strip()
            if not cip:
                continue
            cursor.execute(
                """
                INSERT INTO precios_productos (lista_id, cip, precio, codigo_barras)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE precio=%s, codigo_barras=%s
                """,
                (list_id, cip, price, barcode, price, barcode),
            )
        conn.commit()
        return {"message": f"{len(items)} precios y {len(products)} productos guardados"}
    except Exception as exc:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.post("/import")
def import_products(file: UploadFile = File(...), user=Depends(require_user)):
    try:
        df = pd.read_excel(io.BytesIO(file.file.read())).fillna("")
        col_map = {_normalize_text(col): str(col).strip() for col in df.columns}
        fixed_norms = {"cip", "descripcion", "unidad", "iva"}

        def get_col(*names):
            for name in names:
                if name in col_map:
                    return col_map[name]
            return None

        col_cip = get_col("cip")
        col_desc = get_col("descripcion", "descripcionproducto", "desc")
        col_unit = get_col("unidad")
        if not col_cip:
            raise HTTPException(status_code=400, detail="No se encontró la columna CIP.")

        barcode_cols = {}
        for col in df.columns:
            real_col = str(col).strip()
            normalized = _normalize_text(real_col)
            if normalized.startswith("cb"):
                barcode_cols[normalized[2:]] = real_col

        conn = get_legacy_connection()
        cursor = conn.cursor()
        try:
            try:
                cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            except Exception:
                pass
            for table in ("precios_productos", "listas_precios", "productos"):
                try:
                    cursor.execute(f"TRUNCATE TABLE {table}")
                except Exception:
                    cursor.execute(f"DELETE FROM {table}")
            try:
                cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            except Exception:
                pass

            lists_by_norm = {}
            created_lists = []
            for col in df.columns:
                real_col = str(col).strip()
                normalized = _normalize_text(real_col)
                if normalized in fixed_norms or normalized.startswith("cb"):
                    continue
                cursor.execute(
                    "INSERT INTO listas_precios (nombre, descripcion) VALUES (%s, %s)",
                    (real_col, ""),
                )
                lists_by_norm[normalized] = (real_col, int(cursor.lastrowid))
                created_lists.append(real_col)

            products_inserted = 0
            prices_inserted = 0
            for _, row in df.iterrows():
                cip = str(row.get(col_cip, "")).strip()
                if not cip:
                    continue
                description = str(row.get(col_desc, "")).strip() if col_desc else ""
                unit = str(row.get(col_unit, "")).strip() if col_unit else ""
                iva_source = ""
                for norm, real_col in col_map.items():
                    if norm == "iva":
                        iva_source = row.get(real_col, "")
                        break

                cursor.execute(
                    """
                    INSERT INTO productos (cip, descripcion, unidad, iva, codigo_barras)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (cip, description, unit, _normalize_iva(iva_source), ""),
                )
                products_inserted += 1

                for list_norm, (list_name, list_id) in lists_by_norm.items():
                    barcode_col = barcode_cols.get(list_norm)
                    barcode = str(row.get(barcode_col, "")).strip() if barcode_col else ""
                    cursor.execute(
                        """
                        INSERT INTO precios_productos (lista_id, cip, precio, codigo_barras)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (list_id, cip, _to_float(row.get(list_name, "")), barcode),
                    )
                    prices_inserted += 1

            conn.commit()
            return {
                "message": "Importacion completa",
                "productos_insertados": products_inserted,
                "listas_creadas": created_lists,
                "precios_insertados": prices_inserted,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error importando productos: {exc}")


def _export_products(
    user=Depends(require_user),
    q: str | None = Query(default=None),
):
    try:
        payload = list_products(user=user, q=q)
        items = payload["items"]
        if not items:
            raise HTTPException(status_code=404, detail="No hay productos para exportar.")

        rows = []
        for item in items:
            row = {
                "cip": item.get("cip"),
                "descripcion": item.get("descripcion"),
                "unidad": item.get("unidad"),
                "iva": item.get("iva"),
            }
            for lista in payload["lists"]:
                data = (item.get("precios") or {}).get(lista) or {}
                row[lista] = data.get("precio", 0)
                if lista in payload["lists_with_barcodes"]:
                    row[f"CB {lista}"] = data.get("codigo_barras", "")
            rows.append(row)

        df = pd.DataFrame(rows)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Productos")
        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=productos_con_precios.xlsx"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
