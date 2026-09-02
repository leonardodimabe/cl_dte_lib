"""Libro de Compras y Ventas electrónico (IECV) — LibroCompraVenta.

Genera y firma el ``LibroCompraVenta`` que exige el set de certificación. Aunque
en operación el RCV (Registro de Compra y Venta) lo arma el SII automáticamente,
la certificación todavía requiere enviar este libro.

El Libro de Compras añade, sobre el de ventas: IVA de **uso común** (crédito
parcial según el factor de proporcionalidad del período), IVA **no recuperable**
desglosado por motivo, e IVA **retenido total** de las facturas de compra.

Estructura:

    <LibroCompraVenta version="1.0" xmlns="http://www.sii.cl/SiiDte">
      <EnvioLibro ID="...">
        <Caratula>
          <RutEmisorLibro/> <RutEnvia/> <PeriodoTributario/>
          <FchResol/> <NroResol/> <TipoOperacion/> <TipoLibro/>
          <TipoEnvio/> <FolioNotificacion/>
        </Caratula>
        <ResumenPeriodo>
          <TotalesPeriodo>...</TotalesPeriodo>   ← uno por tipo de documento
        </ResumenPeriodo>
        <Detalle>...</Detalle>                    ← uno por documento
        <TmstFirma/>
      </EnvioLibro>
      <Signature/>
    </LibroCompraVenta>
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass, field

from lxml import etree

from . import signer
from .certificate import Certificate
from .models import DTE
from .validation import build_root, serialize_document

NS = "http://www.sii.cl/SiiDte"


# Códigos de IVA no recuperable (CodIVANoRec) del Libro de Compras.
NON_RECOVERABLE_EXEMPT_OPS = 1  # compras destinadas a operaciones no gravadas
NON_RECOVERABLE_LATE = 2  # facturas registradas fuera de plazo
NON_RECOVERABLE_REJECTED = 3  # gastos rechazados
NON_RECOVERABLE_FREE_DELIVERY = 4  # entrega gratuita del proveedor
NON_RECOVERABLE_OTHER = 9


@dataclass
class NonRecoverableVat:
    """IVA que no da derecho a crédito fiscal (<IVANoRec>)."""

    code: int
    amount: int


@dataclass
class BookLine:
    """Una línea (documento) del libro."""

    doc_type: int
    folio: int
    date: _dt.date
    rut: str  # contraparte: cliente (ventas) o proveedor (compras)
    business_name: str
    exempt_amount: int = 0
    net_amount: int = 0
    vat_amount: int = 0
    total_amount: int = 0
    vat_rate: int = 19
    voided: bool = False
    # --- Sólo Libro de Compras ---
    # IVA de compras de uso común (afectas y exentas a la vez): da crédito
    # parcial según el factor de proporcionalidad del período.
    common_use_vat: int = 0
    # IVA sin derecho a crédito, desglosado por motivo.
    non_recoverable_vat: list[NonRecoverableVat] = field(default_factory=list)
    # IVA retenido por el comprador (factura de compra con cambio de sujeto).
    retained_total_vat: int = 0
    # Monto no facturable del período (LV): p.ej. depósitos por envase. Entra en
    # MntPeriodo, que es lo que el libro de ventas cuadra.
    non_billable_amount: int = 0

    @property
    def has_recoverable_vat(self) -> bool:
        """True si la línea aporta IVA con derecho a crédito directo."""
        return bool(self.vat_amount) and not self.common_use_vat


@dataclass
class BookCover:
    issuer_rut: str
    sender_rut: str
    period: str  # "AAAA-MM", p.ej. "2026-06"
    operation_type: str = "VENTA"  # "VENTA" | "COMPRA"
    resolution_date: _dt.date = _dt.date(2026, 1, 1)
    resolution_number: int = 0  # 0 en certificación
    book_type: str = "MENSUAL"  # MENSUAL | ESPECIAL | RECTIFICA | ...
    submission_type: str = "TOTAL"  # TOTAL | AJUSTE | PARCIAL
    notification_folio: int = 1
    # Factor de proporcionalidad del IVA de uso común (0..1). Sólo Libro de
    # Compras; el set de certificación lo fija en 0,60.
    proportionality_factor: float | None = None
    lines: list[BookLine] = field(default_factory=list)


def sales_line(dte: DTE) -> BookLine:
    """Crea una línea de Libro de Ventas a partir de un DTE (contraparte=receptor)."""
    return BookLine(
        doc_type=int(dte.type),
        folio=dte.folio,
        date=dte.issue_date,
        rut=dte.receiver.rut.value,
        business_name=dte.receiver.business_name,
        exempt_amount=dte.exempt_amount,
        net_amount=dte.net_amount,
        vat_amount=dte.vat,
        total_amount=dte.total_amount,
    )


def build_book(cover: BookCover, cert: Certificate, timestamp: _dt.datetime) -> etree._Element:
    """Construye y firma el LibroCompraVenta."""
    root = build_root("LibroCompraVenta", version="1.0")
    book = etree.SubElement(root, "{%s}EnvioLibro" % NS, ID="LibroCV")

    _cover(book, cover)
    _summary(book, cover)
    for line in cover.lines:
        _detail(book, line, cover.operation_type)
    _t(book, "TmstFirma", _ts(timestamp))

    return signer.sign_enveloped(root, book, cert)


def serialize(element: etree._Element) -> bytes:
    return serialize_document(element)


# --------------------------------------------------------------------------- #
#  Construcción
# --------------------------------------------------------------------------- #
def _cover(book: etree._Element, cover: BookCover) -> None:
    cover_node = etree.SubElement(book, "{%s}Caratula" % NS)
    _t(cover_node, "RutEmisorLibro", cover.issuer_rut)
    _t(cover_node, "RutEnvia", cover.sender_rut)
    _t(cover_node, "PeriodoTributario", cover.period)
    _t(cover_node, "FchResol", cover.resolution_date.isoformat())
    _t(cover_node, "NroResol", str(cover.resolution_number))
    _t(cover_node, "TipoOperacion", cover.operation_type)
    _t(cover_node, "TipoLibro", cover.book_type)
    _t(cover_node, "TipoEnvio", cover.submission_type)
    _t(cover_node, "FolioNotificacion", str(cover.notification_folio))


def _summary(book: etree._Element, cover: BookCover) -> None:
    """Resumen por tipo de documento, en el orden que exige LibroCV_v10.xsd."""
    summary = etree.SubElement(book, "{%s}ResumenPeriodo" % NS)
    groups: dict[int, list[BookLine]] = defaultdict(list)
    for line in cover.lines:
        groups[line.doc_type].append(line)

    for doc_type, group in sorted(groups.items()):
        totals = etree.SubElement(summary, "{%s}TotalesPeriodo" % NS)
        _t(totals, "TpoDoc", str(doc_type))
        _t(totals, "TotDoc", str(len(group)))
        voided_count = sum(1 for ln in group if ln.voided)
        if voided_count:
            _t(totals, "TotAnulado", str(voided_count))
        _t(totals, "TotMntExe", str(sum(ln.exempt_amount for ln in group)))
        _t(totals, "TotMntNeto", str(sum(ln.net_amount for ln in group)))

        # El XSD anota cada total con el libro al que pertenece: TotOpIVARec,
        # el IVA no recuperable y el de uso común son (LC), y el IVA retenido
        # es (LV). Cruzarlos deja el libro descuadrado para el SII.
        compra = cover.operation_type == "COMPRA"
        if compra:
            recoverable = sum(1 for ln in group if ln.has_recoverable_vat)
            if recoverable:
                _t(totals, "TotOpIVARec", str(recoverable))
        _t(totals, "TotMntIVA", str(sum(ln.vat_amount for ln in group)))

        if compra:
            _non_recoverable_totals(totals, group)
            _common_use_totals(totals, group, cover.proportionality_factor)
        else:
            _retained_totals(totals, group)

        total = sum(ln.total_amount for ln in group)
        _t(totals, "TotMntTotal", str(total))
        if not compra:
            non_billable = sum(ln.non_billable_amount for ln in group)
            if non_billable:
                _t(totals, "TotMntNoFact", str(non_billable))
            _t(totals, "TotMntPeriodo", str(total + non_billable))


def _non_recoverable_totals(totals: etree._Element, group: list[BookLine]) -> None:
    """<TotIVANoRec>: una entrada por motivo (CodIVANoRec)."""
    by_code: dict[int, list[int]] = defaultdict(list)
    for line in group:
        for entry in line.non_recoverable_vat:
            by_code[entry.code].append(entry.amount)
    for code, amounts in sorted(by_code.items()):
        node = etree.SubElement(totals, "{%s}TotIVANoRec" % NS)
        _t(node, "CodIVANoRec", str(code))
        _t(node, "TotOpIVANoRec", str(len(amounts)))
        _t(node, "TotMntIVANoRec", str(sum(amounts)))


def _common_use_totals(totals: etree._Element, group: list[BookLine], factor: float | None) -> None:
    """IVA de uso común: se informa el total y el crédito que da el factor."""
    common = [ln for ln in group if ln.common_use_vat]
    if not common:
        return
    amount = sum(ln.common_use_vat for ln in common)
    _t(totals, "TotOpIVAUsoComun", str(len(common)))
    _t(totals, "TotIVAUsoComun", str(amount))
    if factor is not None:
        _t(totals, "FctProp", _factor(factor))
        _t(totals, "TotCredIVAUsoComun", str(round(amount * factor)))


def _retained_totals(totals: etree._Element, group: list[BookLine]) -> None:
    retained = [ln for ln in group if ln.retained_total_vat]
    if not retained:
        return
    _t(totals, "TotOpIVARetTotal", str(len(retained)))
    _t(totals, "TotIVARetTotal", str(sum(ln.retained_total_vat for ln in retained)))


def _detail(book: etree._Element, line: BookLine, operation_type: str = "VENTA") -> None:
    # Orden según LibroCV_v10.xsd: TpoDoc, NroDoc, Anulado?, TasaImp?, FchDoc,
    # CdgSIISucur?, RUTDoc, RznSoc?, ... montos ..., MntTotal.
    detail = etree.SubElement(book, "{%s}Detalle" % NS)
    _t(detail, "TpoDoc", str(line.doc_type))
    _t(detail, "NroDoc", str(line.folio))
    if line.voided:
        _t(detail, "Anulado", "A")
    if line.vat_amount:
        _t(detail, "TasaImp", str(line.vat_rate))
    _t(detail, "FchDoc", line.date.isoformat())
    _t(detail, "RUTDoc", line.rut)
    if line.business_name:
        _t(detail, "RznSoc", line.business_name[:50])
    if line.exempt_amount:
        _t(detail, "MntExe", str(line.exempt_amount))
    if line.net_amount:
        _t(detail, "MntNeto", str(line.net_amount))
    if line.vat_amount:
        _t(detail, "MntIVA", str(line.vat_amount))
    # Orden del XSD: ... MntIVA, MntActivoFijo?, MntIVAActivoFijo?, IVANoRec*,
    # IVAUsoComun?, ..., IVARetTotal?, ..., MntTotal.
    # Mismo criterio que en el resumen: IVANoRec e IVAUsoComun son campos del
    # libro de compras; IVARetTotal, del de ventas.
    if operation_type == "COMPRA":
        for entry in line.non_recoverable_vat:
            node = etree.SubElement(detail, "{%s}IVANoRec" % NS)
            _t(node, "CodIVANoRec", str(entry.code))
            _t(node, "MntIVANoRec", str(entry.amount))
        if line.common_use_vat:
            _t(detail, "IVAUsoComun", str(line.common_use_vat))
    elif line.retained_total_vat:
        _t(detail, "IVARetTotal", str(line.retained_total_vat))
    _t(detail, "MntTotal", str(line.total_amount))
    # MntNoFact y MntPeriodo son (LV): el libro de ventas cuadra por el monto
    # del período, no por el total, así que se emiten siempre en ese libro.
    if operation_type == "VENTA":
        if line.non_billable_amount:
            _t(detail, "MntNoFact", str(line.non_billable_amount))
        _t(detail, "MntPeriodo", str(line.total_amount + line.non_billable_amount))


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _t(parent: etree._Element, tag: str, value: str) -> None:
    etree.SubElement(parent, "{%s}%s" % (NS, tag)).text = value


def _factor(value: float) -> str:
    """FctProp con dos decimales y punto (0.6 → "0.60")."""
    return f"{value:.2f}"


def _ts(timestamp: _dt.datetime) -> str:
    return timestamp.replace(microsecond=0).isoformat()
