"""Cesión electrónica de facturas (factoring): el AEC y su envío al RPETC.

Ceder una factura es venderle a un tercero el derecho a cobrarla. Para que el
deudor quede obligado a pagarle al cesionario y no al vendedor, la cesión tiene
que anotarse en el **Registro Público Electrónico de Transferencia de Créditos**
del SII, y eso se hace mandando un AEC: un Archivo Electrónico de Cesión.

El AEC son tres documentos anidados, **cada uno con su propia firma**:

1. ``DTECedido`` envuelve la factura original tal como se emitió, con su timbre
   y su firma intactos: es la prueba de qué se está cediendo.
2. ``Cesion`` es el contrato: quién cede, a quién, por cuánto y hasta cuándo.
   Lleva la **declaración jurada** del artículo 3 de la Ley 19.983, donde el
   cedente declara tener a disposición del cesionario el recibo de las
   mercaderías. Por eso el recibo (:mod:`dte_chile.exchange`) importa: sin él
   la factura no es cedible y la declaración sería falsa.
3. ``AEC`` es el sobre que se manda, con su carátula.

Dos condiciones que no se pueden saltar: la factura tiene que estar **aceptada
por el SII** —no basta con emitirla— y no puede estar pagada. Ceder una factura
ya pagada es vender un crédito que no existe.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

import requests
from lxml import etree

from ._http import build_session
from .certificate import Certificate
from .errors import SiiError
from .signer import sign_enveloped
from .sii_client import Environment, SIIClient
from .validation import serialize_document

NS = "http://www.sii.cl/SiiDte"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

#: Documentos que se pueden ceder: los que dan crédito fiscal y llevan copia
#: cedible. Una boleta o una guía no se ceden.
CEDIBLE_TYPES = frozenset({33, 34, 43, 46})

#: Ruta de subida del AEC. No es la de los DTE: el registro de cesiones es otro
#: servicio dentro del mismo sitio del SII.
UPLOAD_PATH = "/cgi_rtc/RTC/RTCAnotEnvio.cgi"
STATUS_SERVICE = "/DTEWS/services/wsRPETCConsulta"

#: Estados del envío que devuelve el RPETC.
ACCEPTED_STATUS = "EOK"
IN_PROCESS_STATUS = frozenset({"UPL", "RCP", "SOK", "FSO", "COK", "VDC", "VCS"})


@dataclass
class Party:
    """Cedente o cesionario. El correo es obligatorio: por ahí avisa el SII."""

    rut: str
    name: str
    address: str = ""
    email: str = ""


@dataclass
class Signatory:
    """Quien firma la cesión por el cedente: una persona, con su RUT."""

    rut: str
    name: str


@dataclass
class Cession:
    """Una cesión: qué factura, de quién a quién, por cuánto y hasta cuándo."""

    dte: etree._Element  # el <DTE> firmado tal como se emitió
    assignor: Party  # cedente
    assignee: Party  # cesionario
    signatory: Signatory
    amount: int
    due_date: _dt.date
    sequence: int = 1
    declaration: str = ""

    def declaration_text(self) -> str:
        """La declaración jurada del artículo 3 de la Ley 19.983.

        Se puede reemplazar entera, pero la de por omisión es la que pide el
        SII: declarar que el recibo de las mercaderías está a disposición del
        cesionario. Es lo que hace oponible la cesión al deudor.
        """
        if self.declaration:
            return self.declaration
        datos = _document_data(self.dte)
        return (
            f"Yo, {self.signatory.name}, RUN {self.signatory.rut}, en representación de "
            f"{self.assignor.name}, RUT {self.assignor.rut}, declaro bajo juramento que he "
            f"puesto a disposición del cesionario {self.assignee.name}, RUT "
            f"{self.assignee.rut}, el o los documentos donde constan los recibos de las "
            f"mercaderías entregadas o servicios prestados, entregados por parte del deudor "
            f"de la factura {datos['folio']}, RUT {datos['receiver_rut']}, de acuerdo a lo "
            f"establecido en la Ley N° 19.983."
        )


@dataclass
class CessionSubmission:
    """Resultado de subir el AEC al RPETC."""

    track_id: str | None
    status: str
    detail: str = ""

    @property
    def accepted(self) -> bool:
        return self.status == ACCEPTED_STATUS

    @property
    def in_process(self) -> bool:
        return self.status in IN_PROCESS_STATUS


# --------------------------------------------------------------------------- #
#  Armado del AEC
# --------------------------------------------------------------------------- #
def build_aec(cession: Cession, cert: Certificate, timestamp: _dt.datetime) -> etree._Element:
    """Arma el AEC completo, con sus tres firmas."""
    datos = _document_data(cession.dte)
    if datos["doc_type"] not in CEDIBLE_TYPES:
        raise ValueError(
            f"El documento tipo {datos['doc_type']} no es cedible: la cesión sólo "
            f"corre para {sorted(CEDIBLE_TYPES)}."
        )

    cedido = _build_dte_cedido(cession.dte, timestamp)
    sign_enveloped(cedido, cedido[0], cert)

    cesion = _build_cesion(cession, datos, timestamp)
    sign_enveloped(cesion, cesion[0], cert)

    aec = _build_envelope(cession, cedido, cesion, timestamp)
    sign_enveloped(aec, aec[0], cert)
    return aec


def serialize(aec: etree._Element) -> bytes:
    """Serializa el AEC como lo espera el SII (ISO-8859-1 con declaración)."""
    return serialize_document(aec)


def _build_dte_cedido(dte: etree._Element, timestamp: _dt.datetime) -> etree._Element:
    """El documento cedido: la factura original, intacta, con su marca de tiempo.

    El ``<DTE>`` entra tal cual, con su timbre y su firma: si se tocara, la
    firma del emisor dejaría de valer y el SII rechazaría la cesión.
    """
    # Sin declarar xsi: cuanto menos namespace se meta alrededor del <DTE>, menos
    # riesgo de moverle la forma canónica a la firma del emisor, que no se puede
    # recalcular. Es también lo que hace el módulo chileno de Odoo.
    root = etree.Element("{%s}DTECedido" % NS, nsmap={None: NS}, version="1.0")
    documento = etree.SubElement(root, "{%s}DocumentoDTECedido" % NS, ID="DTE_Cedido")
    documento.append(_copy(dte))
    etree.SubElement(documento, "{%s}TmstFirma" % NS).text = _ts(timestamp)
    return root


def _build_cesion(cession: Cession, datos: dict, timestamp: _dt.datetime) -> etree._Element:
    """El contrato de cesión: quién cede, a quién, por cuánto y hasta cuándo."""
    root = etree.Element("{%s}Cesion" % NS, nsmap={None: NS}, version="1.0")
    documento = etree.SubElement(
        root, "{%s}DocumentoCesion" % NS, ID="Cesion_%s_%s" % (datos["doc_type"], datos["folio"])
    )
    etree.SubElement(documento, "{%s}SeqCesion" % NS).text = str(cession.sequence)

    id_dte = etree.SubElement(documento, "{%s}IdDTE" % NS)
    etree.SubElement(id_dte, "{%s}TipoDTE" % NS).text = str(datos["doc_type"])
    etree.SubElement(id_dte, "{%s}RUTEmisor" % NS).text = datos["issuer_rut"]
    etree.SubElement(id_dte, "{%s}RUTReceptor" % NS).text = datos["receiver_rut"]
    etree.SubElement(id_dte, "{%s}Folio" % NS).text = str(datos["folio"])
    etree.SubElement(id_dte, "{%s}FchEmis" % NS).text = datos["issue_date"]
    etree.SubElement(id_dte, "{%s}MntTotal" % NS).text = str(datos["total_amount"])

    cedente = etree.SubElement(documento, "{%s}Cedente" % NS)
    _party(cedente, cession.assignor)
    autorizado = etree.SubElement(cedente, "{%s}RUTAutorizado" % NS)
    etree.SubElement(autorizado, "{%s}RUT" % NS).text = cession.signatory.rut
    etree.SubElement(autorizado, "{%s}Nombre" % NS).text = cession.signatory.name[:40]
    etree.SubElement(cedente, "{%s}DeclaracionJurada" % NS).text = cession.declaration_text()

    cesionario = etree.SubElement(documento, "{%s}Cesionario" % NS)
    _party(cesionario, cession.assignee)

    etree.SubElement(documento, "{%s}MontoCesion" % NS).text = str(cession.amount)
    etree.SubElement(documento, "{%s}UltimoVencimiento" % NS).text = cession.due_date.isoformat()
    etree.SubElement(documento, "{%s}TmstCesion" % NS).text = _ts(timestamp)
    return root


def _build_envelope(
    cession: Cession,
    cedido: etree._Element,
    cesion: etree._Element,
    timestamp: _dt.datetime,
) -> etree._Element:
    root = etree.Element("{%s}AEC" % NS, nsmap={None: NS, "xsi": NS_XSI}, version="1.0")
    root.set("{%s}schemaLocation" % NS_XSI, "http://www.sii.cl/SiiDte AEC_v10.xsd")
    documento = etree.SubElement(root, "{%s}DocumentoAEC" % NS, ID="AEC")

    caratula = etree.SubElement(documento, "{%s}Caratula" % NS, version="1.0")
    etree.SubElement(caratula, "{%s}RutCedente" % NS).text = cession.assignor.rut
    etree.SubElement(caratula, "{%s}RutCesionario" % NS).text = cession.assignee.rut
    etree.SubElement(caratula, "{%s}NmbContacto" % NS).text = cession.signatory.name[:40]
    etree.SubElement(caratula, "{%s}MailContacto" % NS).text = cession.assignor.email
    etree.SubElement(caratula, "{%s}TmstFirmaEnvio" % NS).text = _ts(timestamp)

    cesiones = etree.SubElement(documento, "{%s}Cesiones" % NS)
    cesiones.append(cedido)
    cesiones.append(cesion)
    return root


def _party(node: etree._Element, party: Party) -> None:
    etree.SubElement(node, "{%s}RUT" % NS).text = party.rut
    etree.SubElement(node, "{%s}RazonSocial" % NS).text = party.name[:100]
    etree.SubElement(node, "{%s}Direccion" % NS).text = party.address[:80]
    etree.SubElement(node, "{%s}eMail" % NS).text = party.email[:80]


def _copy(node: etree._Element) -> etree._Element:
    """Copia el nodo sin arrastrar el árbol del que venía."""
    return etree.fromstring(etree.tostring(node))


def _ts(timestamp: _dt.datetime) -> str:
    return timestamp.replace(microsecond=0).isoformat()


def _document_data(dte: etree._Element) -> dict:
    """Lee del <DTE> firmado lo que la cesión necesita repetir."""
    encabezado = dte.find(".//{%s}Encabezado" % NS)
    if encabezado is None:
        raise ValueError("El documento cedido no tiene <Encabezado>: no es un DTE.")

    def texto(ruta: str) -> str:
        nodo = encabezado.find(ruta.replace("{}", "{%s}" % NS))
        return (nodo.text or "").strip() if nodo is not None else ""

    total = texto("{}Totales/{}MntTotal")
    return {
        "doc_type": int(texto("{}IdDoc/{}TipoDTE") or 0),
        "folio": int(texto("{}IdDoc/{}Folio") or 0),
        "issue_date": texto("{}IdDoc/{}FchEmis"),
        "issuer_rut": texto("{}Emisor/{}RUTEmisor"),
        "receiver_rut": texto("{}Receptor/{}RUTRecep"),
        "total_amount": int(float(total)) if total else 0,
    }


# --------------------------------------------------------------------------- #
#  Envío y estado en el RPETC
# --------------------------------------------------------------------------- #
class CessionClient:
    """Sube el AEC al Registro Público Electrónico de Transferencia de Créditos.

    Comparte host y token con el resto del SII, pero sube por otra ruta y
    consulta el estado por otro servicio: el envío de cesiones no pasa por el
    mismo buzón que los DTE.
    """

    def __init__(
        self, certificate: Certificate, environment: Environment, timeout: int = 30
    ) -> None:
        self.cert = certificate
        self.environment = environment
        self.session = build_session()
        self._timeout = timeout
        self._sii = SIIClient(certificate, environment, timeout=timeout)

    def close(self) -> None:
        self.session.close()
        self._sii.session.close()

    def _token(self) -> str:
        return self._sii.authenticate()

    def send(
        self, aec_xml: bytes, assignor_rut: str, sender_rut: str, notify_email: str = ""
    ) -> CessionSubmission:
        """Sube el AEC y devuelve su Track ID.

        ``notify_email`` es la casilla a la que el SII avisa del resultado; va
        en el envío porque el RPETC responde por correo, no sólo por la
        consulta de estado.
        """
        cuerpo, dv = _split_rut(assignor_rut)
        files = {
            "emailNotif": (None, notify_email),
            "rutCompany": (None, cuerpo),
            "dvCompany": (None, dv),
            "archivo": ("aec.xml", aec_xml, "application/xml"),
        }
        try:
            resp = self.session.post(
                f"{self.environment.host}{UPLOAD_PATH}",
                files=files,  # type: ignore[arg-type]
                headers={"User-Agent": SIIClient.user_agent(sender_rut)},
                cookies={"TOKEN": self._token()},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as ex:
            raise SiiError(f"Fallo al subir el AEC: {ex}") from ex

        try:
            tree = etree.fromstring(resp.content)
        except etree.XMLSyntaxError:
            return CessionSubmission(track_id=None, status="ERROR_NO_XML", detail=resp.text[:400])
        return CessionSubmission(
            track_id=tree.findtext(".//{*}TRACKID"),
            status=tree.findtext(".//{*}STATUS") or tree.findtext(".//{*}ESTADO") or "?",
            detail=resp.text[:400],
        )

    def status(self, track_id: str) -> CessionSubmission:
        """Consulta el estado del envío en el RPETC."""
        respuesta = self._sii._soap_call(  # noqa: SLF001 - mismo paquete
            STATUS_SERVICE, "getEstEnvio", {"Token": self._token(), "TrackId": track_id}
        )
        tree = etree.fromstring(
            respuesta.encode("utf-8") if isinstance(respuesta, str) else respuesta
        )
        return CessionSubmission(
            track_id=track_id,
            status=(tree.findtext(".//{*}ESTADO_ENVIO") or tree.findtext(".//{*}ESTADO") or "?"),
            detail=(tree.findtext(".//{*}GLOSA_ESTADO") or tree.findtext(".//{*}GLOSA") or ""),
        )


def _split_rut(rut: str) -> tuple[str, str]:
    limpio = rut.strip().upper().replace(".", "").replace(" ", "")
    cuerpo, _, dv = limpio.rpartition("-")
    if not cuerpo:
        cuerpo, dv = limpio[:-1], limpio[-1:]
    if not cuerpo.isdigit() or not dv:
        raise ValueError(f"RUT mal formado: {rut!r}")
    return cuerpo, dv
