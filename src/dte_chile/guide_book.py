"""Libro de Guías de Despacho Electrónica (``LibroGuia``).

Cumple dos funciones distintas:

1. **Certificación:** el SET LIBRO DE GUIAS lo exige para certificar el tipo 52.
2. **Operación:** la Res. Ex. N°154 (2025) dispone que el SII tendrá un Registro
   de Guías de Despacho, pero mientras ese registro no entre en marcha —lo que
   se formalizará en una resolución posterior— los emisores con sistema propio o
   de mercado deben registrar aquí sus guías **vigentes y anuladas**, mantener el
   libro actualizado en la casa matriz o sucursal y enviarlo al Servicio cuando
   éste lo requiera para fiscalización.

Estructura (``LibroGuia_v10.xsd``)::

    <LibroGuia version="1.0" xmlns="http://www.sii.cl/SiiDte">
      <EnvioLibro ID="...">
        <Caratula>
          <RutEmisorLibro/> <RutEnvia/> <PeriodoTributario/>
          <FchResol/> <NroResol/> <TipoLibro/> <TipoEnvio/>
          <FolioNotificacion/>
        </Caratula>
        <ResumenPeriodo>
          <TotFolAnulado/> <TotGuiaAnulada/> <TotGuiaVenta/> <TotMntGuiaVta/>
          <TotMntModificado/> <TotTraslado/>*   ← uno por tipo de traslado
        </ResumenPeriodo>
        <Detalle/>*                             ← una por guía
        <TmstFirma/>
      </EnvioLibro>
      <Signature/>
    </LibroGuia>

Ojo con la asimetría del resumen: las guías **de venta** (TpoOper=1) se cuentan
en ``TotGuiaVenta``/``TotMntGuiaVta``, y las que **no** constituyen venta van
desagregadas por tipo en los bloques ``TotTraslado`` (que sólo admiten códigos
2 a 9).
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum

from lxml import etree

from . import signer
from .certificate import Certificate
from .document_types import TransferType
from .models import DTE
from .validation import build_root, serialize_document

NS = "http://www.sii.cl/SiiDte"

# El XSD sólo acepta hasta 6 bloques TotTraslado en el resumen del período.
MAX_TRANSFER_GROUPS = 6


class VoidStatus(IntEnum):
    """Campo ``Anulado`` del detalle.

    El SII separa los dos primeros en el resumen: el 1 suma en ``TotFolAnulado``
    (el folio nunca llegó al Servicio) y el 2 en ``TotGuiaAnulada``.
    """

    BEFORE_SENDING = 1  # anulado previo a su envío al SII
    AFTER_SENDING = 2  # anulado posterior a su envío al SII
    PARTIAL_RECEIPT = 3  # productos recibidos parcialmente


@dataclass
class GuideBookLine:
    """Una guía de despacho dentro del libro."""

    folio: int
    date: _dt.date | None = None
    transfer_type: TransferType | None = None  # TpoOper
    receiver_rut: str = ""
    receiver_name: str = ""
    net_amount: int = 0
    vat_amount: int = 0
    total_amount: int = 0
    vat_rate: int = 19
    voided: VoidStatus | None = None  # Anulado
    # Guía que se facturó en el período: el monto se informa como modificado y
    # se referencia la factura que la absorbió.
    modified_amount: int | None = None  # MntModificado
    ref_doc_type: int | None = None  # TpoDocRef
    ref_folio: int | None = None  # FolioDocRef
    ref_date: _dt.date | None = None  # FchDocRef

    @property
    def is_sale(self) -> bool:
        return self.transfer_type is TransferType.SALE


@dataclass
class GuideBookCover:
    issuer_rut: str
    sender_rut: str  # RUT del titular del certificado que envía
    period: str  # "AAAA-MM", p.ej. "2026-11"
    resolution_date: _dt.date = _dt.date(2026, 1, 1)
    resolution_number: int = 0  # 0 en certificación
    book_type: str = "ESPECIAL"  # el XSD sólo admite ESPECIAL
    submission_type: str = "TOTAL"  # TOTAL | PARCIAL | FINAL | AJUSTE
    # Folio de la notificación con que se solicita el libro. En certificación se
    # usa el número de atención del set. El XSD exige un entero positivo.
    notification_folio: int = 1
    lines: list[GuideBookLine] = field(default_factory=list)


def guide_line(dte: DTE) -> GuideBookLine:
    """Crea la línea del libro a partir de una guía de despacho ya emitida."""
    if not dte.type.is_dispatch_note:
        raise ValueError(f"El tipo {int(dte.type)} no es una guía de despacho.")
    return GuideBookLine(
        folio=dte.folio,
        date=dte.issue_date,
        transfer_type=dte.transfer_type,
        receiver_rut=dte.receiver.rut.value,
        receiver_name=dte.receiver.business_name,
        net_amount=dte.net_amount,
        vat_amount=dte.vat,
        total_amount=dte.total_amount,
    )


def build_guide_book(
    cover: GuideBookCover, cert: Certificate, timestamp: _dt.datetime
) -> etree._Element:
    """Construye y firma el ``LibroGuia``."""
    root = build_root("LibroGuia", version="1.0")
    book = etree.SubElement(root, "{%s}EnvioLibro" % NS, ID="LibroGuia")

    _cover(book, cover)
    _summary(book, cover.lines)
    for line in cover.lines:
        _detail(book, line)
    _t(book, "TmstFirma", _ts(timestamp))

    return signer.sign_enveloped(root, book, cert)


def serialize(element: etree._Element) -> bytes:
    return serialize_document(element)


# --------------------------------------------------------------------------- #
#  Construcción
# --------------------------------------------------------------------------- #
def _cover(book: etree._Element, cover: GuideBookCover) -> None:
    cover_node = etree.SubElement(book, "{%s}Caratula" % NS)
    _t(cover_node, "RutEmisorLibro", cover.issuer_rut)
    _t(cover_node, "RutEnvia", cover.sender_rut)
    _t(cover_node, "PeriodoTributario", cover.period)
    _t(cover_node, "FchResol", cover.resolution_date.isoformat())
    _t(cover_node, "NroResol", str(cover.resolution_number))
    _t(cover_node, "TipoLibro", cover.book_type)
    _t(cover_node, "TipoEnvio", cover.submission_type)
    _t(cover_node, "FolioNotificacion", str(cover.notification_folio))


def _summary(book: etree._Element, lines: list[GuideBookLine]) -> None:
    summary = etree.SubElement(book, "{%s}ResumenPeriodo" % NS)

    # Una guía anulada no aporta a los totales de venta ni de traslado: sólo se
    # cuenta como anulada.
    live = [ln for ln in lines if ln.voided is None]

    void_before = sum(1 for ln in lines if ln.voided is VoidStatus.BEFORE_SENDING)
    void_after = sum(1 for ln in lines if ln.voided is VoidStatus.AFTER_SENDING)
    if void_before:
        _t(summary, "TotFolAnulado", str(void_before))
    if void_after:
        _t(summary, "TotGuiaAnulada", str(void_after))

    sales = [ln for ln in live if ln.is_sale]
    _t(summary, "TotGuiaVenta", str(len(sales)))
    _t(summary, "TotMntGuiaVta", str(sum(ln.total_amount for ln in sales)))

    modified = sum(ln.modified_amount or 0 for ln in lines)
    if modified:
        _t(summary, "TotMntModificado", str(modified))

    # Los traslados que no son venta se agrupan por tipo (códigos 2 a 9).
    groups: dict[int, list[GuideBookLine]] = defaultdict(list)
    for line in live:
        if line.transfer_type is not None and not line.is_sale:
            groups[int(line.transfer_type)].append(line)
    if len(groups) > MAX_TRANSFER_GROUPS:
        raise ValueError(
            f"El libro trae {len(groups)} tipos de traslado distintos y el XSD "
            f"admite {MAX_TRANSFER_GROUPS}."
        )
    for transfer_type, group in sorted(groups.items()):
        totals = etree.SubElement(summary, "{%s}TotTraslado" % NS)
        _t(totals, "TpoTraslado", str(transfer_type))
        _t(totals, "CantGuia", str(len(group)))
        amount = sum(ln.total_amount for ln in group)
        if amount:
            _t(totals, "MntGuia", str(amount))


def _detail(book: etree._Element, line: GuideBookLine) -> None:
    detail = etree.SubElement(book, "{%s}Detalle" % NS)
    _t(detail, "Folio", str(line.folio))

    # De una guía anulada sólo se informa el folio y la anulación: sus montos
    # ya no representan una operación.
    if line.voided is not None:
        _t(detail, "Anulado", str(int(line.voided)))
        return

    if line.transfer_type is not None:
        _t(detail, "TpoOper", str(int(line.transfer_type)))
    if line.date is not None:
        _t(detail, "FchDoc", line.date.isoformat())
    if line.receiver_rut:
        _t(detail, "RUTDoc", line.receiver_rut)
    if line.receiver_name:
        _t(detail, "RznSoc", line.receiver_name[:50])
    if line.net_amount:
        _t(detail, "MntNeto", str(line.net_amount))
        _t(detail, "TasaImp", str(line.vat_rate))
    if line.vat_amount:
        _t(detail, "IVA", str(line.vat_amount))
    _t(detail, "MntTotal", str(line.total_amount))
    if line.modified_amount is not None:
        _t(detail, "MntModificado", str(line.modified_amount))
    if line.ref_doc_type is not None:
        _t(detail, "TpoDocRef", str(line.ref_doc_type))
    if line.ref_folio is not None:
        _t(detail, "FolioDocRef", str(line.ref_folio))
    if line.ref_date is not None:
        _t(detail, "FchDocRef", line.ref_date.isoformat())


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _t(parent: etree._Element, tag: str, value: str) -> None:
    etree.SubElement(parent, "{%s}%s" % (NS, tag)).text = value


def _ts(timestamp: _dt.datetime) -> str:
    return timestamp.replace(microsecond=0).isoformat()
