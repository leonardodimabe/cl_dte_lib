"""Boleta electrónica (39/41) — set de prueba de Boleta Electrónica del SII.

Los cinco casos del set, con precios que ya traen el IVA incluido. Lo que se
verifica sobre todo es que neto + IVA + exento dé EXACTAMENTE el bruto cobrado:
al despejar el neto con una división, un redondeo mal hecho descuadra en $1 y el
SII rechaza la boleta.
"""

import datetime as dt
from pathlib import Path

import pytest
from lxml import etree

from dte_chile import signer
from dte_chile.document_types import DTEType, ServiceIndicator
from dte_chile.models import DTE, Issuer, Item, Receiver, Reference
from dte_chile.receipt import (
    ANONYMOUS_RECEIVER_RUT,
    MAX_RECEIPTS,
    ReceiptCover,
    build_receipt,
    build_receipt_envelope,
    serialize,
    subtotals_for,
)
from dte_chile.validation import Validator

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
TS = dt.datetime(2026, 8, 31, 12, 0, 0)
ISSUE_DATE = dt.date(2026, 8, 31)

pytestmark = pytest.mark.skipif(
    not (SCHEMAS / "bol" / "EnvioBOLETA_v11.xsd").exists(),
    reason="XSD de boleta no presente en schemas/bol",
)


def _issuer():
    return Issuer(
        rut="77262159-0",
        business_name="CONSTRUCTORA DIMABE SPA",
        activity="OTRAS ACTIVIDADES ESPECIALIZADAS DE CONSTRUCCION",
        economic_activity=439000,
        address="MONS. FDO. DE BARRIONUEVO #1540 MARIA FERNANDA",
        commune="RANCAGUA",
        city="RANCAGUA",
        branch_code=4229993,
    )


def _receiver():
    # La boleta no identifica al comprador: RUT genérico de consumidor final.
    return Receiver(
        rut=ANONYMOUS_RECEIVER_RUT, business_name="", activity="", address="", commune=""
    )


def _receipt(folio, items, case=None, doc_type=DTEType.RECEIPT):
    references = []
    if case:
        # El set pide «<CodRef> SET» y «<RazonRef> CASO-n».
        references = [Reference(doc_type=None, folio="", code="SET", reason=case)]
    return DTE(
        type=doc_type,
        folio=folio,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(),
        items=items,
        references=references,
        prices_include_vat=True,
        service_indicator=ServiceIndicator.SALES_AND_SERVICE,
    )


def _set_receipts():
    """Los cinco casos del set, con los precios tal como los da (con IVA)."""
    return [
        _receipt(
            1,
            [Item("Cambio de aceite", 1, 19900), Item("Alineacion y balanceo", 1, 9900)],
            case="CASO-1",
        ),
        _receipt(2, [Item("Papel de regalo", 17, 120)], case="CASO-2"),
        _receipt(3, [Item("Sandwic", 2, 1500), Item("Bebida", 2, 550)], case="CASO-3"),
        _receipt(
            4,
            [
                Item("item afecto 1", 8, 1590),
                Item("item exento 2", 2, 1000, exempt=True),
            ],
            case="CASO-4",
        ),
        _receipt(5, [Item("Arroz", 5, 700, unit="Kg")], case="CASO-5"),
    ]


def _envelope(receipts, cert, caf_factory):
    signed = [
        signer.sign_document(build_receipt(r, caf_factory(int(r.type)), TS), cert) for r in receipts
    ]
    cover = ReceiptCover(
        issuer_rut="77262159-0",
        sender_rut="12291733-9",
        resolution_date=dt.date(2026, 1, 1),
        subtotals=subtotals_for(receipts),
    )
    return serialize(build_receipt_envelope(signed, cover, cert, TS))


# --------------------------------------------------------------------------- #
#  Totales con precios brutos
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("case", "net", "vat", "exempt", "total"),
    [
        ("CASO-1", 25042, 4758, 0, 29800),
        ("CASO-2", 1714, 326, 0, 2040),
        ("CASO-3", 3445, 655, 0, 4100),
        ("CASO-4", 10689, 2031, 2000, 14720),
        ("CASO-5", 2941, 559, 0, 3500),
    ],
)
def test_set_case_totals(case, net, vat, exempt, total):
    receipt = [r for r in _set_receipts() if r.references[0].reason == case][0]
    assert receipt.net_amount == net
    assert receipt.vat == vat
    assert receipt.exempt_amount == exempt
    assert receipt.total_amount == total
    # Lo que de verdad importa: que sume exacto.
    assert receipt.net_amount + receipt.vat + receipt.exempt_amount == receipt.total_amount


def test_vat_is_derived_by_difference_not_by_rounding():
    """Con 1.714,29 de neto teórico, redondear el IVA por separado descuadra."""
    receipt = _receipt(1, [Item("Papel de regalo", 17, 120)])
    assert receipt.net_amount == 1714
    assert receipt.vat == 326  # 2040 - 1714, no round(1714 * 0.19) = 326
    assert receipt.net_amount + receipt.vat == 2040


def test_net_prices_still_add_the_vat_on_top():
    """Sin precios brutos, el comportamiento de siempre no cambia."""
    receipt = _receipt(1, [Item("A", 1, 10000)])
    receipt.prices_include_vat = False
    assert receipt.net_amount == 10000
    assert receipt.vat == 1900
    assert receipt.total_amount == 11900


# --------------------------------------------------------------------------- #
#  Documento
# --------------------------------------------------------------------------- #
def test_iddoc_carries_the_service_indicator(cert, caf_factory):
    document = build_receipt(_set_receipts()[0], caf_factory(39), TS)
    id_doc = document.find("Encabezado/IdDoc")
    assert [c.tag for c in id_doc] == ["TipoDTE", "Folio", "FchEmis", "IndServicio"]
    assert id_doc.findtext("IndServicio") == "3"  # boleta de ventas y servicios


def test_net_prices_declare_indmntneto(cert, caf_factory):
    receipt = _set_receipts()[0]
    receipt.prices_include_vat = False
    id_doc = build_receipt(receipt, caf_factory(39), TS).find("Encabezado/IdDoc")
    assert id_doc.findtext("IndMntNeto") == "2"


def test_issuer_uses_the_boleta_tag_names(cert, caf_factory):
    """En la boleta son RznSocEmisor/GiroEmisor, no RznSoc/GiroEmis."""
    document = build_receipt(_set_receipts()[0], caf_factory(39), TS)
    issuer = document.find("Encabezado/Emisor")
    assert [c.tag for c in issuer] == [
        "RUTEmisor",
        "RznSocEmisor",
        "GiroEmisor",
        "CdgSIISucur",
        "DirOrigen",
        "CmnaOrigen",
        "CiudadOrigen",
    ]


def test_totals_have_no_vat_rate(cert, caf_factory):
    """El XSD de la boleta no tiene TasaIVA."""
    document = build_receipt(_set_receipts()[0], caf_factory(39), TS)
    totals = document.find("Encabezado/Totales")
    assert [c.tag for c in totals] == ["MntNeto", "IVA", "MntTotal"]


def test_exempt_line_is_flagged_and_split(cert, caf_factory):
    document = build_receipt(_set_receipts()[3], caf_factory(39), TS)
    details = document.findall("Detalle")
    assert details[1].findtext("IndExe") == "1"
    totals = document.find("Encabezado/Totales")
    assert totals.findtext("MntExe") == "2000"


def test_unit_of_measure_is_emitted(cert, caf_factory):
    """El set pide informar la unidad Kg en el caso 5."""
    document = build_receipt(_set_receipts()[4], caf_factory(39), TS)
    assert document.find("Detalle").findtext("UnmdItem") == "Kg"


def test_case_reference_identifies_the_set_case(cert, caf_factory):
    """El set de boletas: «<CodRef> SET · <RazonRef> CASO-1».

    En la boleta TpoDocRef es para un documento tributario y «se debe utilizar
    un valor Numérico»; CodRef es el «Código alfanumérico establecido por la
    Empresa». Poner SET en TpoDocRef dejaba texto en un campo numérico.
    """
    document = build_receipt(_set_receipts()[0], caf_factory(39), TS)
    reference = document.find("Referencia")
    assert reference.findtext("CodRef") == "SET"
    assert reference.findtext("RazonRef") == "CASO-1"
    assert reference.find("TpoDocRef") is None
    assert reference.find("FolioRef") is None


def test_non_receipt_type_is_rejected(caf_factory):
    invoice = _receipt(1, [Item("A", 1, 1000)], doc_type=DTEType.AFFECTED_INVOICE)
    with pytest.raises(ValueError, match="no es una boleta"):
        build_receipt(invoice, caf_factory(33), TS)


# --------------------------------------------------------------------------- #
#  Sobre
# --------------------------------------------------------------------------- #
def test_whole_set_validates_against_official_xsd(cert, caf_factory):
    xml = _envelope(_set_receipts(), cert, caf_factory)
    Validator(SCHEMAS).validate(xml)


def test_envelope_is_signed(cert, caf_factory):
    xml = _envelope(_set_receipts(), cert, caf_factory)
    results = signer.verify_signatures(etree.fromstring(xml))
    assert results and all(results)


def test_subtotals_group_affected_and_exempt_receipts():
    receipts = _set_receipts() + [
        _receipt(1, [Item("Servicio exento", 1, 5000)], doc_type=DTEType.EXEMPT_RECEIPT)
    ]
    assert subtotals_for(receipts) == [(39, 5), (41, 1)]


def test_envelope_rejects_more_than_the_limit(cert, caf_factory):
    too_many = [etree.Element("DTE")] * (MAX_RECEIPTS + 1)
    cover = ReceiptCover("77262159-0", "12291733-9", dt.date(2026, 1, 1))
    with pytest.raises(ValueError, match="admite 500 boletas"):
        build_receipt_envelope(too_many, cover, cert, TS)


def test_exempt_receipt_has_no_vat(cert, caf_factory):
    receipt = _receipt(1, [Item("Servicio exento", 1, 5000)], doc_type=DTEType.EXEMPT_RECEIPT)
    assert receipt.net_amount == 0
    assert receipt.vat == 0
    assert receipt.total_amount == 5000
    Validator(SCHEMAS).validate(_envelope([receipt], cert, caf_factory))


# --------------------------------------------------------------------------- #
#  Round-trip e impresión
# --------------------------------------------------------------------------- #
def test_receipt_can_be_read_back_and_printed(cert, caf_factory):
    """Sin recuperar los precios brutos, los totales al releer no cuadran."""
    from dte_chile import parser
    from dte_chile.representation import ResolutionInfo, generate_copies

    original = _set_receipts()[0]
    xml = _envelope([original], cert, caf_factory)

    parsed = parser.parse_documents(xml)[0]
    back = parsed.dte
    assert back.prices_include_vat is True
    assert back.net_amount == original.net_amount
    assert back.vat == original.vat
    assert back.total_amount == original.total_amount == 29800
    # La boleta nombra distinto al emisor: RznSocEmisor/GiroEmisor.
    assert back.issuer.business_name == "CONSTRUCTORA DIMABE SPA"
    assert back.issuer.activity.startswith("OTRAS ACTIVIDADES")
    assert back.service_indicator is ServiceIndicator.SALES_AND_SERVICE

    html = generate_copies(
        back,
        parsed.element,
        ResolutionInfo(number=0, date=dt.date(2026, 8, 26)),
        verification_url="boletas.dimabe.cl",
    )
    assert "BOLETA ELECTRÓNICA" in html
    assert "Consulte su boleta en" in html
    assert "boletas.dimabe.cl" in html


def test_net_priced_receipt_round_trips_too(cert, caf_factory):
    receipt = _set_receipts()[0]
    receipt.prices_include_vat = False
    from dte_chile import parser

    back = parser.parse_documents(_envelope([receipt], cert, caf_factory))[0].dte
    assert back.prices_include_vat is False
    assert back.total_amount == receipt.total_amount


def test_verification_url_only_on_receipts(cert, caf_factory):
    """En una factura ese aviso no corresponde."""
    from dte_chile.models import DTE as _DTE
    from dte_chile.representation import ResolutionInfo, generate_html

    invoice = _DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=1,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("A", 1, 1000)],
    )
    html = generate_html(
        invoice,
        build_receipt(_set_receipts()[0], caf_factory(39), TS),
        ResolutionInfo(number=0, date=dt.date(2026, 8, 26)),
        verification_url="boletas.dimabe.cl",
    )
    assert "Consulte su boleta" not in html
