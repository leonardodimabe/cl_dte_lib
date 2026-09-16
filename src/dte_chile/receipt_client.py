"""Cliente REST del SII para boleta electrónica.

La boleta NO viaja por los mismos servicios que la factura. Según el instructivo
del SII, "los servidores dedicados a la recepción boleta electrónica serán
distintos de los que se utilizan en la recepción de factura electrónica (Palena
y Maullín)... Factura electrónica continuará con los Web Services Soap actuales
y para la boleta electrónica se habilitará servicios Rest". El token tampoco se
comparte: hay que pedir uno específico para boleta.

Flujo, según la especificación oficial (``openapi.yaml`` v1.0.5 en
https://www4c.sii.cl/bolcoreinternetui/api/):

1. ``GET  apicert/recursos/v1/boleta.electronica.semilla`` → ``<SEMILLA>``
2. ``POST apicert/recursos/v1/boleta.electronica.token``   → ``<TOKEN>``
   (cuerpo: la semilla firmada con XMLDSig, ``Content-Type: application/xml``)
3. ``POST pangal/recursos/v1/boleta.electronica.envio``    → JSON con ``trackid``
   (multipart, ``Cookie: TOKEN=…``, ``User-Agent`` obligatorio)
4. ``GET  apicert/recursos/v1/boleta.electronica.envio/{rut}-{dv}-{trackid}``
   → JSON con ``estado``, ``estadistica`` y ``detalle_rep_rech``

**El envío va a otro servidor.** La especificación lo declara en ese recurso:
«Servidor de certificación (Temporal) exclusivo envio (POST
/boleta.electronica.envio)» es ``pangal.sii.cl``, y en producción ``rahue.sii.cl``.
Se intentó antes contra ``apicert`` y el gateway respondía «Acceso Denegado (from
client)», que es lo que devuelve para cualquier ruta que no atiende.

Los pasos 1 y 2 se verificaron contra el ambiente de certificación. Los 3 y 4
siguen la especificación; se confirman con el primer envío real.

El TrackID de boleta tiene hasta 15 dígitos; el de factura, 10. Y el reporte de
consumo de folios (RCOF, hoy «resumen de ventas diarias») **no** va por aquí: la
misma especificación dice que «palena.sii.cl es la plataforma dedicada para la
recepción de DTE y RVD», o sea el upload de factura (``SIIClient``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum

import requests
from lxml import etree

from ._http import build_session
from .certificate import Certificate
from .errors import SiiAuthError, SiiError, SiiUploadError
from .signer import sign_seed
from .sii_client import SubmissionResult

logger = logging.getLogger(__name__)


class ReceiptEnvironment(StrEnum):
    """Hosts REST de boleta. No son maullin/palena: son otros servidores."""

    CERTIFICATION = "apicert"
    PRODUCTION = "api"

    @property
    def base_url(self) -> str:
        return f"https://{self.value}.sii.cl/recursos/v1"

    @property
    def upload_url(self) -> str:
        """El envío tiene servidor propio: pangal en certificación, rahue en producción."""
        host = {"apicert": "pangal", "api": "rahue"}[self.value]
        return f"https://{host}.sii.cl/recursos/v1"


@dataclass
class ReceiptSubmissionStatus:
    """Lo que el SII dice de un envío de boletas."""

    track_id: str
    #: REC, SOK, CRT, FOK, PRD (en proceso) · EPR procesado · RPR con reparos ·
    #: RCH, RCO, RFR, RSC, RCT, RPT, VOF (rechazos).
    state: str
    #: Por tipo: {"tipo", "informados", "aceptados", "rechazados", "reparos"}.
    stats: list[dict] = field(default_factory=list)
    #: Documentos con reparo o rechazo, con sus errores.
    details: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class ReceiptClient:
    """Autenticación y envío de boletas electrónicas por la API REST del SII."""

    SEED_PATH = "/boleta.electronica.semilla"
    TOKEN_PATH = "/boleta.electronica.token"
    UPLOAD_PATH = "/boleta.electronica.envio"
    # Mismo formato que exige el CGI de factura: el SII filtra por User-Agent y
    # con uno propio responde errores genéricos que no explican la causa.
    _USER_AGENT_TEMPLATE = "Mozilla/4.0 (compatible; PROG 1.0; Windows NT 5.0; {rut})"

    @classmethod
    def user_agent(cls, sender_rut: str) -> str:
        return cls._USER_AGENT_TEMPLATE.format(rut=sender_rut)

    def __init__(
        self,
        certificate: Certificate,
        environment: ReceiptEnvironment,
        timeout: int = 30,
    ):
        self.cert = certificate
        self.environment = environment
        self.session = build_session()
        self.session.headers["User-Agent"] = self.user_agent(certificate.rut or "")
        self._timeout = timeout
        self._token: str | None = None

    # ----- 1) Semilla -----
    def get_seed(self) -> str:
        url = self.environment.base_url + self.SEED_PATH
        try:
            response = self.session.get(url, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as ex:
            raise SiiError(f"Error de red pidiendo la semilla de boleta: {ex}") from ex

        seed = _find(response.content, "SEMILLA")
        if seed is None:
            raise SiiAuthError(
                f"El SII no devolvió SEMILLA (estado={_find(response.content, 'ESTADO')})."
            )
        return seed

    # ----- 2) Token -----
    def get_token(self, seed: str) -> str:
        """Firma la semilla y la canjea por un token específico de boleta."""
        url = self.environment.base_url + self.TOKEN_PATH
        try:
            response = self.session.post(
                url,
                data=sign_seed(seed, self.cert),
                headers={"Content-Type": "application/xml"},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except requests.RequestException as ex:
            raise SiiError(f"Error de red pidiendo el token de boleta: {ex}") from ex

        token = _find(response.content, "TOKEN")
        if token is None:
            raise SiiAuthError(
                "El SII rechazó la semilla firmada "
                f"(estado={_find(response.content, 'ESTADO')}, "
                f"glosa={_find(response.content, 'GLOSA')})."
            )
        return token

    def authenticate(self) -> str:
        self._token = self.get_token(self.get_seed())
        logger.info("Autenticado para boleta (%s).", self.environment.value)
        return self._token

    # ----- 3) Envío -----
    def send_receipts(
        self, envelope_xml: bytes, issuer_rut: str, sender_rut: str
    ) -> SubmissionResult:
        """Sube el sobre EnvioBOLETA y devuelve el TrackID (15 dígitos)."""
        # El token se toma de `authenticate`, que siempre devuelve uno: leerlo
        # del atributo obliga a demostrar que no es None justo después de
        # haberlo pedido.
        token = self._token or self.authenticate()

        issuer_body, issuer_dv = issuer_rut.split("-")
        sender_body, sender_dv = sender_rut.split("-")

        try:
            response = self.session.post(
                self.environment.upload_url + self.UPLOAD_PATH,
                data={
                    "rutSender": sender_body,
                    "dvSender": sender_dv,
                    "rutCompany": issuer_body,
                    "dvCompany": issuer_dv,
                },
                files={"archivo": ("EnvioBOLETA.xml", envelope_xml, "text/xml")},
                headers=self._auth(token),
                timeout=self._timeout,
            )
        except requests.RequestException as ex:
            raise SiiError(f"Error de red enviando las boletas: {ex}") from ex

        datos = _json(response.content)
        track_id = (
            str(datos["trackid"]) if datos.get("trackid") else _find(response.content, "TRACKID")
        )
        status = str(
            datos.get("estado") or _find(response.content, "ESTADO") or response.status_code
        )
        detail = (
            _find(response.content, "GLOSA")
            or _fault(response.content)
            or (response.content.decode("utf-8", "replace")[:300] if not datos else "")
        )

        if not track_id:
            raise SiiUploadError(
                f"El SII no aceptó el envío de boletas (HTTP {response.status_code},"
                f" estado={status}): {detail}"
            )
        return SubmissionResult(track_id=track_id, status=status, detail=detail)

    # ----- 4) Estado de un envío -----
    def submission_status(self, track_id: str, issuer_rut: str) -> ReceiptSubmissionStatus:
        """Estado de un envío de boletas, con el detalle de reparos y rechazos."""
        token = self._token or self.authenticate()
        body, dv = issuer_rut.split("-")
        url = f"{self.environment.base_url}{self.UPLOAD_PATH}/{body}-{dv}-{track_id}"
        try:
            response = self.session.get(url, headers=self._auth(token), timeout=self._timeout)
        except requests.RequestException as ex:
            raise SiiError(f"Error de red consultando el envío de boletas: {ex}") from ex

        datos = _json(response.content)
        if not datos.get("estado"):
            detalle = _fault(response.content) or response.content.decode("utf-8", "replace")[:300]
            raise SiiError(
                f"El SII no devolvió el estado del envío {track_id}"
                f" (HTTP {response.status_code}): {detalle}"
            )
        return ReceiptSubmissionStatus(
            track_id=str(datos.get("trackid") or track_id),
            state=str(datos["estado"]),
            stats=list(datos.get("estadistica") or []),
            details=list(datos.get("detalle_rep_rech") or []),
            raw=datos,
        )

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        """El token va en la cabecera Cookie de cada petición.

        Por cabecera y no en el almacén de cookies: el envío va a otro servidor
        (pangal/rahue) y una cookie atada a un dominio no viajaría.
        """
        return {"Cookie": f"TOKEN={token}"}


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _find(payload: bytes, tag: str) -> str | None:
    """Busca un tag por nombre local; las respuestas mezclan namespaces."""
    try:
        root = etree.fromstring(payload)
    except etree.XMLSyntaxError:
        return None
    for node in root.iter():
        if isinstance(node.tag, str) and etree.QName(node).localname == tag:
            return (node.text or "").strip() or None
    return None


def _json(payload: bytes) -> dict:
    """La API responde JSON; los errores del gateway, XML. Vacío si no es JSON."""
    try:
        datos = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _fault(payload: bytes) -> str | None:
    """Extrae el mensaje de un SOAP Fault; el gateway del SII responde así."""
    return _find(payload, "faultstring")
