"""Cliente REST del SII para boleta electrónica.

La boleta NO viaja por los mismos servicios que la factura. Según el instructivo
del SII, "los servidores dedicados a la recepción boleta electrónica serán
distintos de los que se utilizan en la recepción de factura electrónica (Palena
y Maullín)... Factura electrónica continuará con los Web Services Soap actuales
y para la boleta electrónica se habilitará servicios Rest". El token tampoco se
comparte: hay que pedir uno específico para boleta.

Flujo (verificado contra el ambiente de certificación):

1. ``GET  /recursos/v1/boleta.electronica.semilla``  → ``<SEMILLA>``
2. ``POST /recursos/v1/boleta.electronica.token``    → ``<TOKEN>``
   (cuerpo: la semilla firmada con XMLDSig, ``Content-Type: application/xml``)
3. ``POST /recursos/v1/boleta.electronica.envio``    → TrackID
   (multipart, con el token en la cookie ``TOKEN``)

El TrackID de boleta tiene 15 dígitos, a diferencia del de factura que tiene 10.

⚠️ Los pasos 1 y 2 están **verificados contra el ambiente de certificación**. El
paso 3 NO: el gateway responde ``Acceso Denegado (from client)``, y se comprobó
que devuelve exactamente eso para cualquier ruta bajo ``/recursos/v1/`` que no
tenga habilitada —incluida una inventada—, así que la respuesta no distingue
entre "la ruta está mal" y "no tienes permiso". La firma exacta del envío está
en el Swagger del SII (https://www4c.sii.cl/bolcoreinternetui/api/), que lleva
días respondiendo 503. Hasta confirmarla, el contrato de ``send_receipts`` está
deducido del patrón de factura electrónica, no verificado.
"""

from __future__ import annotations

import logging
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
        self.session.cookies.set("TOKEN", token, domain=f"{self.environment.value}.sii.cl")

        try:
            response = self.session.post(
                self.environment.base_url + self.UPLOAD_PATH,
                data={
                    "rutSender": sender_body,
                    "dvSender": sender_dv,
                    "rutCompany": issuer_body,
                    "dvCompany": issuer_dv,
                },
                files={"archivo": ("EnvioBOLETA.xml", envelope_xml, "text/xml")},
                timeout=self._timeout,
            )
        except requests.RequestException as ex:
            raise SiiError(f"Error de red enviando las boletas: {ex}") from ex

        track_id = _find(response.content, "TRACKID")
        status = _find(response.content, "ESTADO") or str(response.status_code)
        detail = _find(response.content, "GLOSA") or _fault(response.content) or ""

        if track_id is None:
            raise SiiUploadError(
                f"El SII no aceptó el envío de boletas (estado={status}): {detail}"
            )
        return SubmissionResult(track_id=track_id, status=status, detail=detail)


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


def _fault(payload: bytes) -> str | None:
    """Extrae el mensaje de un SOAP Fault; el gateway del SII responde así."""
    return _find(payload, "faultstring")
