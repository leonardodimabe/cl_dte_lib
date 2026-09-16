"""Boleta electrónica (39 afecta / 41 exenta) y su sobre ``EnvioBOLETA``.

La boleta NO es un DTE más: tiene su propio esquema (``EnvioBOLETA_v11.xsd``),
su propio sobre y su propio canal de envío al SII. Las diferencias que importan
frente al ``EnvioDTE``:

- El emisor usa otros nombres de etiqueta (``RznSocEmisor``/``GiroEmisor``).
- ``IdDoc`` exige ``IndServicio``; para la boleta de ventas y servicios es 3.
- ``Totales`` no lleva ``TasaIVA``: sólo neto, exento, IVA y total.
- Los precios de las líneas van **con IVA incluido**, así que el neto se despeja
  del bruto. Si se informaran netos habría que declarar ``IndMntNeto`` = 2.
- No hay sección de transporte ni bloque de retenciones.
- El sobre admite hasta 500 boletas y sólo 2 ``SubTotDTE`` (39 y 41).

El detalle del período se reporta aparte, en el consumo de folios (RCOF); ver
:mod:`dte_chile.folio_report`.
"""

from __future__ import annotations

import copy
import datetime as _dt
from collections import Counter
from dataclasses import dataclass, field

from lxml import etree

from . import signer
from .caf import CAF
from .certificate import Certificate
from .document_types import ServiceIndicator
from .envelope import serialize as serialize_envelope
from .models import DTE
from .ted import build_ted
from .validation import build_root

NS = "http://www.sii.cl/SiiDte"

# RUT del SII como receptor del envío (igual que en el EnvioDTE).
SII_RECEIVER_RUT = "60803000-K"

# RUT genérico del consumidor final: la boleta no identifica al comprador.
ANONYMOUS_RECEIVER_RUT = "66666666-6"
# Y su razón social, que no puede ir vacía (ver `_with_receiver_name`).
ANONYMOUS_RECEIVER_NAME = "Consumidor Final"

# El XSD sólo admite 2 bloques SubTotDTE (los tipos 39 y 41) y 500 boletas.
MAX_RECEIPTS = 500


@dataclass
class ReceiptCover:
    """Carátula del ``EnvioBOLETA``."""

    issuer_rut: str
    sender_rut: str  # RUT del titular del certificado que envía
    resolution_date: _dt.date
    resolution_number: int = 0
    receiver_rut: str = SII_RECEIVER_RUT
    subtotals: list[tuple[int, int]] = field(default_factory=list)


def build_receipt(dte: DTE, caf: CAF, timestamp: _dt.datetime) -> etree._Element:
    """Devuelve el ``<Documento>`` de una boleta, listo para firmar."""
    if not dte.type.is_receipt:
        raise ValueError(f"El tipo {int(dte.type)} no es una boleta (39 o 41).")
    dte.validate()
    dte = _with_receiver_name(dte)

    document = etree.Element("Documento", ID=f"F{dte.folio}T{int(dte.type)}")
    _header(document, dte)
    _items(document, dte)
    _references(document, dte)

    document.append(build_ted(dte, caf, timestamp))
    etree.SubElement(document, "TmstFirma").text = timestamp.replace(microsecond=0).isoformat()
    return document


def _with_receiver_name(dte: DTE) -> DTE:
    """La boleta sin comprador identificado lleva «Consumidor Final» como razón social.

    Con la razón social vacía, el <RSR> del timbre queda vacío, y el SII puso
    reparo «Firma Timbre Electrónico Incorrecta» a las cinco boletas del set dos
    veces seguidas: firmado como ``<RSR></RSR>`` (TrackID 32169796) y como
    ``<RSR/>`` (TrackID 32169821), aunque en el segundo el timbre verificaba
    sobre los bytes enviados.

    Las dos implementaciones certificadas de referencia nunca lo dejan vacío. El
    facturador de Odoo (``l10n_cl_edi``) emite la boleta a nombre del contacto
    «Consumidor Final Anónimo», RUT 66666666-6, y de ahí saca ``RznSocRecep`` y
    ``RSR``. dte-sii (devlas-cl) usa «Consumidor Final». Se toma la forma sin
    tilde: dte-sii documenta que el lector PDF417 del SII no devuelve bien los
    bytes acentuados del timbre.

    Se copia el documento: el que llega no se modifica.
    """
    if dte.receiver.business_name:
        return dte
    receiver = copy.copy(dte.receiver)
    receiver.business_name = ANONYMOUS_RECEIVER_NAME
    named = copy.copy(dte)
    named.receiver = receiver
    return named


def build_receipt_envelope(
    signed_receipts: list[etree._Element],
    cover: ReceiptCover,
    cert: Certificate,
    timestamp: _dt.datetime,
) -> etree._Element:
    """Arma y firma el ``<EnvioBOLETA>`` con las boletas ya firmadas."""
    if len(signed_receipts) > MAX_RECEIPTS:
        raise ValueError(
            f"El sobre admite {MAX_RECEIPTS} boletas y se pasaron {len(signed_receipts)}."
        )

    envelope = build_root("EnvioBOLETA", version="1.0")
    set_dte = etree.SubElement(envelope, "{%s}SetDTE" % NS, ID="SetDoc")
    _cover(set_dte, cover, timestamp)
    for receipt in signed_receipts:
        set_dte.append(receipt)

    # Igual que en el EnvioDTE: normalizar namespaces antes de firmar, para que
    # el árbol firmado sea idéntico al que se transmite.
    envelope = etree.fromstring(etree.tostring(envelope))
    set_dte = envelope.find("{%s}SetDTE" % NS)
    return signer.sign_set_dte(envelope, set_dte, cert)


def subtotals_for(documents: list[DTE]) -> list[tuple[int, int]]:
    """Cuenta las boletas por tipo, para los ``SubTotDTE`` de la carátula."""
    return sorted(Counter(int(d.type) for d in documents).items())


def serialize(envelope: etree._Element) -> bytes:
    """Igual que el ``EnvioDTE``: cada ``<DTE>`` sale con su propio ``xmlns``.

    Con el serializador genérico lxml quitaba la declaración por redundante y el
    SII rechazó las cinco boletas del set con «Firma DTE Incorrecta»: corta cada
    <DTE> del texto y lo verifica suelto. Ver :func:`dte_chile.envelope.serialize`.
    """
    return serialize_envelope(envelope)


# --------------------------------------------------------------------------- #
#  Documento
# --------------------------------------------------------------------------- #
def _header(document: etree._Element, dte: DTE) -> None:
    header = etree.SubElement(document, "Encabezado")

    id_doc = etree.SubElement(header, "IdDoc")
    _t(id_doc, "TipoDTE", str(int(dte.type)))
    _t(id_doc, "Folio", str(dte.folio))
    _t(id_doc, "FchEmis", dte.issue_date.isoformat())
    indicator = dte.service_indicator or ServiceIndicator.SALES_AND_SERVICE
    _t(id_doc, "IndServicio", str(int(indicator)))
    if not dte.prices_include_vat:
        # Sólo se declara cuando las líneas van netas; con precios brutos se omite.
        _t(id_doc, "IndMntNeto", "2")

    issuer = etree.SubElement(header, "Emisor")
    _t(issuer, "RUTEmisor", dte.issuer.rut.value)
    _t(issuer, "RznSocEmisor", dte.issuer.business_name[:100])
    _t(issuer, "GiroEmisor", dte.issuer.activity[:80])
    if dte.issuer.branch_code is not None:
        _t(issuer, "CdgSIISucur", str(dte.issuer.branch_code))
    if dte.issuer.address:
        _t(issuer, "DirOrigen", dte.issuer.address[:70])
    if dte.issuer.commune:
        _t(issuer, "CmnaOrigen", dte.issuer.commune)
    if dte.issuer.city:
        _t(issuer, "CiudadOrigen", dte.issuer.city)

    receiver = etree.SubElement(header, "Receptor")
    _t(receiver, "RUTRecep", dte.receiver.rut.value)
    if dte.receiver.business_name:
        _t(receiver, "RznSocRecep", dte.receiver.business_name[:100])
    if dte.receiver.address:
        _t(receiver, "DirRecep", dte.receiver.address[:70])
    if dte.receiver.commune:
        _t(receiver, "CmnaRecep", dte.receiver.commune)
    if dte.receiver.city:
        _t(receiver, "CiudadRecep", dte.receiver.city)

    # Orden del XSD: MntNeto?, MntExe?, IVA?, MntTotal. Sin TasaIVA.
    totals = etree.SubElement(header, "Totales")
    if dte.net_amount:
        _t(totals, "MntNeto", str(dte.net_amount))
    if dte.exempt_amount:
        _t(totals, "MntExe", str(dte.exempt_amount))
    if dte.vat:
        _t(totals, "IVA", str(dte.vat))
    _t(totals, "MntTotal", str(dte.total_amount))


def _items(document: etree._Element, dte: DTE) -> None:
    for position, item in enumerate(dte.items, start=1):
        detail = etree.SubElement(document, "Detalle")
        _t(detail, "NroLinDet", str(position))
        if item.exempt and not dte.type.is_exempt:
            _t(detail, "IndExe", "1")
        _t(detail, "NmbItem", item.name[:80])
        if item.description:
            _t(detail, "DscItem", item.description[:1000])
        if item.quantity:
            _t(detail, "QtyItem", _num(item.quantity))
        if item.unit:
            _t(detail, "UnmdItem", item.unit[:4])
        if item.unit_price:
            _t(detail, "PrcItem", str(item.unit_price))
        if item.discount_pct:
            _t(detail, "DescuentoPct", _num(item.discount_pct))
        if item.discount:
            _t(detail, "DescuentoMonto", str(item.discount))
        _t(detail, "MontoItem", str(item.amount))


def _references(document: etree._Element, dte: DTE) -> None:
    """Referencias. En certificación identifican el caso del set.

    En la boleta los campos no significan lo mismo que en la factura. El formato
    de boletas (v2.22) define ``TpoDocRef`` para referenciar un documento
    tributario —«se debe utilizar un valor Numérico» (39, 41, 50, 52…)— y
    ``CodRef`` como «Código alfanumérico establecido por la Empresa», de hasta 18
    caracteres. El set de boletas del SII pide justamente ese:

        <CodRef> SET
        <RazonRef> CASO-1

    Así que ``code`` acepta texto, y una referencia de caso no lleva TpoDocRef.
    """
    for position, reference in enumerate(dte.references, start=1):
        node = etree.SubElement(document, "Referencia")
        _t(node, "NroLinRef", str(position))
        if reference.doc_type:
            _t(node, "TpoDocRef", str(reference.doc_type))
        if reference.folio:
            _t(node, "FolioRef", str(reference.folio))
        if reference.code is not None:
            codigo = reference.code if isinstance(reference.code, str) else str(int(reference.code))
            _t(node, "CodRef", codigo[:18])
        if reference.reason:
            _t(node, "RazonRef", reference.reason[:90])


def _cover(set_dte: etree._Element, cover: ReceiptCover, timestamp: _dt.datetime) -> None:
    node = etree.SubElement(set_dte, "{%s}Caratula" % NS, version="1.0")
    _ns(node, "RutEmisor", cover.issuer_rut)
    _ns(node, "RutEnvia", cover.sender_rut)
    _ns(node, "RutReceptor", cover.receiver_rut)
    _ns(node, "FchResol", cover.resolution_date.isoformat())
    _ns(node, "NroResol", str(cover.resolution_number))
    _ns(node, "TmstFirmaEnv", timestamp.replace(microsecond=0).isoformat())
    for doc_type, count in cover.subtotals:
        subtotal = etree.SubElement(node, "{%s}SubTotDTE" % NS)
        _ns(subtotal, "TpoDTE", str(doc_type))
        _ns(subtotal, "NroDTE", str(count))


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _t(parent: etree._Element, tag: str, value: str) -> None:
    etree.SubElement(parent, tag).text = value


def _ns(parent: etree._Element, tag: str, value: str) -> None:
    etree.SubElement(parent, "{%s}%s" % (NS, tag)).text = value


def _num(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")
