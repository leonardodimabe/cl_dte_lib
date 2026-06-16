"""Cliente del RCV (Registro de Compra y Venta) del SII — PRODUCCIÓN.

Portado del servicio .NET ``dimabe-servicios`` (DetailBookService +
SiiAuthenticator). Sirve para descargar las compras/ventas que el SII tiene
registradas y, por ejemplo, contrastarlas contra las de Odoo.

Flujo:
  1. Autenticación por **TLS mutuo**: se presenta el certificado de cliente en
     el handshake hacia ``herculesr.sii.cl/cgi_AUT2000/CAutInicio.cgi`` con el
     parámetro ``referencia`` apuntando al DTEauth de palena. El SII devuelve el
     cookie de sesión ``TOKEN``.
  2. Consulta: POST JSON a ``getDetalleCompraExport`` / ``getDetalleVentaExport``
     por cada estado contable. La respuesta trae ``data`` = lista de líneas CSV
     (separadas por ``;``), que se parsean a registros (dict por nombre de
     columna del SII).

⚠️ Es PRODUCCIÓN, con datos reales. El certificado debe estar autorizado para el
RCV de la empresa consultada.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass

import requests

from ._http import build_session
from .book import BookLine
from .certificate import Certificate
from .errors import RcvError, SiiAuthError

logger = logging.getLogger(__name__)

_HOST = "https://www4.sii.cl"
_AUTH_URL = "https://herculesr.sii.cl/cgi_AUT2000/CAutInicio.cgi"
_REFERENCE = "https://palena.sii.cl/cgi_dte/UPL/DTEauth?1"
_NS = "cl.sii.sdi.lob.diii.consdcv.data.api.interfaces.FacadeService"

# operation -> (método facadeService, accionRecaptcha, estados contables)
_OPERATIONS = {
    "COMPRA": ("getDetalleCompraExport", "RCV_DDETC", ["REGISTRO", "RECLAMADO", "PENDIENTE"]),
    "VENTA": ("getDetalleVentaExport", "RCV_DDETV", ["REGISTRO"]),
}


@dataclass
class RcvDocument:
    """Documento del RCV normalizado, listo para conciliar contra Odoo.

    Clave de match contra ``account.move``: (doc_type, counterpart_rut, folio).
    """

    operation: str  # "COMPRA" | "VENTA"
    state: str  # "REGISTRO" | "PENDIENTE" | "RECLAMADO"
    doc_type: int  # 33, 34, 56, 61, 46...
    folio: int
    counterpart_rut: str  # proveedor (compra) / cliente (venta)
    counterpart_name: str
    date: _dt.date
    exempt_amount: int
    net_amount: int
    vat_amount: int
    total_amount: int
    reception_date: _dt.datetime | None = None

    def to_book_line(self) -> BookLine:
        """Convierte a línea de Libro (para armar el LibroCV con estos datos)."""
        return BookLine(
            doc_type=self.doc_type,
            folio=self.folio,
            date=self.date,
            rut=self.counterpart_rut,
            business_name=self.counterpart_name,
            exempt_amount=self.exempt_amount,
            net_amount=self.net_amount,
            vat_amount=self.vat_amount,
            total_amount=self.total_amount,
        )


@dataclass
class _TempCert:
    """Escribe el certificado a un PEM temporal para el TLS mutuo de requests."""

    path: str

    @classmethod
    def create(cls, cert: Certificate) -> _TempCert:
        fd, path = tempfile.mkstemp(suffix=".pem")
        with os.fdopen(fd, "wb") as f:
            f.write(cert.cert_pem)
            f.write(b"\n")
            # CA(s) intermedias: sin ellas el SII no arma la cadena (unknown_ca).
            if cert.chain_pem:
                f.write(cert.chain_pem)
                f.write(b"\n")
            f.write(cert.private_key_pem)
        return cls(path)

    def remove(self) -> None:
        try:
            os.unlink(self.path)
        except OSError:
            pass


class RCVClient:
    def __init__(self, cert: Certificate, timeout: int = 120):
        self.cert = cert
        self._timeout = timeout
        self._pem = _TempCert.create(cert)
        self.session = build_session()
        self.session.cert = self._pem.path  # TLS mutuo con el cert
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})
        self._token: str | None = None

    # --- contexto ---
    def __enter__(self) -> RCVClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self.session.close()
        self._pem.remove()

    def __del__(self) -> None:
        # Respaldo: borra el PEM temporal aunque no se use el context manager.
        try:
            self._pem.remove()
        except Exception:
            pass

    # --- autenticación ---
    def authenticate(self) -> str:
        """Establece la sesión web del SII por TLS mutuo y devuelve el TOKEN."""
        logger.info("Autenticando al RCV por TLS mutuo (herculesr).")
        try:
            resp = self.session.post(
                _AUTH_URL, data={"referencia": _REFERENCE}, timeout=self._timeout
            )
            resp.raise_for_status()
        except requests.RequestException as ex:
            raise RcvError(f"Error de red autenticando al RCV: {ex}") from ex
        token = next(
            (c.value for c in self.session.cookies if c.name in ("TOKEN", "SESSIONID")),
            None,
        )
        if not token:
            raise SiiAuthError(
                "El SII no devolvió TOKEN. Posibles causas: certificado no "
                "autorizado para la empresa, o sesión rechazada."
            )
        self._token = token
        # Asegura que el cookie viaje también al dominio del RCV.
        self.session.cookies.set("TOKEN", token, domain="www4.sii.cl")
        return token

    # --- consulta ---
    def detail(
        self, issuer_rut: str, period: str, operation: str = "COMPRA"
    ) -> dict[str, list[dict]]:
        """Descarga el detalle por estado contable.

        ``issuer_rut``: "76158145-7". ``period``: "AAAAMM" (ej. "202605").
        ``operation``: "COMPRA" | "VENTA". Devuelve {estado: [registros...]}.
        """
        if operation not in _OPERATIONS:
            raise ValueError("operation debe ser COMPRA o VENTA.")
        if not self._token:
            self.authenticate()

        method, action, states = _OPERATIONS[operation]
        body, dv = issuer_rut.split("-")
        endpoint = f"{_HOST}/consdcvinternetui/services/data/facadeService/{method}"

        result: dict[str, list[dict]] = {}
        for state in states:
            payload = {
                "metaData": {
                    "namespace": f"{_NS}/{method}",
                    "conversationId": self._token,
                    "transactionId": str(uuid.uuid4()),
                },
                "data": {
                    "rutEmisor": body,
                    "dvEmisor": dv,
                    "estadoContab": state,
                    "ptributario": period,
                    "operacion": operation,
                    "accionRecaptcha": action,
                    "codTipoDoc": 0,
                    "tokenRecaptcha": "t-o-k-e-n-web",
                },
            }
            try:
                resp = self.session.post(endpoint, json=payload, timeout=self._timeout)
                resp.raise_for_status()
            except requests.RequestException as ex:
                raise RcvError(f"Error consultando RCV {operation}/{state}: {ex}") from ex
            data = resp.json().get("data")
            result[state] = _parse_csv(data) if data else []
            logger.debug("RCV %s/%s: %d registros", operation, state, len(result[state]))
        return result

    def documents(
        self, issuer_rut: str, period: str, operation: str = "COMPRA"
    ) -> list[RcvDocument]:
        """Versión normalizada de :meth:`detail`: lista plana de RcvDocument.

        Aplana los estados (cada documento lleva su ``state``), normaliza los
        nombres de columna del SII y deduplica por (doc_type, contraparte, folio)
        — las líneas extra de impuestos comparten esa clave. Listo para Odoo.
        """
        return _normalize(self.detail(issuer_rut, period, operation), operation)


def _parse_csv(lines: list[str]) -> list[dict]:
    """Convierte las líneas CSV (separadas por ';') en dicts por columna."""
    if not lines or len(lines) < 2:
        return []
    headers = [h.strip() for h in lines[0].split(";")]
    records = []
    for line in lines[1:]:
        values = line.split(";")
        if len(values) < len(headers):
            continue
        records.append(dict(zip(headers, (v.strip() for v in values), strict=False)))
    return records


def to_book_lines(records: list[dict], operation: str = "COMPRA") -> list[BookLine]:
    """Mapea registros crudos del RCV a líneas del Libro (para generar el LibroCV)."""
    return [d.to_book_line() for d in _normalize({"_": records}, operation)]


# --------------------------------------------------------------------------- #
#  Normalización a RcvDocument
# --------------------------------------------------------------------------- #
def _columns(operation: str) -> dict[str, str]:
    """Nombres de columna del RCV (difieren por operación; ojo con el casing)."""
    if operation == "COMPRA":
        return {"rut": "RUT Proveedor", "vat": "Monto IVA Recuperable", "total": "Monto Total"}
    return {"rut": "Rut cliente", "vat": "Monto IVA", "total": "Monto total"}


def _normalize(by_state: dict[str, list[dict]], operation: str) -> list[RcvDocument]:
    """Aplana {estado: [registros]} a [RcvDocument], normaliza y deduplica."""
    cols = _columns(operation)
    out: list[RcvDocument] = []
    seen: set[tuple[int, str, int]] = set()
    for state, records in by_state.items():
        for rec in records:
            doc = _rcv_document_from(rec, operation, state, cols)
            if doc is None:
                continue
            key = (doc.doc_type, doc.counterpart_rut, doc.folio)
            if key in seen:  # líneas extra (impuestos) del mismo documento
                continue
            seen.add(key)
            out.append(doc)
    return out


def _rcv_document_from(
    rec: dict, operation: str, state: str, cols: dict[str, str]
) -> RcvDocument | None:
    """Construye un RcvDocument; devuelve None si la fila no es procesable."""
    doc_type = (rec.get("Tipo Doc") or "").strip()
    folio = (rec.get("Folio") or "").strip()
    if not doc_type.isdigit() or not folio.isdigit():
        return None
    return RcvDocument(
        operation=operation,
        state=state,
        doc_type=int(doc_type),
        folio=int(folio),
        counterpart_rut=(rec.get(cols["rut"]) or "").strip(),
        counterpart_name=(rec.get("Razon Social") or "").strip(),
        date=_parse_sii_date(rec.get("Fecha Docto", "")),
        exempt_amount=_to_int(rec.get("Monto Exento")),
        net_amount=_to_int(rec.get("Monto Neto")),
        vat_amount=_to_int(rec.get(cols["vat"])),
        total_amount=_to_int(rec.get(cols["total"])),
        reception_date=_parse_sii_datetime(rec.get("Fecha Recepcion")),
    )


def _parse_sii_date(value: str) -> _dt.date:
    """Parsea fechas del SII en formato dd/MM/yyyy."""
    d, m, y = value.split("/")
    return _dt.date(int(y), int(m), int(d))


def _parse_sii_datetime(value: str | None) -> _dt.datetime | None:
    """Parsea 'dd/MM/yyyy HH:MM:SS' (o solo fecha). None si viene vacío."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return _dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
