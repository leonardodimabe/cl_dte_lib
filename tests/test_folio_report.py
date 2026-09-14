"""Consumo de Folios (RCOF) de las boletas del set."""

import datetime as dt
from pathlib import Path

import pytest
from lxml import etree

from dte_chile import signer
from dte_chile.document_types import DTEType, ServiceIndicator
from dte_chile.folio_report import (
    FolioReportCover,
    ReportLine,
    build_folio_report,
    ranges_of,
    report_line,
    serialize,
)
from dte_chile.models import DTE, Issuer, Item, Receiver
from dte_chile.receipt import ANONYMOUS_RECEIVER_RUT
from dte_chile.validation import Validator

NS = "{http://www.sii.cl/SiiDte}"
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
TS = dt.datetime(2026, 8, 31, 23, 30, 0)
DAY = dt.date(2026, 8, 31)

pytestmark = pytest.mark.skipif(
    not (SCHEMAS / "bol" / "ConsumoFolio_v10.xsd").exists(),
    reason="XSD de consumo de folios no presente",
)


def _receipt(folio, items):
    return DTE(
        type=DTEType.RECEIPT,
        folio=folio,
        issue_date=DAY,
        issuer=Issuer(
            rut="77262159-0",
            business_name="CONSTRUCTORA DIMABE SPA",
            activity="OTRAS ACTIVIDADES ESPECIALIZADAS DE CONSTRUCCION",
            economic_activity=439000,
            address="MONS. FDO. DE BARRIONUEVO #1540",
            commune="RANCAGUA",
        ),
        receiver=Receiver(ANONYMOUS_RECEIVER_RUT, "", "", "", ""),
        items=items,
        prices_include_vat=True,
        service_indicator=ServiceIndicator.SALES_AND_SERVICE,
    )


def _set_lines():
    """Las cinco boletas del set, más un folio anulado."""
    receipts = [
        _receipt(1, [Item("Cambio de aceite", 1, 19900), Item("Alineacion y balanceo", 1, 9900)]),
        _receipt(2, [Item("Papel de regalo", 17, 120)]),
        _receipt(3, [Item("Sandwic", 2, 1500), Item("Bebida", 2, 550)]),
        _receipt(4, [Item("item afecto 1", 8, 1590), Item("item exento 2", 2, 1000, exempt=True)]),
        _receipt(5, [Item("Arroz", 5, 700, unit="Kg")]),
    ]
    lines = [report_line(r) for r in receipts]
    lines.append(ReportLine(doc_type=39, folio=6, voided=True))
    return lines


def _cover(lines=None, **kw):
    data = {
        "issuer_rut": "77262159-0",
        "sender_rut": "12291733-9",
        "start_date": DAY,
        "end_date": DAY,
        "sequence": 1,
        "lines": _set_lines() if lines is None else lines,
    }
    data.update(kw)
    return FolioReportCover(**data)


def _build(cert, **kw):
    return build_folio_report(_cover(**kw), cert, TS)


def _summary(report, doc_type=39):
    for node in report.iter(f"{NS}Resumen"):
        if node.findtext(f"{NS}TipoDocumento") == str(doc_type):
            return node
    raise AssertionError(f"no hay Resumen para el tipo {doc_type}")


# --------------------------------------------------------------------------- #
#  Compresión de rangos
# --------------------------------------------------------------------------- #
def test_consecutive_folios_become_one_range():
    assert ranges_of([1, 2, 3, 4, 5]) == [(1, 5)]


def test_gaps_split_the_ranges():
    assert ranges_of([1, 2, 3, 7, 8, 20]) == [(1, 3), (7, 8), (20, 20)]


def test_ranges_ignore_order_and_duplicates():
    assert ranges_of([3, 1, 2, 2]) == [(1, 3)]


def test_no_folios_no_ranges():
    assert ranges_of([]) == []


# --------------------------------------------------------------------------- #
#  Resumen
# --------------------------------------------------------------------------- #
def test_totals_sum_only_the_used_folios(cert):
    """El folio anulado cuenta como folio, pero no aporta montos."""
    summary = _summary(_build(cert))
    assert summary.findtext(f"{NS}MntNeto") == str(25042 + 1714 + 3445 + 10689 + 2941)
    assert summary.findtext(f"{NS}MntIva") == str(4758 + 326 + 655 + 2031 + 559)
    assert summary.findtext(f"{NS}MntExento") == "2000"
    assert summary.findtext(f"{NS}MntTotal") == str(29800 + 2040 + 4100 + 14720 + 3500)


def test_folio_counts_separate_used_from_voided(cert):
    summary = _summary(_build(cert))
    assert summary.findtext(f"{NS}FoliosEmitidos") == "5"
    assert summary.findtext(f"{NS}FoliosAnulados") == "1"
    assert summary.findtext(f"{NS}FoliosUtilizados") == "6"


def test_used_and_voided_ranges_are_reported_apart(cert):
    summary = _summary(_build(cert))
    used = summary.find(f"{NS}RangoUtilizados")
    assert (used.findtext(f"{NS}Inicial"), used.findtext(f"{NS}Final")) == ("1", "5")
    voided = summary.find(f"{NS}RangoAnulados")
    assert (voided.findtext(f"{NS}Inicial"), voided.findtext(f"{NS}Final")) == ("6", "6")


def test_vat_rate_only_when_there_is_vat(cert):
    lines = [ReportLine(doc_type=41, folio=1, exempt_amount=5000, total_amount=5000)]
    summary = _summary(_build(cert, lines=lines), doc_type=41)
    assert summary.find(f"{NS}MntIva") is None
    assert summary.find(f"{NS}TasaIVA") is None
    assert summary.findtext(f"{NS}MntExento") == "5000"


def test_each_document_type_gets_its_own_summary(cert):
    lines = _set_lines() + [ReportLine(doc_type=41, folio=1, exempt_amount=5000, total_amount=5000)]
    report = _build(cert, lines=lines)
    assert [n.findtext(f"{NS}TipoDocumento") for n in report.iter(f"{NS}Resumen")] == ["39", "41"]


# --------------------------------------------------------------------------- #
#  Carátula y firma
# --------------------------------------------------------------------------- #
def test_cover_follows_xsd_order(cert):
    cover = _build(cert).find(f"{NS}DocumentoConsumoFolios/{NS}Caratula")
    assert [etree.QName(c).localname for c in cover] == [
        "RutEmisor",
        "RutEnvia",
        "FchResol",
        "NroResol",
        "FchInicio",
        "FchFinal",
        "SecEnvio",
        "TmstFirmaEnv",
    ]


def test_correlative_is_optional(cert):
    cover = _build(cert, correlative=7).find(f"{NS}DocumentoConsumoFolios/{NS}Caratula")
    assert cover.findtext(f"{NS}Correlativo") == "7"


def test_backwards_period_is_rejected(cert):
    with pytest.raises(ValueError, match="termina antes de empezar"):
        _build(cert, start_date=DAY, end_date=DAY - dt.timedelta(days=1))


def test_report_is_signed_and_verifies(cert):
    reparsed = etree.fromstring(serialize(_build(cert)))
    assert signer.verify_signatures(reparsed) == [True]


def test_report_validates_against_official_xsd(cert):
    Validator(SCHEMAS).validate(serialize(_build(cert)))
