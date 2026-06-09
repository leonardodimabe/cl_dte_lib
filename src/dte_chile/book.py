"""Libro de Compras y Ventas electrónico (IECV) — LibroCompraVenta.

Genera y firma el ``LibroCompraVenta`` que exige el set de certificación. Aunque
en operación el RCV (Registro de Compra y Venta) lo arma el SII automáticamente,
la certificación todavía requiere enviar este libro.

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

NS = "http://www.sii.cl/SiiDte"


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
    root = etree.Element("{%s}LibroCompraVenta" % NS, nsmap={None: NS}, version="1.0")
    book = etree.SubElement(root, "{%s}EnvioLibro" % NS, ID="LibroCV")

    _cover(book, cover)
    _summary(book, cover.lines)
    for line in cover.lines:
        _detail(book, line)
    _t(book, "TmstFirma", _ts(timestamp))

    return signer.sign_enveloped(root, book, cert)


def serialize(element: etree._Element) -> bytes:
    return etree.tostring(element, xml_declaration=True, encoding="ISO-8859-1")


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


def _summary(book: etree._Element, lines: list[BookLine]) -> None:
    summary = etree.SubElement(book, "{%s}ResumenPeriodo" % NS)
    groups: dict[int, list[BookLine]] = defaultdict(list)
    for line in lines:
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
        _t(totals, "TotMntIVA", str(sum(ln.vat_amount for ln in group)))
        _t(totals, "TotMntTotal", str(sum(ln.total_amount for ln in group)))


def _detail(book: etree._Element, line: BookLine) -> None:
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
    _t(detail, "MntTotal", str(line.total_amount))


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _t(parent: etree._Element, tag: str, value: str) -> None:
    etree.SubElement(parent, "{%s}%s" % (NS, tag)).text = value


def _ts(timestamp: _dt.datetime) -> str:
    return timestamp.replace(microsecond=0).isoformat()
