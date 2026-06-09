"""Acuses de intercambio entre contribuyentes (recepción de DTE).

Cuando un emisor te envía un EnvioDTE, debes responder con documentos firmados.
Este módulo cubre los dos del set de certificación:

  1. RespuestaDTE  → acuse de recibo del envío (RecepcionEnvio) **o** aceptación
     o rechazo comercial de cada DTE (ResultadoDTE). El XSD usa un <xs:choice>.
  2. EnvioRecibos  → recibo de mercaderías/servicios (Ley 19.983), con firma del
     conjunto y una firma por cada recibo.

Todo se construye en el namespace SiiDte de forma consistente (sin filtrar
namespaces extra) para que las firmas anidadas validen tras round-trip.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from lxml import etree

from . import signer
from .certificate import Certificate

NS = "http://www.sii.cl/SiiDte"
NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"

# Códigos de estado (SII): 0 = conforme/aceptado.
STATUS_OK = 0

# Declaración del recibo de mercaderías (Ley 19.983). DEBE ser EXACTAMENTE el
# valor fijo del XSD (Recibos_v10.xsd): sin acentos y sin "°" tras los artículos.
RECEIPT_DECLARATION = (
    "El acuse de recibo que se declara en este acto, de acuerdo a lo dispuesto "
    "en la letra b) del Art. 4, y la letra c) del Art. 5 de la Ley 19.983, "
    "acredita que la entrega de mercaderias o servicio(s) prestado(s) ha(n) sido "
    "recibido(s)."
)


@dataclass
class ReceivedDocument:
    doc_type: int
    folio: int
    issue_date: _dt.date
    issuer_rut: str
    receiver_rut: str
    total_amount: int


@dataclass
class Contact:
    name: str = "Contacto"
    phone: str = ""
    email: str = ""


@dataclass
class ReceivedEnvelope:
    """Resultado de parsear un EnvioDTE recibido."""

    envelope_name: str
    set_dte_id: str
    digest: str | None
    issuer_rut: str
    receiver_rut: str
    documents: list[ReceivedDocument] = field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Parseo del EnvioDTE recibido
# --------------------------------------------------------------------------- #
def parse_envelope(xml: bytes, envelope_name: str = "envio.xml") -> ReceivedEnvelope:
    """Lee un EnvioDTE recibido y extrae los datos necesarios para responder."""
    root = etree.fromstring(xml)
    set_dte = _local(root, "SetDTE")
    cover = _local(set_dte, "Caratula")

    # Digest del SetDTE = DigestValue de la firma exterior del EnvioDTE.
    digest = None
    for sig in root:
        if etree.QName(sig).localname == "Signature":
            dv = sig.find(".//{%s}DigestValue" % NS_DSIG)
            digest = dv.text if dv is not None else None
            break

    documents = []
    for doc in root.iter():
        if etree.QName(doc).localname != "Documento":
            continue
        header = _local(doc, "Encabezado")
        id_doc = _local(header, "IdDoc")
        issuer = _local(header, "Emisor")
        receiver = _local(header, "Receptor")
        totals = _local(header, "Totales")
        documents.append(
            ReceivedDocument(
                doc_type=int(_local_text(id_doc, "TipoDTE")),
                folio=int(_local_text(id_doc, "Folio")),
                issue_date=_dt.date.fromisoformat(_local_text(id_doc, "FchEmis")),
                issuer_rut=_local_text(issuer, "RUTEmisor"),
                receiver_rut=_local_text(receiver, "RUTRecep"),
                total_amount=int(_local_text(totals, "MntTotal")),
            )
        )

    return ReceivedEnvelope(
        envelope_name=envelope_name,
        set_dte_id=set_dte.get("ID"),
        digest=digest,
        issuer_rut=_local_text(cover, "RutEmisor"),
        receiver_rut=_local_text(cover, "RutReceptor"),
        documents=documents,
    )


# --------------------------------------------------------------------------- #
#  RespuestaDTE
# --------------------------------------------------------------------------- #
# El XSD define <Resultado> con un <xs:choice>: una RespuestaDTE es ACUSE DE
# RECIBO (RecepcionEnvio) **o** RESULTADO comercial (ResultadoDTE), no ambos.


def build_receipt_acknowledgment(
    envelope: ReceivedEnvelope,
    cert: Certificate,
    timestamp: _dt.datetime,
    contact: Contact | None = None,
    response_id: int = 1,
    envelope_code: int = 1,
) -> etree._Element:
    """RespuestaDTE de acuse de recibo del envío (RecepcionEnvio)."""
    root, result = _base_response(envelope, contact, timestamp, response_id, 1)

    reception = etree.SubElement(result, "{%s}RecepcionEnvio" % NS)
    _t(reception, "NmbEnvio", envelope.envelope_name)
    _t(reception, "FchRecep", _ts(timestamp))
    _t(reception, "CodEnvio", str(envelope_code))
    _t(reception, "EnvioDTEID", envelope.set_dte_id)
    if envelope.digest:
        _t(reception, "Digest", envelope.digest)
    _t(reception, "RutEmisor", envelope.issuer_rut)
    _t(reception, "RutReceptor", envelope.receiver_rut)
    _t(reception, "EstadoRecepEnv", str(STATUS_OK))
    _t(reception, "RecepEnvGlosa", "Envio Recibido Conforme")
    _t(reception, "NroDTE", str(len(envelope.documents)))
    for document in envelope.documents:
        reception_dte = etree.SubElement(reception, "{%s}RecepcionDTE" % NS)
        _document_data(reception_dte, document)  # RecepcionDTE NO lleva CodEnvio
        _t(reception_dte, "EstadoRecepDTE", str(STATUS_OK))
        _t(reception_dte, "RecepDTEGlosa", "DTE Recibido Conforme")

    return signer.sign_enveloped(root, result, cert)


def build_result_response(
    envelope: ReceivedEnvelope,
    cert: Certificate,
    timestamp: _dt.datetime,
    accept: bool = True,
    rejection_label: str = "",
    contact: Contact | None = None,
    response_id: int = 1,
    envelope_code: int = 1,
) -> etree._Element:
    """RespuestaDTE de aceptación/rechazo comercial (ResultadoDTE)."""
    root, result = _base_response(
        envelope, contact, timestamp, response_id, len(envelope.documents)
    )

    for document in envelope.documents:
        result_dte = etree.SubElement(result, "{%s}ResultadoDTE" % NS)
        _document_data(result_dte, document, with_envelope_code=True, envelope_code=envelope_code)
        if accept:
            _t(result_dte, "EstadoDTE", str(STATUS_OK))
            _t(result_dte, "EstadoDTEGlosa", "DTE Aceptado OK")
        else:
            _t(result_dte, "EstadoDTE", "2")  # 2 = rechazado
            _t(result_dte, "EstadoDTEGlosa", rejection_label or "DTE Rechazado")

    return signer.sign_enveloped(root, result, cert)


def _base_response(envelope, contact, timestamp, response_id, detail_count):
    """Crea <RespuestaDTE><Resultado><Caratula> y devuelve (root, result)."""
    contact = contact or Contact()
    root = etree.Element("{%s}RespuestaDTE" % NS, nsmap={None: NS}, version="1.0")
    result = etree.SubElement(root, "{%s}Resultado" % NS, ID="Respuesta")
    cover = etree.SubElement(result, "{%s}Caratula" % NS, version="1.0")
    _t(cover, "RutResponde", envelope.receiver_rut)  # quien recibió el DTE responde
    _t(cover, "RutRecibe", envelope.issuer_rut)  # el emisor original recibe
    _t(cover, "IdRespuesta", str(response_id))
    _t(cover, "NroDetalles", str(detail_count))
    _t(cover, "NmbContacto", contact.name)
    if contact.email:
        _t(cover, "MailContacto", contact.email)
    _t(cover, "TmstFirmaResp", _ts(timestamp))
    return root, result


def _document_data(parent, document: ReceivedDocument, with_envelope_code=False, envelope_code=1):
    _t(parent, "TipoDTE", str(document.doc_type))
    _t(parent, "Folio", str(document.folio))
    _t(parent, "FchEmis", document.issue_date.isoformat())
    _t(parent, "RUTEmisor", document.issuer_rut)
    _t(parent, "RUTRecep", document.receiver_rut)
    _t(parent, "MntTotal", str(document.total_amount))
    # CodEnvio solo va en ResultadoDTE, NO en RecepcionDTE (según el XSD).
    if with_envelope_code:
        _t(parent, "CodEnvio", str(envelope_code))


# --------------------------------------------------------------------------- #
#  EnvioRecibos (recibo de mercaderías - Ley 19.983)
# --------------------------------------------------------------------------- #
def build_receipts_envelope(
    envelope: ReceivedEnvelope,
    cert: Certificate,
    timestamp: _dt.datetime,
    location: str,
    contact: Contact | None = None,
) -> etree._Element:
    """Construye y firma un EnvioRecibos (un Recibo por DTE, firma anidada)."""
    contact = contact or Contact()
    responder_rut = envelope.receiver_rut
    recipient_rut = envelope.issuer_rut
    signer_rut = cert.rut or responder_rut

    root = etree.Element("{%s}EnvioRecibos" % NS, nsmap={None: NS}, version="1.0")
    set_receipts = etree.SubElement(root, "{%s}SetRecibos" % NS, ID="SetRecibos")

    cover = etree.SubElement(set_receipts, "{%s}Caratula" % NS, version="1.0")
    _t(cover, "RutResponde", responder_rut)
    _t(cover, "RutRecibe", recipient_rut)
    _t(cover, "NmbContacto", contact.name)
    if contact.email:
        _t(cover, "MailContacto", contact.email)
    _t(cover, "TmstFirmaEnv", _ts(timestamp))

    # Un Recibo por documento, cada uno con su firma sobre DocumentoRecibo.
    for i, document in enumerate(envelope.documents, start=1):
        receipt = etree.SubElement(set_receipts, "{%s}Recibo" % NS, version="1.0")
        receipt_doc = etree.SubElement(receipt, "{%s}DocumentoRecibo" % NS, ID=f"Recibo{i}")
        _t(receipt_doc, "TipoDoc", str(document.doc_type))
        _t(receipt_doc, "Folio", str(document.folio))
        _t(receipt_doc, "FchEmis", document.issue_date.isoformat())
        _t(receipt_doc, "RUTEmisor", document.issuer_rut)
        _t(receipt_doc, "RUTRecep", document.receiver_rut)
        _t(receipt_doc, "MntTotal", str(document.total_amount))
        _t(receipt_doc, "Recinto", location)
        _t(receipt_doc, "RutFirma", signer_rut)
        _t(receipt_doc, "Declaracion", RECEIPT_DECLARATION)
        _t(receipt_doc, "TmstFirmaRecibo", _ts(timestamp))
        signer.sign_enveloped(receipt, receipt_doc, cert)

    # Normalizar antes de firmar el set (igual que en EnvioDTE), para que las
    # firmas de cada Recibo sobrevivan al round-trip.
    root = etree.fromstring(etree.tostring(root))
    set_receipts = _local(root, "SetRecibos")
    return signer.sign_enveloped(root, set_receipts, cert)


def serialize(element: etree._Element) -> bytes:
    return etree.tostring(element, xml_declaration=True, encoding="ISO-8859-1")


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _t(parent: etree._Element, tag: str, value: str) -> None:
    etree.SubElement(parent, "{%s}%s" % (NS, tag)).text = value


def _ts(timestamp: _dt.datetime) -> str:
    return timestamp.replace(microsecond=0).isoformat()


def _local(parent: etree._Element, name: str) -> etree._Element:
    """Primer descendiente con ese nombre local (ignorando namespace)."""
    for node in parent.iter():
        if node is not parent and etree.QName(node).localname == name:
            return node
    raise ValueError(f"No se encontró <{name}>.")


def _local_text(parent: etree._Element, name: str) -> str:
    return _local(parent, name).text
