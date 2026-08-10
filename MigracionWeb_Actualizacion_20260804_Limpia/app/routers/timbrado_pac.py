from dataclasses import dataclass
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import requests

try:
    from app.core.config import EXTERNAL_CFG
except Exception:  # pragma: no cover - fallback si se usa aislado
    EXTERNAL_CFG = {}

try:
    from lxml import etree
except Exception:  # pragma: no cover - optional dependency guard
    etree = None


class PacNoIntegradoError(RuntimeError):
    """El XML ya está armado, pero falta conectar el proveedor PAC."""


class PacTimbradoError(RuntimeError):
    """El PAC está integrado, pero rechazó o no pudo timbrar el XML."""


@dataclass
class ResultadoPac:
    uuid: str
    xml_timbrado: str
    proveedor: str
    raw_response: dict


@dataclass
class ResultadoCancelacionPac:
    uuid: str
    proveedor: str
    estatus_uuid: str
    estatus_cancelacion: str
    acuse: str
    raw_response: dict


@dataclass
class ResultadoEstatusPac:
    uuid: str
    proveedor: str
    codigo_estatus: str
    estado: str
    es_cancelable: str
    estatus_cancelacion: str
    raw_response: dict


@dataclass
class ResultadoAcusePac:
    uuid: str
    proveedor: str
    acuse: str
    fecha: str
    raw_response: dict


PROVEEDORES_PAC_PENDIENTES = {"SW SAPRO"}
SAT_CFDI40_XSLT_URL = "https://www.sat.gob.mx/sitio_internet/cfd/4/cadenaoriginal_4_0/cadenaoriginal_4_0.xslt"
SAT_XSLT_CACHE_DIR = Path(__file__).resolve().parents[2] / "storage" / "sat" / "xslt"
PAC_GLOBAL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "storage" / "pac" / "global_config.json"


def normalizar_proveedor_pac(valor) -> str:
    return str(valor or "").strip().upper()


def proveedor_pac_integrado(proveedor: str) -> bool:
    proveedor = normalizar_proveedor_pac(proveedor)
    return proveedor in {"SIMULADO", "FINKOK"}


def _default_pac_value(*keys: str) -> str:
    global_cfg = cargar_config_pac_global()
    for key in keys:
        env_val = os.environ.get(key.upper()) or os.environ.get(key)
        if str(env_val or "").strip():
            return str(env_val).strip()
        cfg_val = (EXTERNAL_CFG or {}).get(key) or (EXTERNAL_CFG or {}).get(key.lower())
        if str(cfg_val or "").strip():
            return str(cfg_val).strip()
        global_val = global_cfg.get(key) or global_cfg.get(key.lower())
        if str(global_val or "").strip():
            return str(global_val).strip()
    return ""


def cargar_config_pac_global() -> dict:
    try:
        if PAC_GLOBAL_CONFIG_PATH.exists():
            with open(PAC_GLOBAL_CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh) or {}
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def guardar_config_pac_global(datos: dict) -> dict:
    actual = cargar_config_pac_global()
    nuevo = dict(actual)
    for campo in ("pac_usuario", "pac_password", "pac_url", "pac_cancel_url"):
        if campo in (datos or {}):
            nuevo[campo] = str((datos or {}).get(campo) or "").strip()
    PAC_GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PAC_GLOBAL_CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(nuevo, fh, ensure_ascii=False, indent=2)
    return nuevo


def aplicar_defaults_pac(config: dict | None) -> dict:
    cfg = dict(config or {})
    proveedor = normalizar_proveedor_pac(cfg.get("proveedor"))
    if proveedor == "FINKOK":
        defaults = {
            "pac_usuario": _default_pac_value("FINKOK_USUARIO", "PAC_USUARIO", "pac_usuario", "finkok_usuario"),
            "pac_password": _default_pac_value("FINKOK_PASSWORD", "PAC_PASSWORD", "pac_password", "finkok_password"),
            "pac_url": _default_pac_value("FINKOK_URL", "PAC_URL", "pac_url", "finkok_url"),
            "pac_cancel_url": _default_pac_value("FINKOK_CANCEL_URL", "PAC_CANCEL_URL", "pac_cancel_url", "finkok_cancel_url"),
        }
        for campo, valor in defaults.items():
            if valor and not str(cfg.get(campo) or "").strip():
                cfg[campo] = valor
    return cfg


def validar_preflight_pac(config: dict) -> dict:
    config = aplicar_defaults_pac(config or {})
    proveedor = normalizar_proveedor_pac(config.get("proveedor"))
    errores = []
    advertencias = []
    if not proveedor:
        errores.append("Falta proveedor PAC.")
    elif proveedor == "SIMULADO":
        return {"ok": True, "proveedor": proveedor, "errores": [], "advertencias": ["Proveedor SIMULADO; no se enviara a PAC real."]}
    elif not proveedor_pac_integrado(proveedor):
        errores.append(f"Proveedor PAC {proveedor} no esta integrado.")
    if proveedor and proveedor != "SIMULADO":
        csd = diagnosticar_csd_config(config)
        errores.extend(csd.get("errores") or [])
        advertencias.extend(csd.get("advertencias") or [])
        for campo, etiqueta in (
            ("pac_usuario", "usuario PAC"),
            ("pac_password", "password PAC"),
        ):
            if not str(config.get(campo) or "").strip():
                errores.append(f"Falta {etiqueta}.")
        if proveedor != "FINKOK" and not str(config.get("pac_url") or "").strip():
            errores.append("Falta URL del PAC.")
    output_dir = str(config.get("output_dir") or "").strip()
    if output_dir:
        try:
            os.makedirs(output_dir, exist_ok=True)
            test_path = os.path.join(output_dir, ".write_test")
            with open(test_path, "w", encoding="utf-8") as fh:
                fh.write("ok")
            try:
                os.remove(test_path)
            except Exception:
                pass
        except Exception as exc:
            errores.append(f"No se puede escribir en carpeta fiscal: {output_dir}. {exc}")
    return {"ok": not errores, "proveedor": proveedor, "errores": errores, "advertencias": advertencias}


def timbrar_xml_pac(proveedor: str, config: dict, xml: str) -> ResultadoPac:
    proveedor = normalizar_proveedor_pac(proveedor)
    if proveedor == "FINKOK":
        return _timbrar_finkok(aplicar_defaults_pac(config or {}), xml)
    if proveedor in PROVEEDORES_PAC_PENDIENTES:
        raise PacNoIntegradoError(f"Proveedor PAC {proveedor} aún no está integrado.")
    raise PacNoIntegradoError(f"Proveedor PAC {proveedor or 'SIN PROVEEDOR'} no está integrado.")


def cancelar_cfdi_pac(proveedor: str, config: dict, uuid_val: str, motivo: str, uuid_sustitucion: str = "") -> ResultadoCancelacionPac:
    proveedor = normalizar_proveedor_pac(proveedor)
    if proveedor == "FINKOK":
        return _cancelar_finkok(aplicar_defaults_pac(config or {}), uuid_val, motivo, uuid_sustitucion)
    if proveedor in PROVEEDORES_PAC_PENDIENTES:
        raise PacNoIntegradoError(f"Proveedor PAC {proveedor} aún no tiene cancelación integrada.")
    raise PacNoIntegradoError(f"Proveedor PAC {proveedor or 'SIN PROVEEDOR'} no tiene cancelación integrada.")


def consultar_estatus_cfdi_pac(proveedor: str, config: dict, uuid_val: str, rfc_receptor: str, total: str) -> ResultadoEstatusPac:
    proveedor = normalizar_proveedor_pac(proveedor)
    if proveedor == "FINKOK":
        return _consultar_estatus_finkok(aplicar_defaults_pac(config or {}), uuid_val, rfc_receptor, total)
    if proveedor in PROVEEDORES_PAC_PENDIENTES:
        raise PacNoIntegradoError(f"Proveedor PAC {proveedor} aún no tiene consulta de estatus integrada.")
    raise PacNoIntegradoError(f"Proveedor PAC {proveedor or 'SIN PROVEEDOR'} no tiene consulta de estatus integrada.")


def obtener_acuse_cancelacion_pac(proveedor: str, config: dict, uuid_val: str) -> ResultadoAcusePac:
    proveedor = normalizar_proveedor_pac(proveedor)
    if proveedor == "FINKOK":
        return _obtener_acuse_finkok(aplicar_defaults_pac(config or {}), uuid_val, "C")
    if proveedor in PROVEEDORES_PAC_PENDIENTES:
        raise PacNoIntegradoError(f"Proveedor PAC {proveedor} aún no tiene recuperación de acuse integrada.")
    raise PacNoIntegradoError(f"Proveedor PAC {proveedor or 'SIN PROVEEDOR'} no tiene recuperación de acuse integrada.")


def obtener_acuse_recepcion_pac(proveedor: str, config: dict, uuid_val: str) -> ResultadoAcusePac:
    proveedor = normalizar_proveedor_pac(proveedor)
    if proveedor == "FINKOK":
        return _obtener_acuse_finkok(aplicar_defaults_pac(config or {}), uuid_val, "R")
    if proveedor in PROVEEDORES_PAC_PENDIENTES:
        raise PacNoIntegradoError(f"Proveedor PAC {proveedor} aún no tiene recuperación de acuse de recepción integrada.")
    raise PacNoIntegradoError(f"Proveedor PAC {proveedor or 'SIN PROVEEDOR'} no tiene recuperación de acuse de recepción integrada.")


def _xml_escape(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _finkok_endpoint(config: dict) -> str:
    url = str((config or {}).get("pac_url") or "").strip()
    if url:
        return url
    modo_pruebas = bool((config or {}).get("modo_pruebas", True))
    return (
        "https://demo-facturacion.finkok.com/servicios/soap/stamp"
        if modo_pruebas
        else "https://facturacion.finkok.com/servicios/soap/stamp"
    )


def _finkok_cancel_endpoint(config: dict) -> str:
    url = str((config or {}).get("pac_cancel_url") or "").strip()
    if url:
        return url
    stamp_url = str((config or {}).get("pac_url") or "").strip()
    if stamp_url and "/stamp" in stamp_url:
        return stamp_url.replace("/stamp", "/cancel")
    modo_pruebas = bool((config or {}).get("modo_pruebas", True))
    return (
        "https://demo-facturacion.finkok.com/servicios/soap/cancel"
        if modo_pruebas
        else "https://facturacion.finkok.com/servicios/soap/cancel"
    )


def _first_text_by_localname(root, localname: str) -> str:
    if root is None:
        return ""
    for node in root.iter():
        if str(node.tag).split("}")[-1] == localname:
            return str(node.text or "").strip()
    return ""


def _all_text_by_localname(root, localname: str) -> list[str]:
    values = []
    if root is None:
        return values
    for node in root.iter():
        if str(node.tag).split("}")[-1] == localname and str(node.text or "").strip():
            values.append(str(node.text or "").strip())
    return values


def _first_element_by_localname(root, localname: str):
    if root is None:
        return None
    for node in root.iter():
        if str(node.tag).split("}")[-1] == localname:
            return node
    return None


def _child_text_by_localname(root, localname: str) -> str:
    node = _first_element_by_localname(root, localname)
    return str(node.text or "").strip() if node is not None else ""


def _timbrar_finkok(config: dict, xml: str) -> ResultadoPac:
    endpoint = _finkok_endpoint(config)
    username = str((config or {}).get("pac_usuario") or "").strip()
    password = str((config or {}).get("pac_password") or "").strip()
    if not endpoint:
        raise PacTimbradoError("Falta URL del PAC Finkok.")
    if not username or not password:
        raise PacTimbradoError("Faltan credenciales PAC Finkok.")
    xml_b64 = base64.b64encode(str(xml or "").encode("utf-8")).decode("ascii")
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:stamp="http://facturacion.finkok.com/stamp">
  <soapenv:Header/>
  <soapenv:Body>
    <stamp:stamp>
      <stamp:xml>{xml_b64}</stamp:xml>
      <stamp:username>{_xml_escape(username)}</stamp:username>
      <stamp:password>{_xml_escape(password)}</stamp:password>
    </stamp:stamp>
  </soapenv:Body>
</soapenv:Envelope>"""
    try:
        response = requests.post(
            endpoint,
            data=envelope.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "stamp",
            },
            timeout=int((config or {}).get("pac_timeout") or 60),
        )
    except Exception as exc:
        raise PacTimbradoError(f"No se pudo conectar a Finkok: {exc}") from exc
    raw = {
        "http_status": response.status_code,
        "endpoint": endpoint,
        "proveedor": "FINKOK",
    }
    if response.status_code >= 500:
        raise PacTimbradoError(f"Finkok respondió HTTP {response.status_code}.")
    try:
        root = etree.fromstring(response.content) if etree is not None else ET.fromstring(response.content)
    except Exception as exc:
        raw["response_preview"] = (response.text or "")[:1000]
        raise PacTimbradoError(f"Finkok respondió contenido no XML: {exc}") from exc
    fault = _first_text_by_localname(root, "faultstring")
    faultcode = _first_text_by_localname(root, "faultcode")
    uuid_val = _first_text_by_localname(root, "UUID")
    xml_timbrado = _first_text_by_localname(root, "xml")
    cod_estatus = _first_text_by_localname(root, "CodEstatus")
    incidencias = _all_text_by_localname(root, "MensajeIncidencia")
    raw.update({
        "cod_estatus": cod_estatus,
        "faultcode": faultcode,
        "faultstring": fault,
        "incidencias": incidencias,
        "uuid": uuid_val,
    })
    if fault or incidencias:
        mensaje = "; ".join(x for x in [fault, *incidencias] if x) or "Finkok rechazo el XML."
        raise PacTimbradoError(mensaje)
    if not uuid_val or not xml_timbrado:
        raise PacTimbradoError(f"Finkok no devolvió UUID/XML timbrado. Estatus: {cod_estatus or 'sin estatus'}")
    return ResultadoPac(uuid=uuid_val, xml_timbrado=xml_timbrado, proveedor="FINKOK", raw_response=raw)


def _file_base64(path: str, label: str) -> str:
    path = str(path or "").strip()
    if not path:
        raise PacTimbradoError(f"Falta archivo {label} del CSD.")
    if not os.path.exists(path):
        raise PacTimbradoError(f"No se encontró el archivo {label} del CSD: {path}")
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def _credenciales_cancelacion_finkok(config: dict) -> tuple[str, str]:
    """Convierte el CSD al formato PEM que exige el servicio cancel de Finkok.

    El WSDL de Finkok recibe los binarios como base64, pero el contenido del
    certificado debe ser PEM y la llave debe ir cifrada DES3 con el password
    de la cuenta PAC, no con el password original del archivo .key del SAT.
    """
    cer_path = str((config or {}).get("csd_cer_path") or "").strip()
    key_path = str((config or {}).get("csd_key_path") or "").strip()
    key_password = str((config or {}).get("csd_key_password") or "")
    # Finkok uses a passphrase of its own to encrypt the cancellation key.
    # It is not necessarily the password used to authenticate the PAC account.
    # The fallback preserves existing installations until the separate value is set.
    pac_password = str((config or {}).get("pac_cancel_passphrase") or (config or {}).get("pac_password") or "")
    _file_base64(cer_path, ".cer")
    _file_base64(key_path, ".key")
    if not key_password:
        raise PacTimbradoError("Falta password del CSD para cancelar en Finkok.")
    if not pac_password:
        raise PacTimbradoError("Falta el passphrase de cancelación Finkok.")

    cert_pem = b""
    cert_error = ""
    for inform in ("DER", "PEM"):
        ok, out, err = _run_openssl_bytes(["x509", "-inform", inform, "-in", cer_path, "-outform", "PEM"])
        if ok and out:
            cert_pem = out
            break
        cert_error = err
    if not cert_pem:
        raise PacTimbradoError(f"No se pudo convertir el certificado CSD a PEM: {cert_error}")

    key_pem = b""
    key_error = ""
    for inform in ("DER", "PEM"):
        ok, out, err = _run_openssl_bytes([
            "pkcs8", "-inform", inform, "-in", key_path,
            "-passin", f"pass:{key_password}",
            "-topk8", "-v2", "des3",
            "-passout", f"pass:{pac_password}",
            "-outform", "PEM",
        ])
        if ok and out:
            key_pem = out
            break
        key_error = err
    if not key_pem:
        raise PacTimbradoError(f"No se pudo convertir la llave CSD a PEM para Finkok: {key_error}")
    return (
        base64.b64encode(cert_pem).decode("ascii"),
        base64.b64encode(key_pem).decode("ascii"),
    )


def _cancelar_finkok(config: dict, uuid_val: str, motivo: str, uuid_sustitucion: str = "") -> ResultadoCancelacionPac:
    endpoint = _finkok_cancel_endpoint(config)
    username = str((config or {}).get("pac_usuario") or "").strip()
    password = str((config or {}).get("pac_password") or "").strip()
    taxpayer_id = str((config or {}).get("rfc_emisor") or "").strip().upper()
    uuid_val = str(uuid_val or "").strip().upper()
    motivo = str(motivo or "").strip()[:2]
    uuid_sustitucion = str(uuid_sustitucion or "").strip().upper()
    if not endpoint:
        raise PacTimbradoError("Falta URL de cancelación Finkok.")
    if not username or not password:
        raise PacTimbradoError("Faltan credenciales PAC Finkok.")
    if not taxpayer_id:
        raise PacTimbradoError("Falta RFC emisor para cancelar en Finkok.")
    if not uuid_val:
        raise PacTimbradoError("Falta UUID del CFDI a cancelar.")
    if motivo not in {"01", "02", "03", "04"}:
        raise PacTimbradoError("Motivo SAT inválido para cancelación.")
    if motivo == "01" and not uuid_sustitucion:
        raise PacTimbradoError("El motivo 01 requiere UUID sustituto.")
    cer_b64, key_b64 = _credenciales_cancelacion_finkok(config or {})
    folio_sust_attr = f' FolioSustitucion="{_xml_escape(uuid_sustitucion)}"' if uuid_sustitucion else ""
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:can="http://facturacion.finkok.com/cancel" xmlns:core="apps.services.soap.core.views">
  <soapenv:Header/>
  <soapenv:Body>
    <can:cancel>
      <can:UUIDS>
        <core:UUID UUID="{_xml_escape(uuid_val)}" Motivo="{_xml_escape(motivo)}"{folio_sust_attr}/>
      </can:UUIDS>
      <can:username>{_xml_escape(username)}</can:username>
      <can:password>{_xml_escape(password)}</can:password>
      <can:taxpayer_id>{_xml_escape(taxpayer_id)}</can:taxpayer_id>
      <can:cer>{cer_b64}</can:cer>
      <can:key>{key_b64}</can:key>
      <can:store_pending>true</can:store_pending>
    </can:cancel>
  </soapenv:Body>
</soapenv:Envelope>"""
    try:
        response = requests.post(
            endpoint,
            data=envelope.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "cancel",
            },
            timeout=int((config or {}).get("pac_timeout") or 60),
        )
    except Exception as exc:
        raise PacTimbradoError(f"No se pudo conectar a Finkok para cancelar: {exc}") from exc
    raw = {
        "http_status": response.status_code,
        "endpoint": endpoint,
        "proveedor": "FINKOK",
        "uuid": uuid_val,
        "motivo": motivo,
        "uuid_sustitucion": uuid_sustitucion,
    }
    if response.status_code >= 500:
        raise PacTimbradoError(f"Finkok respondió HTTP {response.status_code} al cancelar.")
    try:
        root = etree.fromstring(response.content) if etree is not None else ET.fromstring(response.content)
    except Exception as exc:
        raw["response_preview"] = (response.text or "")[:1000]
        raise PacTimbradoError(f"Finkok respondió contenido no XML al cancelar: {exc}") from exc
    fault = _first_text_by_localname(root, "faultstring")
    faultcode = _first_text_by_localname(root, "faultcode")
    folio_node = _first_element_by_localname(root, "Folio")
    estatus_uuid = _child_text_by_localname(folio_node, "EstatusUUID") if folio_node is not None else ""
    estatus_cancelacion = _child_text_by_localname(folio_node, "EstatusCancelacion") if folio_node is not None else ""
    uuid_respuesta = _child_text_by_localname(folio_node, "UUID") if folio_node is not None else ""
    cod_estatus = _first_text_by_localname(root, "CodEstatus")
    acuse = _first_text_by_localname(root, "Acuse")
    raw.update({
        "cod_estatus": cod_estatus,
        "faultcode": faultcode,
        "faultstring": fault,
        "estatus_uuid": estatus_uuid,
        "estatus_cancelacion": estatus_cancelacion,
        "uuid_respuesta": uuid_respuesta,
        "acuse_chars": len(acuse),
    })
    if fault:
        raise PacTimbradoError(fault)
    estatus_texto = f"{estatus_uuid} {estatus_cancelacion} {cod_estatus}".lower()
    aceptada = (
        estatus_uuid.startswith(("201", "202"))
        or "cancelado" in estatus_texto
        or "en proceso" in estatus_texto
        or "solicitud recibida" in estatus_texto
    )
    if not aceptada:
        detalle = estatus_cancelacion or cod_estatus or estatus_uuid or "sin estatus"
        raise PacTimbradoError(f"Finkok no aceptó la cancelación. Estatus: {detalle}")
    return ResultadoCancelacionPac(
        uuid=uuid_respuesta or uuid_val,
        proveedor="FINKOK",
        estatus_uuid=estatus_uuid,
        estatus_cancelacion=estatus_cancelacion,
        acuse=acuse,
        raw_response=raw,
    )


def _consultar_estatus_finkok(config: dict, uuid_val: str, rfc_receptor: str, total: str) -> ResultadoEstatusPac:
    endpoint = _finkok_cancel_endpoint(config)
    username = str((config or {}).get("pac_usuario") or "").strip()
    password = str((config or {}).get("pac_password") or "").strip()
    taxpayer_id = str((config or {}).get("rfc_emisor") or "").strip().upper()
    uuid_val = str(uuid_val or "").strip().upper()
    rfc_receptor = str(rfc_receptor or "").strip().upper()
    total = str(total or "").strip()
    if not endpoint:
        raise PacTimbradoError("Falta URL de consulta Finkok.")
    if not username or not password:
        raise PacTimbradoError("Faltan credenciales PAC Finkok.")
    if not taxpayer_id:
        raise PacTimbradoError("Falta RFC emisor para consultar estatus en Finkok.")
    if not rfc_receptor:
        raise PacTimbradoError("Falta RFC receptor para consultar estatus en Finkok.")
    if not uuid_val:
        raise PacTimbradoError("Falta UUID del CFDI a consultar.")
    if not total:
        raise PacTimbradoError("Falta total del CFDI para consultar estatus en Finkok.")
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:can="http://facturacion.finkok.com/cancel">
  <soapenv:Header/>
  <soapenv:Body>
    <can:get_sat_status>
      <can:username>{_xml_escape(username)}</can:username>
      <can:password>{_xml_escape(password)}</can:password>
      <can:taxpayer_id>{_xml_escape(taxpayer_id)}</can:taxpayer_id>
      <can:rtaxpayer_id>{_xml_escape(rfc_receptor)}</can:rtaxpayer_id>
      <can:uuid>{_xml_escape(uuid_val)}</can:uuid>
      <can:total>{_xml_escape(total)}</can:total>
    </can:get_sat_status>
  </soapenv:Body>
</soapenv:Envelope>"""
    try:
        response = requests.post(
            endpoint,
            data=envelope.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "get_sat_status",
            },
            timeout=int((config or {}).get("pac_timeout") or 60),
        )
    except Exception as exc:
        raise PacTimbradoError(f"No se pudo conectar a Finkok para consultar estatus: {exc}") from exc
    raw = {
        "http_status": response.status_code,
        "endpoint": endpoint,
        "proveedor": "FINKOK",
        "uuid": uuid_val,
        "rfc_receptor": rfc_receptor,
        "total": total,
    }
    if response.status_code >= 500:
        raise PacTimbradoError(f"Finkok respondió HTTP {response.status_code} al consultar estatus.")
    try:
        root = etree.fromstring(response.content) if etree is not None else ET.fromstring(response.content)
    except Exception as exc:
        raw["response_preview"] = (response.text or "")[:1000]
        raise PacTimbradoError(f"Finkok respondió contenido no XML al consultar estatus: {exc}") from exc
    fault = _first_text_by_localname(root, "faultstring")
    error = _first_text_by_localname(root, "error")
    if fault or error:
        raise PacTimbradoError(fault or error)
    codigo_estatus = _first_text_by_localname(root, "CodigoEstatus")
    estado = _first_text_by_localname(root, "Estado")
    es_cancelable = _first_text_by_localname(root, "EsCancelable")
    estatus_cancelacion = _first_text_by_localname(root, "EstatusCancelacion")
    raw.update({
        "codigo_estatus": codigo_estatus,
        "estado": estado,
        "es_cancelable": es_cancelable,
        "estatus_cancelacion": estatus_cancelacion,
        "validacion_efos": _first_text_by_localname(root, "ValidacionEFOS"),
    })
    if not any([codigo_estatus, estado, es_cancelable, estatus_cancelacion]):
        raise PacTimbradoError("Finkok no devolvió estatus SAT interpretable.")
    return ResultadoEstatusPac(
        uuid=uuid_val,
        proveedor="FINKOK",
        codigo_estatus=codigo_estatus,
        estado=estado,
        es_cancelable=es_cancelable,
        estatus_cancelacion=estatus_cancelacion,
        raw_response=raw,
    )


def _obtener_acuse_finkok(config: dict, uuid_val: str, tipo: str = "C") -> ResultadoAcusePac:
    endpoint = _finkok_cancel_endpoint(config)
    username = str((config or {}).get("pac_usuario") or "").strip()
    password = str((config or {}).get("pac_password") or "").strip()
    taxpayer_id = str((config or {}).get("rfc_emisor") or "").strip().upper()
    uuid_val = str(uuid_val or "").strip().upper()
    tipo = str(tipo or "C").strip().upper()[:1] or "C"
    if not endpoint:
        raise PacTimbradoError("Falta URL de acuse Finkok.")
    if not username or not password:
        raise PacTimbradoError("Faltan credenciales PAC Finkok.")
    if not taxpayer_id:
        raise PacTimbradoError("Falta RFC emisor para recuperar acuse en Finkok.")
    if not uuid_val:
        raise PacTimbradoError("Falta UUID del CFDI para recuperar acuse.")
    if tipo not in {"C", "R"}:
        raise PacTimbradoError("Tipo de acuse inválido. Usa C para cancelación o R para recepción.")
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:can="http://facturacion.finkok.com/cancel">
  <soapenv:Header/>
  <soapenv:Body>
    <can:get_receipt>
      <can:username>{_xml_escape(username)}</can:username>
      <can:password>{_xml_escape(password)}</can:password>
      <can:taxpayer_id>{_xml_escape(taxpayer_id)}</can:taxpayer_id>
      <can:uuid>{_xml_escape(uuid_val)}</can:uuid>
      <can:type>{_xml_escape(tipo)}</can:type>
    </can:get_receipt>
  </soapenv:Body>
</soapenv:Envelope>"""
    try:
        response = requests.post(
            endpoint,
            data=envelope.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "get_receipt",
            },
            timeout=int((config or {}).get("pac_timeout") or 60),
        )
    except Exception as exc:
        raise PacTimbradoError(f"No se pudo conectar a Finkok para recuperar acuse: {exc}") from exc
    raw = {
        "http_status": response.status_code,
        "endpoint": endpoint,
        "proveedor": "FINKOK",
        "uuid": uuid_val,
        "type": tipo,
    }
    if response.status_code >= 500:
        raise PacTimbradoError(f"Finkok respondió HTTP {response.status_code} al recuperar acuse.")
    try:
        root = etree.fromstring(response.content) if etree is not None else ET.fromstring(response.content)
    except Exception as exc:
        raw["response_preview"] = (response.text or "")[:1000]
        raise PacTimbradoError(f"Finkok respondió contenido no XML al recuperar acuse: {exc}") from exc
    fault = _first_text_by_localname(root, "faultstring")
    error = _first_text_by_localname(root, "error")
    success = _first_text_by_localname(root, "success").lower()
    receipt = _first_text_by_localname(root, "receipt")
    fecha = _first_text_by_localname(root, "date")
    uuid_respuesta = _first_text_by_localname(root, "uuid") or uuid_val
    raw.update({
        "faultstring": fault,
        "error": error,
        "success": success,
        "receipt_chars": len(receipt),
        "date": fecha,
    })
    if fault or error:
        raise PacTimbradoError(fault or error)
    if success in {"false", "0"}:
        raise PacTimbradoError("Finkok no encontró acuse para este UUID.")
    if not receipt:
        raise PacTimbradoError("Finkok no devolvió XML de acuse.")
    return ResultadoAcusePac(
        uuid=uuid_respuesta,
        proveedor="FINKOK",
        acuse=receipt,
        fecha=fecha,
        raw_response=raw,
    )


def _mask_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * max(4, len(value) - 4)}{value[-2:]}"


def preparar_paquete_pac(proveedor: str, config: dict, xml: str) -> dict:
    config = aplicar_defaults_pac(config or {})
    proveedor = normalizar_proveedor_pac(proveedor or (config or {}).get("proveedor"))
    pac_url = str((config or {}).get("pac_url") or "").strip()
    if proveedor == "FINKOK" and not pac_url:
        pac_url = _finkok_endpoint(config or {})
    pac_usuario = str((config or {}).get("pac_usuario") or "").strip()
    pac_password = str((config or {}).get("pac_password") or "").strip()
    xml_bytes = str(xml or "").encode("utf-8")
    xml_b64 = base64.b64encode(xml_bytes).decode("ascii")
    errores = []
    advertencias = []
    if not proveedor:
        errores.append("Falta proveedor PAC.")
    elif proveedor == "SIMULADO":
        errores.append("El proveedor SIMULADO no genera paquete PAC real.")
    if not pac_url:
        errores.append("Falta URL del PAC.")
    if not pac_usuario:
        errores.append("Falta usuario PAC.")
    if not pac_password:
        errores.append("Falta password PAC.")
    if xml and " Sello=" not in xml and " Sello=\"" not in xml:
        advertencias.append("El XML no parece incluir atributo Sello.")

    request_preview = {
        "endpoint": pac_url,
        "proveedor": proveedor,
        "usuario": pac_usuario,
        "password": _mask_secret(pac_password),
        "xml_sha256": hashlib.sha256(xml_bytes).hexdigest(),
        "xml_bytes": len(xml_bytes),
        "xml_base64_chars": len(xml_b64),
    }
    if proveedor == "FINKOK":
        request_preview.update({
            "tipo": "SOAP",
            "operacion": "stamp",
            "payload": {
                "xml": f"<base64 {len(xml_b64)} chars>",
                "username": pac_usuario,
                "password": _mask_secret(pac_password),
            },
        })
    elif proveedor == "SW SAPRO":
        request_preview.update({
            "tipo": "HTTP",
            "operacion": "timbrar",
            "headers": {"Authorization": "Bearer <token>", "Content-Type": "application/xml"},
            "payload": f"<xml {len(xml_bytes)} bytes>",
        })
    elif proveedor:
        request_preview.update({
            "tipo": "HTTP",
            "operacion": "timbrar",
            "payload": f"<xml {len(xml_bytes)} bytes>",
        })

    return {
        "ok": not errores,
        "proveedor": proveedor,
        "url": pac_url,
        "xml_sha256": request_preview["xml_sha256"],
        "xml_bytes": len(xml_bytes),
        "xml_base64_chars": len(xml_b64),
        "errores": errores,
        "advertencias": advertencias,
        "request_preview": request_preview,
    }


def probar_conectividad_pac(config: dict) -> dict:
    config = aplicar_defaults_pac(config or {})
    proveedor = normalizar_proveedor_pac((config or {}).get("proveedor"))
    pac_url = str((config or {}).get("pac_url") or "").strip()
    if proveedor == "FINKOK" and not pac_url:
        pac_url = _finkok_endpoint(config or {})
    pac_usuario = str((config or {}).get("pac_usuario") or "").strip()
    pac_password = str((config or {}).get("pac_password") or "").strip()
    resultado = {
        "ok": False,
        "proveedor": proveedor,
        "url": pac_url,
        "http_status": None,
        "respondio": False,
        "integrado": proveedor_pac_integrado(proveedor),
        "errores": [],
        "advertencias": [],
    }
    if not proveedor:
        resultado["errores"].append("Falta proveedor PAC.")
    elif proveedor == "SIMULADO":
        resultado["advertencias"].append("Proveedor SIMULADO no requiere conexión PAC real.")
    elif proveedor not in PROVEEDORES_PAC_PENDIENTES and not proveedor_pac_integrado(proveedor):
        resultado["advertencias"].append(f"Proveedor {proveedor} no tiene adaptador reconocido.")
    if proveedor and proveedor != "SIMULADO":
        if not pac_url:
            resultado["errores"].append("Falta URL del PAC.")
        elif not re.match(r"^https?://", pac_url, flags=re.IGNORECASE):
            resultado["errores"].append("La URL del PAC debe iniciar con http:// o https://.")
        if not pac_usuario:
            resultado["errores"].append("Falta usuario PAC.")
        if not pac_password:
            resultado["errores"].append("Falta password PAC.")
    if resultado["errores"] or proveedor == "SIMULADO":
        resultado["ok"] = proveedor == "SIMULADO" and not resultado["errores"]
        return resultado
    try:
        response = requests.get(pac_url, timeout=15, allow_redirects=True)
        resultado["http_status"] = response.status_code
        resultado["respondio"] = True
        if response.status_code >= 500:
            resultado["errores"].append(f"El PAC respondió HTTP {response.status_code}.")
        else:
            if response.status_code in {401, 403}:
                resultado["advertencias"].append("El PAC respondió, pero requiere autenticación; la prueba no envía credenciales.")
            elif response.status_code >= 400:
                resultado["advertencias"].append(f"El PAC respondió HTTP {response.status_code}; revisa si la URL debe incluir WSDL o endpoint específico.")
            resultado["ok"] = True
    except Exception as exc:
        resultado["errores"].append(f"No se pudo conectar al PAC: {exc}")
    return resultado


def _openssl_executable() -> str:
    """Ubica OpenSSL sin depender de que esté agregado al PATH del servicio."""
    configured = str(
        os.environ.get("OPENSSL_BIN")
        or (EXTERNAL_CFG or {}).get("openssl_bin")
        or ""
    ).strip()
    packaged = ""
    if getattr(sys, "frozen", False):
        packaged = str(Path(sys.executable).resolve().parent / "tools" / "openssl" / "openssl.exe")
    candidates = [
        configured,
        packaged,
        shutil.which("openssl") or "",
        r"C:\Program Files\Git\usr\bin\openssl.exe",
        r"C:\Program Files\OpenSSL-Win64\bin\openssl.exe",
        r"C:\Program Files\OpenSSL-Win32\bin\openssl.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return "openssl"


def _run_openssl(args, timeout=8):
    try:
        res = subprocess.run(
            [_openssl_executable(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return res.returncode == 0, (res.stdout or "").strip(), (res.stderr or "").strip()
    except Exception as exc:
        return False, "", str(exc)


def _run_openssl_bytes(args, timeout=8, input_data: bytes | None = None):
    try:
        res = subprocess.run(
            [_openssl_executable(), *args],
            input=input_data,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return res.returncode == 0, res.stdout or b"", (res.stderr or b"").decode("utf-8", errors="ignore").strip()
    except Exception as exc:
        return False, b"", str(exc)


def _cache_name_url(url: str) -> str:
    base = Path(str(url or "").split("?")[0]).name or "sat.xslt"
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", base)
    digest = hashlib.sha1(str(url or "").encode("utf-8")).hexdigest()[:12]
    return f"{digest}_{base}"


def _download_cached_url(url: str, refresh: bool = False) -> Path:
    SAT_XSLT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = SAT_XSLT_CACHE_DIR / _cache_name_url(url)
    if dest.is_file() and not refresh:
        return dest
    response = requests.get(url, timeout=25)
    response.raise_for_status()
    content = response.content or b""
    if not content.strip():
        raise RuntimeError(f"El SAT devolvio vacio el recurso XSLT: {url}")
    dest.write_bytes(content)
    return dest


class _SatCachedResolver(etree.Resolver if etree is not None else object):
    def resolve(self, url, pubid, context):  # pragma: no cover - exercised through lxml
        if str(url or "").lower().startswith(("http://", "https://")):
            path = _download_cached_url(url)
            return self.resolve_filename(str(path), context)
        return None


def _openssl_cert(args_base):
    for inform in ("DER", "PEM"):
        ok, out, err = _run_openssl(["x509", "-inform", inform, *args_base])
        if ok:
            return True, out, "", inform
    return False, "", err, ""


def _openssl_key_pubkey(path, password):
    passin = f"pass:{password or ''}"
    attempts = [
        ["pkey", "-inform", "DER", "-in", path, "-passin", passin, "-pubout"],
        ["pkey", "-inform", "PEM", "-in", path, "-passin", passin, "-pubout"],
        ["rsa", "-inform", "DER", "-in", path, "-passin", passin, "-pubout"],
        ["rsa", "-inform", "PEM", "-in", path, "-passin", passin, "-pubout"],
    ]
    last_err = ""
    for args in attempts:
        ok, out, err = _run_openssl(args)
        if ok and out:
            return True, out, "", args[1]
        last_err = err
    return False, "", last_err, ""


def _normalizar_pubkey(pubkey: str) -> str:
    return "".join(line.strip() for line in str(pubkey or "").splitlines() if "PUBLIC KEY" not in line)


def _extraer_rfc_subject(subject: str) -> str:
    texto = str(subject or "").upper()
    candidatos = re.findall(r"\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b", texto)
    return candidatos[0] if candidatos else ""


def _serial_sat_desde_hex(serial_hex: str) -> str:
    limpio = re.sub(r"[^0-9A-Fa-f]", "", str(serial_hex or ""))
    if not limpio:
        return ""
    try:
        serial_ascii = bytes.fromhex(limpio).decode("ascii", errors="ignore")
        digitos = "".join(ch for ch in serial_ascii if ch.isdigit())
        if len(digitos) >= 10:
            return digitos
    except Exception:
        pass
    return limpio


def _parse_openssl_cert_date(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _certificado_base64_desde_cer(cer_path: str) -> tuple[bool, str, str]:
    ultimo_error = ""
    for inform in ("DER", "PEM"):
        ok, out, err = _run_openssl_bytes(["x509", "-inform", inform, "-in", cer_path, "-outform", "DER"])
        if ok and out:
            return True, base64.b64encode(out).decode("ascii"), ""
        ultimo_error = err
    return False, "", ultimo_error


def obtener_material_csd(config: dict) -> dict:
    """Devuelve NoCertificado y Certificado en base64 para el XML candidato."""
    diagnostico = diagnosticar_csd_config(config or {})
    resultado = {
        "ok": False,
        "no_certificado": "",
        "certificado": "",
        "serial_openssl": diagnostico.get("serial") or "",
        "errores": list(diagnostico.get("errores") or []),
        "advertencias": list(diagnostico.get("advertencias") or []),
    }
    if not diagnostico.get("certificado_ok"):
        return resultado
    no_certificado = _serial_sat_desde_hex(diagnostico.get("serial"))
    ok_cert, certificado, err = _certificado_base64_desde_cer(diagnostico.get("cer_path") or "")
    if not no_certificado:
        resultado["errores"].append("No se pudo obtener el número de certificado.")
    if not ok_cert:
        resultado["errores"].append(f"No se pudo convertir el certificado a base64 DER: {err}")
    if resultado["errores"]:
        return resultado
    resultado.update({"ok": True, "no_certificado": no_certificado, "certificado": certificado})
    return resultado


def generar_cadena_original_cfdi(xml: str, refresh_xslt: bool = False) -> dict:
    resultado = {"ok": False, "cadena": "", "xslt_path": "", "errores": [], "advertencias": []}
    if etree is None:
        resultado["errores"].append("No esta disponible lxml para generar cadena original.")
        return resultado
    try:
        xslt_path = _download_cached_url(SAT_CFDI40_XSLT_URL, refresh=refresh_xslt)
        parser = etree.XMLParser(resolve_entities=False, no_network=False, recover=False)
        parser.resolvers.add(_SatCachedResolver())
        xslt_doc = etree.parse(str(xslt_path), parser)
        transform = etree.XSLT(xslt_doc)
        xml_doc = etree.fromstring(str(xml or "").encode("utf-8"))
        cadena = str(transform(xml_doc)).strip()
        if not cadena:
            resultado["errores"].append("La cadena original salio vacia.")
            return resultado
        resultado.update({"ok": True, "cadena": cadena, "xslt_path": str(xslt_path)})
        return resultado
    except Exception as exc:
        resultado["errores"].append(f"No se pudo generar cadena original CFDI 4.0: {exc}")
        return resultado


def sellar_cadena_csd(cadena: str, config: dict) -> dict:
    key_path = str((config or {}).get("csd_key_path") or "").strip()
    password = str((config or {}).get("csd_key_password") or "")
    resultado = {"ok": False, "sello": "", "errores": [], "advertencias": []}
    if not key_path:
        resultado["errores"].append("Falta ruta de la llave .key.")
        return resultado
    if not os.path.exists(key_path):
        resultado["errores"].append(f"No existe la llave .key en servidor: {key_path}")
        return resultado
    if not password:
        resultado["errores"].append("Falta password del CSD.")
        return resultado
    passin = f"pass:{password}"
    attempts = [
        ["dgst", "-sha256", "-keyform", "DER", "-sign", key_path, "-passin", passin],
        ["dgst", "-sha256", "-keyform", "PEM", "-sign", key_path, "-passin", passin],
        ["dgst", "-sha256", "-sign", key_path, "-passin", passin],
    ]
    last_err = ""
    for args in attempts:
        ok, out, err = _run_openssl_bytes(args, timeout=15, input_data=str(cadena or "").encode("utf-8"))
        if ok and out:
            resultado.update({"ok": True, "sello": base64.b64encode(out).decode("ascii")})
            return resultado
        last_err = err
    resultado["errores"].append(f"No se pudo sellar la cadena original con OpenSSL: {last_err}")
    return resultado


def sellar_xml_cfdi(xml: str, config: dict) -> dict:
    resultado = {
        "ok": False,
        "xml": xml,
        "cadena_original": "",
        "sello": "",
        "errores": [],
        "advertencias": [],
    }
    material = obtener_material_csd(config or {})
    if not material.get("ok"):
        resultado["errores"].extend(material.get("errores") or ["No se pudo obtener material CSD."])
        resultado["advertencias"].extend(material.get("advertencias") or [])
        return resultado
    cadena = generar_cadena_original_cfdi(xml)
    if not cadena.get("ok"):
        resultado["errores"].extend(cadena.get("errores") or ["No se pudo generar cadena original."])
        resultado["advertencias"].extend(cadena.get("advertencias") or [])
        return resultado
    sello = sellar_cadena_csd(cadena["cadena"], config or {})
    if not sello.get("ok"):
        resultado["errores"].extend(sello.get("errores") or ["No se pudo sellar cadena original."])
        resultado["advertencias"].extend(sello.get("advertencias") or [])
        resultado["cadena_original"] = cadena["cadena"]
        return resultado
    try:
        root = etree.fromstring(str(xml or "").encode("utf-8")) if etree is not None else None
        if root is None:
            raise RuntimeError("No esta disponible lxml para insertar el sello.")
        root.set("NoCertificado", material["no_certificado"])
        root.set("Certificado", material["certificado"])
        root.set("Sello", sello["sello"])
        
        ns_cfdi = "http://www.sat.gob.mx/cfd/4"
        addenda = root.find(f"{{{ns_cfdi}}}Addenda")
        if addenda is not None:
            # Preservar el contenido de la addenda como XML real
            contenido_addenda = addenda.text or ""
            if contenido_addenda.strip().startswith("<"):
                # La addenda contiene XML, necesitamos parsearlo y agregarlo como elementos
                try:
                    # Envolver en un elemento temporal para parsear
                    temp_xml = f"<root xmlns:cfdi='{ns_cfdi}'>{contenido_addenda}</root>"
                    temp_root = etree.fromstring(temp_xml.encode("utf-8"))
                    addenda.clear()
                    for child in temp_root:
                        addenda.append(child)
                except Exception:
                    pass
        
        xml_sellado = etree.tostring(root, encoding="UTF-8", xml_declaration=True).decode("utf-8")
    except Exception as exc:
        resultado["errores"].append(f"No se pudo insertar el sello en el XML: {exc}")
        resultado["cadena_original"] = cadena["cadena"]
        resultado["sello"] = sello["sello"]
        return resultado
    resultado.update({
        "ok": True,
        "xml": xml_sellado,
        "cadena_original": cadena["cadena"],
        "sello": sello["sello"],
    })
    return resultado


def diagnosticar_csd_config(config: dict) -> dict:
    cer_path = str((config or {}).get("csd_cer_path") or "").strip()
    key_path = str((config or {}).get("csd_key_path") or "").strip()
    password = str((config or {}).get("csd_key_password") or "")
    rfc_emisor = str((config or {}).get("rfc_emisor") or "").strip().upper()
    resultado = {
        "cer_path": cer_path,
        "key_path": key_path,
        "cer_existe": bool(cer_path and os.path.exists(cer_path)),
        "key_existe": bool(key_path and os.path.exists(key_path)),
        "certificado_ok": False,
        "key_ok": False,
        "par_ok": False,
        "rfc_certificado": "",
        "rfc_coincide": None,
        "serial": "",
        "not_before": "",
        "not_after": "",
        "vigente": None,
        "dias_vigencia": None,
        "subject": "",
        "errores": [],
        "advertencias": [],
    }
    if not cer_path:
        resultado["errores"].append("Falta ruta del certificado .cer.")
    elif not resultado["cer_existe"]:
        resultado["errores"].append(f"No existe el certificado .cer en servidor: {cer_path}")
    if not key_path:
        resultado["errores"].append("Falta ruta de la llave .key.")
    elif not resultado["key_existe"]:
        resultado["errores"].append(f"No existe la llave .key en servidor: {key_path}")
    if not password:
        resultado["errores"].append("Falta password del CSD.")
    if not resultado["cer_existe"]:
        return resultado

    ok, out, err, _ = _openssl_cert(["-in", cer_path, "-noout", "-subject", "-serial", "-dates"])
    if not ok:
        resultado["errores"].append(f"No se pudo leer el certificado con OpenSSL: {err}")
        return resultado
    resultado["certificado_ok"] = True
    for line in out.splitlines():
        if line.startswith("subject="):
            resultado["subject"] = line.partition("=")[2].strip()
        elif line.startswith("serial="):
            resultado["serial"] = line.partition("=")[2].strip()
        elif line.startswith("notBefore="):
            resultado["not_before"] = line.partition("=")[2].strip()
        elif line.startswith("notAfter="):
            resultado["not_after"] = line.partition("=")[2].strip()
    not_after_dt = _parse_openssl_cert_date(resultado["not_after"])
    if not_after_dt:
        dias = (not_after_dt - datetime.now(timezone.utc)).days
        resultado["dias_vigencia"] = dias
        resultado["vigente"] = dias >= 0
        if dias < 0:
            resultado["errores"].append(f"El certificado CSD venció el {resultado['not_after']}.")
        elif dias <= 30:
            resultado["advertencias"].append(f"El certificado CSD vence en {dias} día(s): {resultado['not_after']}.")
    resultado["rfc_certificado"] = _extraer_rfc_subject(resultado["subject"])
    if rfc_emisor and resultado["rfc_certificado"]:
        resultado["rfc_coincide"] = rfc_emisor == resultado["rfc_certificado"]
        if not resultado["rfc_coincide"]:
            resultado["errores"].append(f"RFC del certificado ({resultado['rfc_certificado']}) no coincide con RFC emisor ({rfc_emisor}).")
    elif rfc_emisor:
        resultado["advertencias"].append("No se pudo extraer RFC del subject del certificado.")

    ok_cert_pub, cert_pub, cert_err, _ = _openssl_cert(["-in", cer_path, "-pubkey", "-noout"])
    if not ok_cert_pub:
        resultado["advertencias"].append(f"No se pudo extraer llave pública del certificado: {cert_err}")
    if resultado["key_existe"] and password:
        ok_key, key_pub, key_err, _ = _openssl_key_pubkey(key_path, password)
        resultado["key_ok"] = ok_key
        if not ok_key:
            resultado["errores"].append("No se pudo leer la llave .key con el password configurado.")
        elif ok_cert_pub:
            resultado["par_ok"] = _normalizar_pubkey(cert_pub) == _normalizar_pubkey(key_pub)
            if not resultado["par_ok"]:
                resultado["errores"].append("La llave .key no corresponde al certificado .cer.")
    return resultado
