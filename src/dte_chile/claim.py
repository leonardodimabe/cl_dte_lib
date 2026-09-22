"""Aceptar o reclamar ante el SII un DTE recibido (Ley 19.983).

Ojo con la distinción, que es la que cuesta caro: responderle al proveedor por
correo con un ``RespuestaDTE`` —lo que hace :mod:`dte_chile.exchange`— es el
intercambio comercial entre las dos empresas. Lo que corre el plazo de ocho
días y lo que mira el SII es **este** registro, que vive en un web service
aparte: el Registro de Aceptación o Reclamo de un DTE.

A los ocho días corridos de recibido, sin reclamo registrado, la factura se
entiende irrevocablemente aceptada y se hace cedible: el proveedor puede
venderla a un factoring y el deudor ya no puede discutirla.

El servicio es SOAP RPC/literal y se autentica con el mismo token que el resto
del SII —semilla firmada con el certificado— viajando como cookie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import requests
from lxml import etree

from ._http import build_session
from .certificate import Certificate
from .errors import SiiError
from .sii_client import Environment, SIIClient

#: Host del registro. No es el de siempre (palena/maullin): es otro servicio.
CLAIM_HOST = {
    Environment.CERTIFICATION: "https://ws2.sii.cl/WSREGISTRORECLAMODTECERT",
    Environment.PRODUCTION: "https://ws1.sii.cl/WSREGISTRORECLAMODTE",
}
CLAIM_PATH = "/registroreclamodteservice"
NS_CLAIM = "http://ws.registroreclamodte.diii.sdi.sii.cl"
NS_SOAPENV = "http://schemas.xmlsoap.org/soap/envelope/"

#: Tipos de documento que aceptan aceptación o reclamo: los que dan crédito
#: fiscal y se pueden ceder. Una boleta o una guía no entran aquí.
CLAIMABLE_TYPES = frozenset({33, 34, 43, 46, 56, 61})


class ClaimAction(StrEnum):
    """Lo que se registra en el SII sobre el documento recibido."""

    ACCEPT_CONTENT = "ACD"  # acepta el contenido del documento
    CLAIM_CONTENT = "RCD"  # reclama el contenido del documento
    GOODS_RECEIPT = "ERM"  # otorga el recibo de mercaderías o servicios
    PARTIAL_LACK = "RFP"  # reclama falta parcial de mercaderías
    TOTAL_LACK = "RFT"  # reclama falta total de mercaderías


@dataclass
class ClaimEvent:
    """Un evento del historial del documento en el SII."""

    code: str = ""
    label: str = ""
    date: str = ""
    responder_rut: str = ""


@dataclass
class ClaimResult:
    """Respuesta del registro: ``code`` 0 o 1 es éxito, según el SII."""

    code: int | None
    detail: str
    events: list[ClaimEvent] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.code in (0, 1)


class ClaimClient:
    """Cliente del Registro de Aceptación o Reclamo de DTE.

    Usa el mismo certificado y el mismo flujo de token que el envío de
    documentos, pero contra otro host: por eso no vive dentro de ``SIIClient``.
    """

    def __init__(
        self, certificate: Certificate, environment: Environment, timeout: int = 30
    ) -> None:
        self.cert = certificate
        self.environment = environment
        self.session = build_session()
        self._timeout = timeout
        self._token: str | None = None

    # ----- Token: el mismo del resto del SII -----
    def _get_token(self) -> str:
        if self._token is None:
            client = SIIClient(self.cert, self.environment, timeout=self._timeout)
            try:
                self._token = client.authenticate()
            finally:
                client.session.close()
        return self._token

    # ----- Llamada SOAP -----
    def _call(self, operation: str, params: dict[str, str]) -> ClaimResult:
        envelope = _build_envelope(operation, params)
        url = f"{CLAIM_HOST[self.environment]}{CLAIM_PATH}"
        try:
            resp = self.session.post(
                url,
                data=envelope,
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
                cookies={"TOKEN": self._get_token()},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as ex:
            raise SiiError(f"Error de red en {operation}: {ex}") from ex
        return _parse_result(resp.content, operation)

    # ----- Operaciones -----
    def register(
        self, issuer_rut: str, doc_type: int, folio: int, action: ClaimAction
    ) -> ClaimResult:
        """Registra en el SII la aceptación o el reclamo de un documento recibido.

        ``issuer_rut`` es el RUT de quien **emitió** el documento —el
        proveedor—, no el propio: el documento se identifica por su emisor,
        tipo y folio, que es lo que lo hace único ante el SII.
        """
        if doc_type not in CLAIMABLE_TYPES:
            raise ValueError(
                f"El documento tipo {doc_type} no admite aceptación ni reclamo en el SII."
            )
        body, dv = _split_rut(issuer_rut)
        return self._call(
            "ingresarAceptacionReclamoDoc",
            {
                "rutEmisor": body,
                "dvEmisor": dv,
                "tipoDoc": str(doc_type),
                "folio": str(folio),
                "accionDoc": str(action),
            },
        )

    def history(self, issuer_rut: str, doc_type: int, folio: int) -> ClaimResult:
        """El historial del documento en el SII: qué se registró y cuándo.

        Sirve para saber si el plazo ya corrió, si alguien aceptó antes, o si
        el reclamo que creíamos hecho quedó registrado de verdad.
        """
        body, dv = _split_rut(issuer_rut)
        return self._call(
            "listarEventosHistDoc",
            {
                "rutEmisor": body,
                "dvEmisor": dv,
                "tipoDoc": str(doc_type),
                "folio": str(folio),
            },
        )


# --------------------------------------------------------------------------- #
#  SOAP a mano (RPC/literal, como el resto de los servicios del SII)
# --------------------------------------------------------------------------- #
def _build_envelope(operation: str, params: dict[str, str]) -> bytes:
    env = etree.Element("{%s}Envelope" % NS_SOAPENV, nsmap={"soapenv": NS_SOAPENV, "ws": NS_CLAIM})
    body = etree.SubElement(env, "{%s}Body" % NS_SOAPENV)
    op = etree.SubElement(body, "{%s}%s" % (NS_CLAIM, operation))
    for name, value in params.items():
        # Los parámetros van sin prefijo: el binding es RPC/literal.
        etree.SubElement(op, name).text = value
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")


def _parse_result(xml: bytes, operation: str) -> ClaimResult:
    tree = etree.fromstring(xml)
    fault = tree.find(".//{*}Fault")
    if fault is not None:
        detalle = fault.findtext(".//faultstring") or fault.findtext(".//{*}faultstring") or ""
        raise SiiError(f"El SII rechazó {operation}: {detalle}")
    ret = tree.find(".//{*}%sResponse" % operation)
    if ret is None:
        raise SiiError(f"Respuesta SOAP sin <{operation}Response>.")
    code = ret.findtext(".//{*}codResp")
    return ClaimResult(
        code=int(code) if code is not None and code.strip().lstrip("-").isdigit() else None,
        detail=(ret.findtext(".//{*}descResp") or "").strip(),
        events=[
            ClaimEvent(
                code=(nodo.findtext(".//{*}codEvento") or "").strip(),
                label=(nodo.findtext(".//{*}descEvento") or "").strip(),
                date=(nodo.findtext(".//{*}fechaEvento") or "").strip(),
                responder_rut=(nodo.findtext(".//{*}rutResponde") or "").strip(),
            )
            for nodo in ret.findall(".//{*}listaEventosDoc")
        ],
    )


def _split_rut(rut: str) -> tuple[str, str]:
    """Separa «77262159-0» en («77262159», «0»), que es como lo pide el SII."""
    limpio = rut.strip().upper().replace(".", "").replace(" ", "")
    if "-" in limpio:
        cuerpo, dv = limpio.rsplit("-", 1)
    else:
        cuerpo, dv = limpio[:-1], limpio[-1:]
    if not cuerpo.isdigit() or not dv:
        raise ValueError(f"RUT mal formado: {rut!r}")
    return cuerpo, dv
