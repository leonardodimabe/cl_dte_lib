"""Cliente de Boletas de Honorarios Electrónicas (BHE) recibidas y de Boletas de
Prestación de Servicios de Terceros (BTE) emitidas — PRODUCCIÓN.

Portado del módulo Odoo ``dimabe_bhe_sii`` (grupo_los_lirios PR #36 y #132). El
SII no publica estas boletas por la casilla de intercambio ni por un servicio
SOAP, así que la única vía es el portal web (HTML server-rendered).

Las BTE viven en otro sistema del SII (``zeus.sii.cl/cvc_cgi/bte``), pero la
misma sesión de clave tributaria sirve para las dos consultas.

Flujo:
  1. Autenticación por **clave tributaria** (HTTP puro, sin navegador): GET del
     formulario de login (siembra las cookies anti-bot) y POST de RUT+clave a
     ``zeusr.sii.cl/cgi_AUT2000/CAutInicio.cgi``. El SII devuelve la cookie de
     sesión ``TOKEN``. Luego un GET al menú de honorarios siembra la sesión del
     portal (``NETSCAPE_LIVEWIRE.*``).
  2. Consulta: POST al CGI del Informe Mensual de Boletas Recibidas por cada
     página (el SII pagina de a 100 filas). La respuesta es HTML con los datos
     embebidos en un array JS ``arr_informe_mensual['<campo>_<n>'] = ...`` que
     se extrae por regex.

⚠️ Es scraping de un CGI privado sin contrato: el SII puede cambiar el markup o
agregar captcha en cualquier momento. Ante HTML irreconocible se lanza
``BheError`` con un extracto de la respuesta para diagnóstico.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from dataclasses import dataclass, field
from html import unescape as _unescape

import requests

from ._http import build_session
from .errors import BheError, SiiAuthError
from .rut import format_rut

logger = logging.getLogger(__name__)

# Login por clave tributaria.
_LOGIN_REF = "https://misiir.sii.cl/cgi_misii/siihome.cgi"
_LOGIN_FORM = "https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoRutClave.html?" + _LOGIN_REF
_LOGIN_POST = "https://zeusr.sii.cl/cgi_AUT2000/CAutInicio.cgi"

# Portal de honorarios (Informe Mensual de Boletas Recibidas).
_MENU_URL = "https://loa.sii.cl/cgi_IMT/TMBCOC_MenuConsultasContribRec.cgi?dummy=1"
_REPORT_URL = "https://loa.sii.cl/cgi_IMT/TMBCOC_InformeMensualBheRec.cgi"
_REPORT_REFERER = "https://loa.sii.cl/cgi_IMT/TMBCOC_MenuConsultasContribRec.cgi"

# BTE emitidas por el contribuyente (informe mensual, «CNTR=1»; «2» son las recibidas).
_BTE_MENU_URL = "https://zeus.sii.cl/cvc_cgi/bte/bte_indiv_cons?1"
_BTE_REPORT_URL = "https://zeus.sii.cl/cvc_cgi/bte/bte_indiv_cons2"
_BTE_MARKER = "INFORME BTE's EMITIDAS"
# Columnas de una fila del informe, después de la celda con el enlace a la boleta.
_BTE_COLUMNS = (
    "folio",
    "estado",
    "fecha_emision",
    "rut_emisor",
    "nombre_emisor",
    "fecha_recepcion",
    "rut_prestador",
    "nombre_prestador",
    "bruto",
    "retenido",
    "pagado",
)
_BTE_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_BTE_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_BTE_PAGES_RE = re.compile(r"P(?:&aacute;|á)gina\s+\d+\s+de\s+(\d+)", re.I)
_BTE_TOTAL_RE = re.compile(r"Total\s+Boletas\s*:\s*(\d+)", re.I)

# El SII valida el User-Agent: sin uno de navegador rechaza el login.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_PAGE_SIZE = 100  # el SII pagina de a 100 filas
_MAX_PAGES = 100  # tope duro contra loops por respuestas repetidas del SII

# arr_informe_mensual['<campo>_<n>'] = "valor" | formatMiles("valor")
_ROW_RE = re.compile(
    r"arr_informe_mensual\['([a-z_]+)_(\d+)'\]\s*=\s*"
    r"(?:formatMiles\(\s*\"([^\"]*)\"|\"([^\"]*)\")",
    re.IGNORECASE,
)
_TOTAL_RE = re.compile(r"xml_values\['total_boletas'\]\s*=\s*\"?(\d+)")

# Marcadores de sesión inválida/expirada (página de login o aviso del SII).
_AUTH_MARKERS = ("IngresoRutClave", "CAutInicio", "sesión ha expirado", "sesion ha expirado")


@dataclass
class BheDocument:
    """BHE recibida, normalizada desde el informe mensual del SII.

    Clave de match contra Odoo: (issuer_rut, folio). Montos en CLP entero.
    """

    issuer_rut: str  # "12345678-5"
    issuer_name: str
    folio: int
    issue_date: _dt.date | None
    gross_amount: int  # honorarios brutos
    retention_amount: int  # retención segunda categoría
    net_amount: int  # líquido a pagar
    status: str  # "vigente" | "anulada"
    period: str  # "AAAA-MM" consultado
    cancel_date: _dt.date | None = None
    raw: dict = field(default_factory=dict)  # fila original del SII (trazabilidad)


@dataclass
class BteDocument:
    """BTE emitida por el contribuyente, normalizada desde el informe mensual del SII.

    La emite la empresa cuando le paga a alguien que no emite su propia boleta, y
    es ella la que retiene: el «prestador» es quien presta el servicio y recibe el
    líquido. Clave de match contra Odoo: (provider_rut, folio). Montos en CLP entero.
    """

    provider_rut: str  # "12345678-5"
    provider_name: str
    folio: int
    issue_date: _dt.date | None
    gross_amount: int  # honorarios brutos
    retention_amount: int  # retención que practica la empresa
    net_amount: int  # líquido pagado al prestador
    status: str  # "vigente" | "anulada"
    period: str  # "AAAA-MM" consultado
    raw: dict = field(default_factory=dict)  # fila original del SII (trazabilidad)


class BheClient:
    """Cliente del portal de honorarios del SII, autenticado por clave tributaria.

    ``rut``: RUT de la empresa receptora/retenedora (cualquier formato).
    La clave tributaria solo se usa para el login; no se persiste.
    """

    def __init__(self, rut: str, password: str, timeout: int = 30):
        self.rut = format_rut(rut)
        self._password = password
        self._timeout = timeout
        self.session = build_session()
        self.session.headers.update({"User-Agent": _BROWSER_UA})
        self._authenticated = False

    # --- contexto ---
    def __enter__(self) -> BheClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        self.session.close()

    # --- autenticación ---
    def authenticate(self) -> None:
        """Login con RUT+clave y siembra de la sesión del portal de honorarios."""
        body, dv = self.rut.split("-")
        logger.info("Autenticando al SII por clave tributaria (rut %s).", self.rut)
        try:
            # 1) GET del formulario (obtiene las cookies anti-bot).
            self.session.get(_LOGIN_FORM, timeout=self._timeout)
            # 2) POST del login.
            self.session.post(
                _LOGIN_POST,
                data={
                    "rut": body,
                    "dv": dv,
                    "rutcntr": f"{body}-{dv}",
                    "clave": self._password,
                    "referencia": _LOGIN_REF,
                    "411": "",
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://zeusr.sii.cl",
                    "Referer": _LOGIN_FORM,
                },
                timeout=self._timeout,
            )
        except requests.RequestException as ex:
            raise BheError(f"Error de red en el login al SII: {ex}") from ex

        if not any(c.name.upper() == "TOKEN" for c in self.session.cookies):
            raise SiiAuthError(
                f"El login al SII falló para {self.rut}. Verifique el RUT y la clave tributaria."
            )
        # 3) Sesión del portal de honorarios (setea NETSCAPE_LIVEWIRE.*).
        try:
            self.session.get(_MENU_URL, timeout=self._timeout)
        except requests.RequestException:
            pass  # no es fatal: el informe puede sembrarla igual
        self._authenticated = True

    # --- consulta ---
    def fetch_received(self, year: int, month: int) -> list[BheDocument]:
        """Descarga las BHE recibidas del período, recorriendo todas las páginas.

        Devuelve lista vacía si el período no tiene boletas.
        """
        if not self._authenticated:
            self.authenticate()
        period = f"{year:04d}-{month:02d}"

        documents: list[BheDocument] = []
        total: int | None = None
        for page in range(_MAX_PAGES):
            html = self._request_page(year, month, page)
            rows = _parse_report(html)
            if page == 0:
                total = _extract_total(html)
                if not rows:
                    self._check_empty_response(html, total)
                    return []
            elif not rows:
                break  # página repetida/vacía: el SII no entregó más filas
            documents.extend(_normalize_row(r, period) for r in rows)
            if total is None or len(documents) >= total:
                break
        else:
            logger.warning("BHE %s: se alcanzó el tope de %d páginas.", period, _MAX_PAGES)

        if total is not None and len(documents) < total:
            raise BheError(
                f"El SII reporta {total} boletas para {period} pero solo se "
                f"obtuvieron {len(documents)} (paginación incompleta)."
            )
        logger.info("BHE %s: %d boletas recibidas.", period, len(documents))
        return documents

    def _request_page(self, year: int, month: int, page: int) -> str:
        try:
            resp = self.session.post(
                _REPORT_URL,
                data={
                    "rut_arrastre": self.rut.split("-")[0],
                    "dv_arrastre": self.rut.split("-")[1],
                    "pagina_solicitada": str(page),
                    "cbmesinformemensual": f"{month:02d}",
                    "cbanoinformemensual": str(year),
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://loa.sii.cl",
                    "Referer": _REPORT_REFERER,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as ex:
            raise BheError(f"Error de red consultando el informe BHE: {ex}") from ex
        logger.debug(
            "BHE %04d-%02d página %d -> HTTP %s len=%d",
            year,
            month,
            page,
            resp.status_code,
            len(resp.text),
        )
        return resp.text

    def fetch_issued_bte(self, year: int, month: int) -> list[BteDocument]:
        """Descarga las BTE que el contribuyente emitió en el período, todas las páginas.

        Incluye las anuladas (``status="anulada"``): decidir qué hacer con ellas es
        de quien consume. Devuelve lista vacía si el período no tiene boletas.
        """
        if not self._authenticated:
            self.authenticate()
        period = f"{year:04d}-{month:02d}"
        try:
            # El menú de consulta siembra la sesión del sistema de BTE.
            self.session.get(_BTE_MENU_URL, timeout=self._timeout)
        except requests.RequestException:
            pass  # no es fatal: el informe puede sembrarla igual

        documents: list[BteDocument] = []
        total: int | None = None
        page, total_pages = 1, 1
        while page <= total_pages:
            html = self._request_bte_page(year, month, page)
            if _BTE_MARKER not in html:
                self._raise_unrecognized(html, "el informe de BTE emitidas")
            if page == 1:
                total = _extract_bte_total(html)
            documents.extend(_normalize_bte_row(r, period) for r in _parse_bte_report(html))
            total_pages = _extract_bte_total_pages(html)
            page += 1
            if page > _MAX_PAGES:
                logger.warning("BTE %s: se alcanzó el tope de %d páginas.", period, _MAX_PAGES)
                break

        if total is not None and len(documents) != total:
            raise BheError(
                f"El SII reporta {total} BTE emitidas para {period} pero se leyeron "
                f"{len(documents)} (paginación o formato del informe inesperado)."
            )
        logger.info("BTE %s: %d boletas emitidas.", period, len(documents))
        return documents

    def _request_bte_page(self, year: int, month: int, page: int) -> str:
        try:
            resp = self.session.post(
                _BTE_REPORT_URL,
                data={
                    "CNTR": "1",
                    "PAGINA": str(page),
                    "AUTEN": "RUTCLAVE",
                    "TIPO": "mensual",
                    "MESM": f"{month:02d}",
                    "ANOM": str(year),
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": _BTE_MENU_URL,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as ex:
            raise BheError(f"Error de red consultando el informe de BTE: {ex}") from ex
        # El sistema de BTE responde en Latin-1 y a veces sin declararlo.
        resp.encoding = resp.encoding or "latin-1"
        logger.debug(
            "BTE %04d-%02d página %d -> HTTP %s len=%d",
            year,
            month,
            page,
            resp.status_code,
            len(resp.text),
        )
        return resp.text

    @staticmethod
    def _raise_unrecognized(html: str, what: str) -> None:
        """Respuesta sin el informe: sesión caída (``SiiAuthError``) o formato nuevo."""
        if any(marker.lower() in html.lower() for marker in _AUTH_MARKERS):
            raise SiiAuthError(f"La sesión del SII expiró o fue rechazada al consultar {what}.")
        excerpt = re.sub(r"\s+", " ", html)[:300]
        raise BheError(f"La respuesta del SII no contiene {what}. Extracto: {excerpt!r}")

    @staticmethod
    def _check_empty_response(html: str, total: int | None) -> None:
        """Distingue 'período sin boletas' (retorna) de respuesta inválida (lanza)."""
        if total == 0 or "INFORME MENSUAL" in html.upper():
            return  # informe rendereado sin filas: mes sin boletas
        if any(marker.lower() in html.lower() for marker in _AUTH_MARKERS):
            raise SiiAuthError(
                "La sesión del SII expiró o fue rechazada al consultar el informe BHE."
            )
        excerpt = re.sub(r"\s+", " ", html)[:300]
        raise BheError(
            f"La respuesta del SII no contiene el informe de boletas recibidas. "
            f"Extracto: {excerpt!r}"
        )


# --------------------------------------------------------------------------- #
#  Parseo del HTML del informe
# --------------------------------------------------------------------------- #
def _parse_report(html: str) -> list[dict]:
    """Extrae las filas del array JS ``arr_informe_mensual['<campo>_<n>']``."""
    rows: dict[str, dict] = {}
    for match in _ROW_RE.finditer(html):
        fld, idx, num_val, str_val = match.groups()
        rows.setdefault(idx, {})[fld.lower()] = num_val if num_val is not None else str_val
    return [rows[idx] for idx in sorted(rows, key=int)]


def _extract_total(html: str) -> int | None:
    """Total de boletas del período según ``xml_values['total_boletas']``."""
    match = _TOTAL_RE.search(html)
    return int(match.group(1)) if match else None


def _normalize_row(row: dict, period: str) -> BheDocument:
    """Mapea una fila cruda del SII a BheDocument."""
    rut = (row.get("rutemisor") or "").strip()
    dv = (row.get("dvemisor") or "").strip()
    cancel_date = _parse_date(row.get("fechaanulacion"))
    cancelled = cancel_date is not None or bool((row.get("fechaanulacion") or "").strip())
    cancelled = cancelled or (row.get("estado") or "").strip().upper() == "A"
    return BheDocument(
        issuer_rut=f"{rut}-{dv.upper()}" if rut else "",
        issuer_name=(row.get("nombre_emisor") or "").strip(),
        folio=_to_int(row.get("nroboleta")),
        issue_date=_parse_date(row.get("fecha_boleta")),
        gross_amount=_to_int(row.get("totalhonorarios")),
        retention_amount=_to_int(row.get("retencion_receptor")),
        net_amount=_to_int(row.get("honorariosliquidos")),
        status="anulada" if cancelled else "vigente",
        period=period,
        cancel_date=cancel_date,
        raw=dict(row),
    )


def _parse_bte_report(html: str) -> list[dict]:
    """Filas del informe de BTE: sólo las que enlazan a la boleta (``bte_indiv_cons3``),
    así se descartan el encabezado y la fila de totales."""
    rows = []
    for row_html in _BTE_ROW_RE.findall(html):
        if "bte_indiv_cons3" not in row_html:
            continue
        # La primera celda es el ícono con el enlace a la boleta.
        cells = [_cell_text(c) for c in _BTE_CELL_RE.findall(row_html)][1:]
        if len(cells) < len(_BTE_COLUMNS):
            continue
        rows.append(dict(zip(_BTE_COLUMNS, cells[: len(_BTE_COLUMNS)], strict=True)))
    return rows


def _cell_text(cell_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", cell_html)
    return re.sub(r"\s+", " ", _unescape(text)).strip()


def _extract_bte_total_pages(html: str) -> int:
    match = _BTE_PAGES_RE.search(html)
    return int(match.group(1)) if match else 1


def _extract_bte_total(html: str) -> int | None:
    """Total de boletas del período según «Total Boletas : N»."""
    match = _BTE_TOTAL_RE.search(html)
    return int(match.group(1)) if match else None


def _normalize_bte_row(row: dict, period: str) -> BteDocument:
    """Mapea una fila del informe de BTE a BteDocument."""
    return BteDocument(
        provider_rut=row["rut_prestador"].upper(),
        provider_name=row["nombre_prestador"],
        folio=_to_int(row["folio"]),
        # El informe de BTE escribe las fechas con guiones: DD-MM-YYYY.
        issue_date=_parse_date(row["fecha_emision"].replace("-", "/")),
        gross_amount=_to_int(row["bruto"]),
        retention_amount=_to_int(row["retenido"]),
        net_amount=_to_int(row["pagado"]),
        status="anulada" if row["estado"].upper().startswith("ANUL") else "vigente",
        period=period,
        raw=dict(row),
    )


def _parse_date(value: str | None) -> _dt.date | None:
    """Parsea 'DD/MM/YYYY' del SII. None si viene vacío o malformado."""
    value = (value or "").strip()
    parts = value.split("/")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(p) for p in parts)
        return _dt.date(year, month, day)
    except ValueError:
        return None


def _to_int(value) -> int:
    """Monto del SII a CLP entero. Asume formato chileno: '.' miles, ',' decimal."""
    if isinstance(value, (int, float)):
        return int(value)
    if not value:
        return 0
    try:
        return int(round(float(str(value).strip().replace(".", "").replace(",", "."))))
    except (ValueError, TypeError):
        return 0
