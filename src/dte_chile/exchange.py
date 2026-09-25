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
from .validation import serialize_document

NS = "http://www.sii.cl/SiiDte"
NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"

# Códigos de estado (SII): 0 = conforme/aceptado.
STATUS_OK = 0

# Estados y glosas del «Formato Mensaje de Respuesta a Documentos Tributarios
# Electrónicos» del SII (formato_ic.pdf). La glosa se repite tal cual.
ENVELOPE_RECEIVED = (0, "Envio Recibido Conforme")
ENVELOPE_WRONG_RECEIVER = (3, "Envio Rechazado - RUT Receptor No Corresponde")
DTE_RECEIVED = (0, "DTE Recibido OK")
DTE_WRONG_RECEIVER = (3, "DTE No Recibido - Error en RUT Receptor")
RESULT_ACCEPTED = (0, "ACEPTADO OK")
RESULT_REJECTED = 2

# Declaración del recibo de mercaderías (Ley 19.983). DEBE ser EXACTAMENTE el
# valor fijo del XSD (Recibos_v10.xsd): sin acentos y sin "°" tras los artículos.
RECEIPT_DECLARATION = (
    "El acuse de recibo que se declara en este acto, de acuerdo a lo dispuesto "
    "en la letra b) del Art. 4, y la letra c) del Art. 5 de la Ley 19.983, "
    "acredita que la entrega de mercaderias o servicio(s) prestado(s) ha(n) sido "
    "recibido(s)."
)


@dataclass
class ReceivedLine:
    """Una línea de detalle tal como la escribió el proveedor.

    No se reconstruye el dominio de emisión (`parser.parse_document`): ese
    camino exige que los totales recalculados calcen y redondea precios a
    enteros, y un documento ajeno puede traer precios con decimales, impuestos
    adicionales o montos brutos. Aquí se entrega lo que dice el XML, sin
    recalcular nada: quien lo recibe decide qué hacer con él.
    """

    number: int
    name: str
    #: MontoItem: el monto de la línea con descuentos y recargos aplicados.
    amount: float
    description: str = ""
    #: Pares (TpoCodigo, VlrCodigo). El código es el del catálogo del
    #: proveedor, no el nuestro: por eso se entrega con su tipo.
    codes: list[tuple[str, str]] = field(default_factory=list)
    quantity: float | None = None
    #: UnmdItem, texto libre («KG», «UN», «CJ»): no hay tabla que lo normalice.
    unit: str = ""
    unit_price: float | None = None
    discount_amount: float = 0.0
    surcharge_amount: float = 0.0
    #: IndExe: 1 exento, 2 no facturable, 6 no facturable negativo...
    exempt_indicator: int | None = None
    #: CodImpAdic: los impuestos adicionales o retenciones que afectan la línea.
    tax_codes: list[int] = field(default_factory=list)


@dataclass
class ReceivedReference:
    """Un <Referencia>: la OC (801), la guía (52) o la factura que corrige una nota."""

    #: TpoDocRef como texto: además de los tipos numéricos hay códigos como «HES».
    doc_type: str
    folio: str
    date: _dt.date | None = None
    #: CodRef: 1 anula, 2 corrige texto, 3 corrige montos. Sólo en notas.
    code: int | None = None
    reason: str = ""
    #: IndGlobal=1: la referencia abarca un conjunto de documentos, no uno.
    is_global: bool = False


@dataclass
class ReceivedAdjustment:
    """Descuento o recargo global (<DscRcgGlobal>)."""

    kind: str  # TpoMov: D descuento, R recargo
    value_type: str  # TpoValor: % o $
    value: float
    #: IndExeDR: None sobre lo afecto, 1 sobre lo exento, 2 sobre lo no facturable.
    exempt_indicator: int | None = None
    reason: str = ""


@dataclass
class ReceivedTax:
    """Impuesto adicional o retención de los totales (<ImptoReten>)."""

    code: int
    amount: int
    rate: float | None = None


@dataclass
class ReceivedDocument:
    doc_type: int
    folio: int
    issue_date: _dt.date
    issuer_rut: str
    receiver_rut: str
    total_amount: int
    issuer_name: str = ""
    due_date: _dt.date | None = None
    #: MntBruto=1: los precios de las líneas vienen con IVA incluido.
    prices_include_vat: bool = False
    net_amount: int = 0
    exempt_amount: int = 0
    vat_rate: float | None = None
    vat_amount: int = 0
    taxes: list[ReceivedTax] = field(default_factory=list)
    lines: list[ReceivedLine] = field(default_factory=list)
    references: list[ReceivedReference] = field(default_factory=list)
    adjustments: list[ReceivedAdjustment] = field(default_factory=list)


@dataclass
class Contact:
    name: str = "Contacto"
    phone: str = ""
    email: str = ""


def addressed_to(document: ReceivedDocument, responder_rut: str) -> bool:
    """¿El DTE va dirigido a quien responde?

    Un envío puede traer documentos de otro receptor. El set de intercambio de
    la certificación lo hace a propósito: de sus dos facturas, una no es para el
    postulante. Esa no se recibe, se rechaza comercialmente y no lleva recibo de
    mercaderías.
    """
    return _rut(document.receiver_rut) == _rut(responder_rut)


def _rut(value: str) -> str:
    return value.replace(".", "").strip().upper()


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
                issuer_name=_optional_text(issuer, "RznSoc") or "",
                due_date=_optional_date(_optional_text(id_doc, "FchVenc")),
                prices_include_vat=_optional_text(id_doc, "MntBruto") == "1",
                net_amount=_int(_optional_text(totals, "MntNeto")),
                exempt_amount=_int(_optional_text(totals, "MntExe")),
                vat_rate=_optional_float(_optional_text(totals, "TasaIVA")),
                vat_amount=_int(_optional_text(totals, "IVA")),
                taxes=[_received_tax(n) for n in _children(totals, "ImptoReten")],
                lines=[_received_line(n) for n in _children(doc, "Detalle")],
                references=[_received_reference(n) for n in _children(doc, "Referencia")],
                adjustments=[_received_adjustment(n) for n in _children(doc, "DscRcgGlobal")],
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
    responder_rut: str | None = None,
) -> etree._Element:
    """RespuestaDTE de acuse de recibo del envío (RecepcionEnvio).

    El estado se da por envío y por documento: un DTE dirigido a otro RUT se
    informa «DTE No Recibido - Error en RUT Receptor» (código 3), y un envío
    cuya carátula no es para quien responde, «RUT Receptor No Corresponde».
    """
    responder = responder_rut or envelope.receiver_rut
    root, result = _base_response(envelope, contact, timestamp, response_id, 1, responder)

    if _rut(envelope.receiver_rut) == _rut(responder):
        envelope_status = ENVELOPE_RECEIVED
    else:
        envelope_status = ENVELOPE_WRONG_RECEIVER
    reception = etree.SubElement(result, "{%s}RecepcionEnvio" % NS)
    _t(reception, "NmbEnvio", envelope.envelope_name)
    _t(reception, "FchRecep", _ts(timestamp))
    _t(reception, "CodEnvio", str(envelope_code))
    _t(reception, "EnvioDTEID", envelope.set_dte_id)
    if envelope.digest:
        _t(reception, "Digest", envelope.digest)
    _t(reception, "RutEmisor", envelope.issuer_rut)
    _t(reception, "RutReceptor", envelope.receiver_rut)
    _t(reception, "EstadoRecepEnv", str(envelope_status[0]))
    _t(reception, "RecepEnvGlosa", envelope_status[1])
    _t(reception, "NroDTE", str(len(envelope.documents)))
    for document in envelope.documents:
        status = DTE_RECEIVED if addressed_to(document, responder) else DTE_WRONG_RECEIVER
        reception_dte = etree.SubElement(reception, "{%s}RecepcionDTE" % NS)
        _document_data(reception_dte, document)  # RecepcionDTE NO lleva CodEnvio
        _t(reception_dte, "EstadoRecepDTE", str(status[0]))
        _t(reception_dte, "RecepDTEGlosa", status[1])

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
    responder_rut: str | None = None,
) -> etree._Element:
    """RespuestaDTE de aceptación/rechazo comercial (ResultadoDTE).

    ``accept`` decide sobre los documentos dirigidos a quien responde. Los de
    otro receptor se rechazan siempre. La glosa sigue el formato del SII:
    «ACEPTADO OK», o «RECHAZADO» con el motivo, citando la Ley 19.983.
    """
    responder = responder_rut or envelope.receiver_rut
    root, result = _base_response(
        envelope, contact, timestamp, response_id, len(envelope.documents), responder
    )

    for document in envelope.documents:
        result_dte = etree.SubElement(result, "{%s}ResultadoDTE" % NS)
        _document_data(result_dte, document, with_envelope_code=True, envelope_code=envelope_code)
        if not addressed_to(document, responder):
            _t(result_dte, "EstadoDTE", str(RESULT_REJECTED))
            motivo = f"el RUT receptor {document.receiver_rut} no corresponde a {responder}"
            _t(result_dte, "EstadoDTEGlosa", _rejection(motivo))
        elif accept:
            _t(result_dte, "EstadoDTE", str(RESULT_ACCEPTED[0]))
            _t(result_dte, "EstadoDTEGlosa", RESULT_ACCEPTED[1])
        else:
            _t(result_dte, "EstadoDTE", str(RESULT_REJECTED))
            _t(result_dte, "EstadoDTEGlosa", _rejection(rejection_label))

    return signer.sign_enveloped(root, result, cert)


def _rejection(reason: str) -> str:
    """«RECHAZADO» con el motivo y la Ley 19.983, dentro de los 256 del formato."""
    text = "RECHAZADO segun Ley 19.983"
    if reason:
        text += f": {reason}"
    return text[:256]


def _base_response(envelope, contact, timestamp, response_id, detail_count, responder_rut):
    """Crea <RespuestaDTE><Resultado><Caratula> y devuelve (root, result)."""
    contact = contact or Contact()
    root = etree.Element("{%s}RespuestaDTE" % NS, nsmap={None: NS}, version="1.0")
    result = etree.SubElement(root, "{%s}Resultado" % NS, ID="Respuesta")
    cover = etree.SubElement(result, "{%s}Caratula" % NS, version="1.0")
    _t(cover, "RutResponde", responder_rut)  # quien recibió el DTE responde
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
    responder_rut: str | None = None,
) -> etree._Element:
    """Construye y firma un EnvioRecibos (un Recibo por DTE, firma anidada).

    Sólo lleva recibo lo que efectivamente se recibió: los DTE dirigidos a quien
    responde. Recibir mercaderías de una factura que no es propia no tiene
    sentido, y el set de intercambio de la certificación trae justo esa trampa.
    """
    contact = contact or Contact()
    responder_rut = responder_rut or envelope.receiver_rut
    recipient_rut = envelope.issuer_rut
    received = [d for d in envelope.documents if addressed_to(d, responder_rut)]
    if not received:
        raise ValueError("ningún documento del envío está dirigido a quien responde")
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
    for i, document in enumerate(received, start=1):
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
    return serialize_document(element)


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


# --------------------------------------------------------------------------- #
#  Lectura tolerante del documento recibido
# --------------------------------------------------------------------------- #
# Un documento ajeno se lee sin exigir nada que el XSD no exija: un campo
# opcional que falta es un dato vacío, no un error que haga perder el correo.


def _children(parent: etree._Element, name: str) -> list[etree._Element]:
    """Hijos DIRECTOS con ese nombre local (el <Detalle> es hijo de <Documento>)."""
    return [n for n in parent if isinstance(n.tag, str) and etree.QName(n).localname == name]


def _optional_text(parent: etree._Element, name: str) -> str | None:
    for node in parent.iter():
        if node is not parent and etree.QName(node).localname == name:
            return node.text.strip() if node.text else None
    return None


def _child_text(parent: etree._Element, name: str) -> str | None:
    """Como `_optional_text`, pero sólo entre los hijos directos.

    Un <Detalle> contiene <SubDscto> y <CdgItem> con hijos propios: buscar en
    profundidad confundiría, por ejemplo, el <TpoCodigo> de un código con otro.
    """
    nodos = _children(parent, name)
    return nodos[0].text.strip() if nodos and nodos[0].text else None


def _int(value: str | None) -> int:
    return int(round(float(value))) if value else 0


def _optional_float(value: str | None) -> float | None:
    return float(value) if value else None


def _optional_date(value: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(value) if value else None


def _received_line(node: etree._Element) -> ReceivedLine:
    codes = []
    for cdg in _children(node, "CdgItem"):
        tipo, valor = _child_text(cdg, "TpoCodigo"), _child_text(cdg, "VlrCodigo")
        if valor:
            codes.append((tipo or "", valor))
    exento = _child_text(node, "IndExe")
    return ReceivedLine(
        number=_int(_child_text(node, "NroLinDet")),
        name=_child_text(node, "NmbItem") or "",
        amount=float(_child_text(node, "MontoItem") or 0),
        description=_child_text(node, "DscItem") or "",
        codes=codes,
        quantity=_optional_float(_child_text(node, "QtyItem")),
        unit=_child_text(node, "UnmdItem") or "",
        unit_price=_optional_float(_child_text(node, "PrcItem")),
        discount_amount=float(_child_text(node, "DescuentoMonto") or 0),
        surcharge_amount=float(_child_text(node, "RecargoMonto") or 0),
        exempt_indicator=int(exento) if exento else None,
        tax_codes=[int(n.text) for n in _children(node, "CodImpAdic") if n.text],
    )


def _received_reference(node: etree._Element) -> ReceivedReference:
    codigo = _child_text(node, "CodRef")
    return ReceivedReference(
        doc_type=_child_text(node, "TpoDocRef") or "",
        folio=_child_text(node, "FolioRef") or "",
        date=_optional_date(_child_text(node, "FchRef")),
        code=int(codigo) if codigo and codigo.isdigit() else None,
        reason=_child_text(node, "RazonRef") or "",
        is_global=_child_text(node, "IndGlobal") == "1",
    )


def _received_adjustment(node: etree._Element) -> ReceivedAdjustment:
    exento = _child_text(node, "IndExeDR")
    return ReceivedAdjustment(
        kind=_child_text(node, "TpoMov") or "D",
        value_type=_child_text(node, "TpoValor") or "$",
        value=float(_child_text(node, "ValorDR") or 0),
        exempt_indicator=int(exento) if exento else None,
        reason=_child_text(node, "GlosaDR") or "",
    )


def _received_tax(node: etree._Element) -> ReceivedTax:
    return ReceivedTax(
        code=_int(_child_text(node, "TipoImp")),
        amount=_int(_child_text(node, "MontoImp")),
        rate=_optional_float(_child_text(node, "TasaImp")),
    )
