"""Cliente de Boletas de Honorarios Electrónicas (BHE) recibidas — PRODUCCIÓN.

Portado del módulo Odoo ``dimabe_bhe_sii`` (grupo_los_lirios PR #36). El SII no
publica las BHE recibidas por la casilla de intercambio ni por un servicio SOAP,
así que la única vía es el portal web de honorarios (HTML server-rendered).

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

import requests

from ._http import build_session
from .errors import BheError, SiiAuthError
from .rut import format_rut

logger = logging.getLogger(__name__)

# Login por clave tributaria.
_LOGIN_REF = "https://misiir.sii.cl/cgi_misii/siihome.cgi"
_LOGIN_FORM = (
    "https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoRutClave.html?" + _LOGIN_REF
)
_LOGIN_POST = "https://zeusr.sii.cl/cgi_AUT2000/CAutInicio.cgi"

# Portal de honorarios (Informe Mensual de Boletas Recibidas).
_MENU_URL = "https://loa.sii.cl/cgi_IMT/TMBCOC_MenuConsultasContribRec.cgi?dummy=1"
_REPORT_URL = "https://loa.sii.cl/cgi_IMT/TMBCOC_InformeMensualBheRec.cgi"
_REPORT_REFERER = "https://loa.sii.cl/cgi_IMT/TMBCOC_MenuConsultasContribRec.cgi"

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
                f"El login al SII falló para {self.rut}. "
                "Verifique el RUT y la clave tributaria."
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
            year, month, page, resp.status_code, len(resp.text),
        )
        return resp.text

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
