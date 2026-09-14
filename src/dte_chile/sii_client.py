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

import datetime as _dt
import logging
from dataclasses import dataclass, field
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
class DocTypeStats:
    """Cuántos documentos de un tipo aceptó, rechazó o reparó el SII.

    Sale del bloque ``ESTADISTICA`` que devuelve ``QueryEstUp``. Es el dato que
    de verdad dice cómo fue el envío: el ESTADO del sobre puede ser ``EPR``
    —«envío procesado»— con todos sus documentos rechazados dentro, porque una
    cosa es que el sobre se haya podido leer y otra que su contenido valga.
    """

    doc_type: int
    informed: int = 0
    accepted: int = 0
    rejected: int = 0
    flagged: int = 0


@dataclass
class DocumentStatus:
    """Lo que el SII dice de UN documento: su estado y el porqué.

    ``label`` es la glosa del estado (p. ej. «DTE Recibido»); ``error_label``,
    la del reparo o rechazo cuando lo hay. El SII no siempre rellena los mismos
    campos, así que se guarda ``raw`` para poder leer lo que no se mapeó.
    """

    doc_type: int
    folio: int
    status: str
    label: str = ""
    error_label: str = ""
    raw: str = ""


@dataclass
class SubmissionResult:
    track_id: str | None
    status: str
    detail: str = ""
    #: Desglose por tipo de documento. Vacío en libros, que no lo llevan.
    stats: list[DocTypeStats] = field(default_factory=list)

    @property
    def accepted(self) -> int:
        return sum(s.accepted for s in self.stats)

    @property
    def rejected(self) -> int:
        return sum(s.rejected for s in self.stats)

    @property
    def flagged(self) -> int:
        return sum(s.flagged for s in self.stats)

    @property
    def informed(self) -> int:
        return sum(s.informed for s in self.stats)


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
    # El CGI de upload FILTRA por User-Agent: sólo procesa el formato que el SII
    # documenta para sistemas propios. Con cualquier otro —incluido el de un
    # navegador real— devuelve una página HTML de error genérica que no dice por
    # qué, y el envío nunca llega a validarse. Verificado contra Maullín.
    _USER_AGENT_TEMPLATE = "Mozilla/4.0 (compatible; PROG 1.0; Windows NT 5.0; {rut})"

    @classmethod
    def user_agent(cls, sender_rut: str) -> str:
        """User-Agent en el formato que exige el SII, con el RUT de quien envía."""
        return cls._USER_AGENT_TEMPLATE.format(rut=sender_rut)

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
                headers={"User-Agent": self.user_agent(sender_rut)},
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
            # Los nombres salen del WSDL (QueryEstUp.jws?WSDL): el servicio es
            # Axis RPC y liga los parámetros por nombre, no por posición.
            {
                "RutCompania": issuer_body,
                "DvCompania": issuer_dv,
                "TrackId": str(track_id),
                "Token": self._token,
            },
        )
        status, _ = _parse_response(response, "ESTADO")
        tree = etree.fromstring(response.encode("utf-8") if isinstance(response, str) else response)
        label_node = tree.find(".//{*}GLOSA")
        label = label_node.text if label_node is not None else ""
        return SubmissionResult(
            track_id=track_id,
            status=status or "?",
            detail=label or response,
            stats=_parse_stats(tree),
        )

    # ----- 6) Consulta del estado de UN documento -----
    DOC_QUERY_SVC = "/DTEWS/QueryEstDte.jws"

    def query_document(
        self,
        *,
        issuer_rut: str,
        receiver_rut: str,
        doc_type: int | str,
        folio: int | str,
        issue_date: _dt.date,
        total_amount: int | str,
        querier_rut: str | None = None,
    ) -> DocumentStatus:
        """Estado de un documento concreto ante el SII, con su glosa.

        ``getEstUp`` (por TrackID) sólo entrega el recuento por tipo: cuántos
        aceptados, rechazados y con reparo. No dice **cuál** ni **por qué**, y
        para averiguarlo había que pedirle al SII que mandara el detalle por
        correo. Esto lo pregunta documento por documento.

        El SII identifica el documento por la tupla completa —emisor, receptor,
        tipo, folio, fecha y monto total—, no sólo por el folio: es su forma de
        comprobar que quien pregunta conoce el documento.

        ``querier_rut`` es quien consulta; por omisión, el titular del
        certificado con que se autentica.
        """
        if not self._token:
            self.authenticate()
        assert self._token is not None
        consultante = querier_rut or self.cert.rut or issuer_rut
        cons_body, cons_dv = consultante.split("-")
        emis_body, emis_dv = issuer_rut.split("-")
        recep_body, recep_dv = receiver_rut.split("-")
        response = self._soap_call(
            self.DOC_QUERY_SVC,
            "getEstDte",
            # Nombres y orden del WSDL (QueryEstDte.jws?WSDL).
            {
                "RutConsultante": cons_body,
                "DvConsultante": cons_dv,
                "RutCompania": emis_body,
                "DvCompania": emis_dv,
                "RutReceptor": recep_body,
                "DvReceptor": recep_dv,
                "TipoDte": str(int(doc_type)),
                "FolioDte": str(int(folio)),
                # El SII espera dd-mm-aaaa acá, no el ISO del documento.
                "FechaEmisionDte": issue_date.strftime("%d-%m-%Y"),
                "MontoDte": str(int(total_amount)),
                "Token": self._token,
            },
        )
        tree = etree.fromstring(response.encode("utf-8") if isinstance(response, str) else response)

        def texto(tag: str) -> str:
            nodo = tree.find(f".//{{*}}{tag}")
            return (nodo.text or "").strip() if nodo is not None and nodo.text else ""

        return DocumentStatus(
            doc_type=int(doc_type),
            folio=int(folio),
            status=texto("ESTADO") or "?",
            label=texto("GLOSA_ESTADO") or texto("GLOSA"),
            error_label=texto("GLOSA_ERR") or texto("ERR_CODE"),
            raw=response if isinstance(response, str) else response.decode("utf-8", "replace"),
        )


def _parse_stats(tree) -> list[DocTypeStats]:
    """Lee el desglose por tipo de documento de la respuesta de QueryEstUp.

    El SII lo devuelve **plano**: dentro de ``RESP_BODY`` van TIPO_DOCTO,
    INFORMADOS, ACEPTADOS, RECHAZADOS y REPAROS repetidos uno tras otro, sin
    ningún elemento que agrupe cada tanda. Su documentación describe un
    ``ESTADISTICA`` que envuelve cada grupo; la respuesta real de Maullín no lo
    trae. Se recorre en orden de documento y cada TIPO_DOCTO abre un grupo
    nuevo, lo que sirve para las dos formas.
    """
    CAMPOS = {
        "INFORMADOS": "informed",
        "ACEPTADOS": "accepted",
        "RECHAZADOS": "rejected",
        "REPAROS": "flagged",
    }

    def entero(texto: str | None) -> int:
        try:
            return int((texto or "").strip())
        except ValueError:
            return 0

    salida: list[DocTypeStats] = []
    actual: DocTypeStats | None = None
    for nodo in tree.iter():
        etiqueta = str(nodo.tag).rsplit("}", 1)[-1]
        if etiqueta == "TIPO_DOCTO":
            tipo = entero(nodo.text)
            if not tipo:
                continue
            actual = DocTypeStats(doc_type=tipo)
            salida.append(actual)
        elif actual is not None and etiqueta in CAMPOS:
            setattr(actual, CAMPOS[etiqueta], entero(nodo.text))
    return salida


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
