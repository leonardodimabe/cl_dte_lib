"""Cliente de comunicación con el SII: autenticación y envío de DTE.

Flujo de autenticación:
    1. Solicitar SEMILLA (CrSeed).
    2. Firmar la semilla con el certificado (getToken espera la semilla firmada).
    3. Obtener TOKEN (GetTokenFromSeed).
    4. Enviar el sobre EnvioDTE (upload) → devuelve TRACK ID.
    5. Consultar estado por TRACK ID (cron asíncrono).

Ambientes:
    - Maullín  → certificación  (maullin.sii.cl)
    - Palena   → producción     (palena.sii.cl)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

import requests
from lxml import etree

from ._http import build_session
from .certificate import Certificate
from .errors import SiiAuthError, SiiError, SiiUploadError
from .signer import sign_seed

logger = logging.getLogger(__name__)

# Namespaces de los servicios SOAP (AXIS) del SII.
_NS_SOAPENV = "http://schemas.xmlsoap.org/soap/envelope/"
_NS_DEFAULT = "http://DefaultNamespace"


class Environment(StrEnum):
    CERTIFICATION = "maullin"  # pruebas
    PRODUCTION = "palena"  # real

    @property
    def host(self) -> str:
        return f"https://{self.value}.sii.cl"


@dataclass
class SubmissionResult:
    track_id: str | None
    status: str
    detail: str = ""


class SIIClient:
    SEED_SVC = "/DTEWS/CrSeed.jws"
    TOKEN_SVC = "/DTEWS/GetTokenFromSeed.jws"

    def __init__(self, certificate: Certificate, environment: Environment, timeout: int = 30):
        self.cert = certificate
        self.environment = environment
        self.session = build_session()
        self._timeout = timeout
        self._token: str | None = None

    def _soap_call(self, service: str, operation: str, params: dict) -> str:
        """POST SOAP manual. Devuelve el texto del elemento ``<{operation}Return>``."""
        envelope = _build_soap_envelope(operation, params)
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""}
        logger.debug("SOAP %s → %s%s", operation, self.environment.host, service)
        try:
            resp = self.session.post(
                f"{self.environment.host}{service}",
                data=envelope,
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as ex:
            raise SiiError(f"Error de red en {operation}: {ex}") from ex
        tree = etree.fromstring(resp.content)
        return_node = tree.find(".//{*}%sReturn" % operation)
        if return_node is None or return_node.text is None:
            raise SiiError(f"Respuesta SOAP sin <{operation}Return>.")
        return return_node.text

    # ----- 1) Semilla -----
    def get_seed(self) -> str:
        """Solicita una semilla al SII (servicio CrSeed)."""
        response = self._soap_call(self.SEED_SVC, "getSeed", {})
        status, value = _parse_response(response, "SEMILLA")
        if value is None:
            raise SiiAuthError(f"El SII no devolvió SEMILLA (estado={status}).")
        return value

    # ----- 2+3) Firmar semilla y obtener token -----
    def get_token(self, seed: str) -> str:
        """Firma la semilla (XMLDSig) y la canjea por un token (GetTokenFromSeed)."""
        signed_xml = sign_seed(seed, self.cert).decode("utf-8")
        response = self._soap_call(self.TOKEN_SVC, "getToken", {"pszXml": signed_xml})
        status, value = _parse_response(response, "TOKEN")
        if value is None:
            raise SiiAuthError(f"El SII rechazó la semilla firmada (estado={status}).")
        return value

    def authenticate(self) -> str:
        seed = self.get_seed()
        self._token = self.get_token(seed)
        logger.info("Autenticado ante el SII (%s).", self.environment.value)
        return self._token

    # ----- 4) Envío del sobre -----
    UPLOAD_PATH = "/cgi_dte/UPL/DTEUpload"
    # El SII rechaza el upload si no hay User-Agent.
    _USER_AGENT = "Mozilla/4.0 (compatible; dte_chile 0.1; Windows)"

    def send_dte(self, envelope_xml: bytes, issuer_rut: str, sender_rut: str) -> SubmissionResult:
        """Sube el sobre EnvioDTE al SII (DTEUpload) y devuelve el TrackID."""
        if not self._token:
            self.authenticate()
        assert self._token is not None

        issuer_body, issuer_dv = issuer_rut.split("-")
        sender_body, sender_dv = sender_rut.split("-")

        files = {
            "rutSender": (None, sender_body),
            "dvSender": (None, sender_dv),
            "rutCompany": (None, issuer_body),
            "dvCompany": (None, issuer_dv),
            "archivo": ("envio.xml", envelope_xml, "application/xml"),
        }
        logger.info("Subiendo EnvioDTE (%d bytes) a %s.", len(envelope_xml), self.environment.value)
        try:
            resp = self.session.post(
                f"{self.environment.host}{self.UPLOAD_PATH}",
                files=files,  # type: ignore[arg-type]  # tuplas multipart heterogéneas
                headers={"User-Agent": self._USER_AGENT},
                cookies={"TOKEN": self._token},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as ex:
            raise SiiUploadError(f"Fallo al subir el sobre: {ex}") from ex

        # El SII responde XML cuando acepta; ante error suele devolver HTML.
        try:
            tree = etree.fromstring(resp.content)
        except etree.XMLSyntaxError:
            return SubmissionResult(
                track_id=None,
                status="ERROR_NO_XML",
                detail=resp.text,
            )
        track = tree.findtext(".//{*}TRACKID")
        status = tree.findtext(".//{*}STATUS") or tree.findtext(".//{*}ESTADO")
        return SubmissionResult(track_id=track, status=status or "?", detail=resp.text[:400])

    # ----- 5) Consulta de estado -----
    QUERY_SVC = "/DTEWS/QueryEstUp.jws"

    def query_status(self, track_id: str, issuer_rut: str) -> SubmissionResult:
        """Consulta el estado del envío por TrackID (servicio QueryEstUp)."""
        if not self._token:
            self.authenticate()
        assert self._token is not None
        issuer_body, issuer_dv = issuer_rut.split("-")
        response = self._soap_call(
            self.QUERY_SVC,
            "getEstUp",
            {"Rut": issuer_body, "Dv": issuer_dv, "TrackId": str(track_id), "Token": self._token},
        )
        status, _ = _parse_response(response, "ESTADO")
        label_node = etree.fromstring(
            response.encode("utf-8") if isinstance(response, str) else response
        ).find(".//{*}GLOSA")
        label = label_node.text if label_node is not None else ""
        return SubmissionResult(track_id=track_id, status=status or "?", detail=label or response)


def _build_soap_envelope(operation: str, params: dict) -> bytes:
    """Arma el sobre SOAP para un servicio AXIS del SII.

    El valor de cada parámetro (p.ej. el XML firmado en ``pszXml``) se inserta
    como texto y lxml lo escapa automáticamente.
    """
    env = etree.Element(
        "{%s}Envelope" % _NS_SOAPENV,
        nsmap={"soapenv": _NS_SOAPENV, "def": _NS_DEFAULT},
    )
    body = etree.SubElement(env, "{%s}Body" % _NS_SOAPENV)
    op = etree.SubElement(body, "{%s}%s" % (_NS_DEFAULT, operation))
    for name, value in params.items():
        param_node = etree.SubElement(op, name)
        param_node.text = value
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")


def _parse_response(response, tag: str) -> tuple[str | None, str | None]:
    """Extrae (ESTADO, <tag>) de la respuesta XML que devuelve el SII.

    Las respuestas vienen como un string XML del tipo::

        <SII:RESPUESTA><SII:RESP_HDR><ESTADO>00</ESTADO></SII:RESP_HDR>
        <SII:RESP_BODY><SEMILLA>..</SEMILLA></SII:RESP_BODY></SII:RESPUESTA>
    """
    if isinstance(response, str):
        response = response.encode("utf-8")
    tree = etree.fromstring(response)
    status_node = tree.find(".//{*}ESTADO")
    value_node = tree.find(".//{*}%s" % tag)
    status = status_node.text if status_node is not None else None
    value = value_node.text if value_node is not None else None
    return status, value
