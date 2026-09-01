"""Reporte de Consumo de Folios (RCOF / Resumen de Ventas Diarias).

Quien emite boletas electrónicas debe reportar al SII, por día, cuántos folios
usó y por cuánto: el detalle de las boletas no viaja documento a documento como
en el DTE, sino agregado en este resumen (``ConsumoFolio_v10.xsd``).

Estructura::

    <ConsumoFolios version="1.0" xmlns="http://www.sii.cl/SiiDte">
      <DocumentoConsumoFolios ID="...">
        <Caratula>
          <RutEmisor/> <RutEnvia/> <FchResol/> <NroResol/>
          <FchInicio/> <FchFinal/> <Correlativo?/> <SecEnvio/> <TmstFirmaEnv/>
        </Caratula>
        <Resumen>...</Resumen>*      ← uno por tipo de documento
      </DocumentoConsumoFolios>
      <Signature/>
    </ConsumoFolios>

Los rangos de folios se informan como intervalos consecutivos: ``ranges_of``
comprime una lista de folios sueltos en los tramos que el SII espera.
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


@dataclass
class ReportLine:
    """Un folio consumido en el período, con su desglose."""

    doc_type: int
    folio: int
    net_amount: int = 0
    vat_amount: int = 0
    exempt_amount: int = 0
    total_amount: int = 0
    vat_rate: int = 19
    voided: bool = False  # folio anulado: cuenta, pero no suma montos


@dataclass
class FolioReportCover:
    issuer_rut: str
    sender_rut: str  # RUT del titular del certificado que envía
    start_date: _dt.date  # FchInicio
    end_date: _dt.date  # FchFinal
    sequence: int  # SecEnvio: número de envío del día
    resolution_date: _dt.date = _dt.date(2026, 1, 1)
    resolution_number: int = 0
    correlative: int | None = None  # Correlativo, si el SII lo pide
    lines: list[ReportLine] = field(default_factory=list)


def report_line(dte: DTE, *, voided: bool = False) -> ReportLine:
    """Crea la línea del reporte a partir de una boleta ya emitida."""
    return ReportLine(
        doc_type=int(dte.type),
        folio=dte.folio,
        net_amount=dte.net_amount,
        vat_amount=dte.vat,
        exempt_amount=dte.exempt_amount,
        total_amount=dte.total_amount,
        voided=voided,
    )


def ranges_of(folios: list[int]) -> list[tuple[int, int]]:
    """Comprime folios sueltos en tramos consecutivos: [1,2,3,7,8] → [(1,3),(7,8)]."""
    if not folios:
        return []
    ordered = sorted(set(folios))
    ranges: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for folio in ordered[1:]:
        if folio == previous + 1:
            previous = folio
            continue
        ranges.append((start, previous))
        start = previous = folio
    ranges.append((start, previous))
    return ranges


def build_folio_report(
    cover: FolioReportCover, cert: Certificate, timestamp: _dt.datetime
) -> etree._Element:
    """Construye y firma el ``ConsumoFolios``."""
    if cover.end_date < cover.start_date:
        raise ValueError("El período del reporte termina antes de empezar.")

    root = build_root("ConsumoFolios", version="1.0")
    document = etree.SubElement(root, "{%s}DocumentoConsumoFolios" % NS, ID="ConsumoFolios")
    _cover(document, cover, timestamp)
    _summaries(document, cover.lines)
    return signer.sign_enveloped(root, document, cert)


def serialize(element: etree._Element) -> bytes:
    return serialize_document(element)


# --------------------------------------------------------------------------- #
#  Construcción
# --------------------------------------------------------------------------- #
def _cover(document: etree._Element, cover: FolioReportCover, ts: _dt.datetime) -> None:
    node = etree.SubElement(document, "{%s}Caratula" % NS, version="1.0")
    _t(node, "RutEmisor", cover.issuer_rut)
    _t(node, "RutEnvia", cover.sender_rut)
    _t(node, "FchResol", cover.resolution_date.isoformat())
    _t(node, "NroResol", str(cover.resolution_number))
    _t(node, "FchInicio", cover.start_date.isoformat())
    _t(node, "FchFinal", cover.end_date.isoformat())
    if cover.correlative is not None:
        _t(node, "Correlativo", str(cover.correlative))
    _t(node, "SecEnvio", str(cover.sequence))
    _t(node, "TmstFirmaEnv", ts.replace(microsecond=0).isoformat())


def _summaries(document: etree._Element, lines: list[ReportLine]) -> None:
    groups: dict[int, list[ReportLine]] = defaultdict(list)
    for line in lines:
        groups[line.doc_type].append(line)

    for doc_type, group in sorted(groups.items()):
        used = [ln for ln in group if not ln.voided]
        voided = [ln for ln in group if ln.voided]

        node = etree.SubElement(document, "{%s}Resumen" % NS)
        _t(node, "TipoDocumento", str(doc_type))

        # Los montos son sólo de los folios efectivamente usados.
        net = sum(ln.net_amount for ln in used)
        vat = sum(ln.vat_amount for ln in used)
        exempt = sum(ln.exempt_amount for ln in used)
        if net:
            _t(node, "MntNeto", str(net))
        if vat:
            _t(node, "MntIva", str(vat))
            _t(node, "TasaIVA", str(group[0].vat_rate))
        if exempt:
            _t(node, "MntExento", str(exempt))
        _t(node, "MntTotal", str(sum(ln.total_amount for ln in used)))

        # FoliosEmitidos = usados; FoliosUtilizados = emitidos + anulados.
        _t(node, "FoliosEmitidos", str(len(used)))
        _t(node, "FoliosAnulados", str(len(voided)))
        _t(node, "FoliosUtilizados", str(len(group)))

        for start, end in ranges_of([ln.folio for ln in used]):
            block = etree.SubElement(node, "{%s}RangoUtilizados" % NS)
            _t(block, "Inicial", str(start))
            _t(block, "Final", str(end))
        for start, end in ranges_of([ln.folio for ln in voided]):
            block = etree.SubElement(node, "{%s}RangoAnulados" % NS)
            _t(block, "Inicial", str(start))
            _t(block, "Final", str(end))


def _t(parent: etree._Element, tag: str, value: str) -> None:
    etree.SubElement(parent, "{%s}%s" % (NS, tag)).text = value
