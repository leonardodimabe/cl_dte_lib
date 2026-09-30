"""Impuestos adicionales que se recargan al comprador: el ILA de las bebidas.

A diferencia de la retención (que resta), el ILA se suma al total y grava sólo
las líneas de la bebida: su base es el neto de esas líneas. Códigos y tasas del
Formato DTE v2.0, pág. 44: 24 licores 31,5 %, 25 vinos 20,5 %, 26 cervezas
20,5 %, 27 bebidas analcohólicas 10 %, 271 bebidas azucaradas 18 %.
"""

import datetime as dt
from pathlib import Path

import pytest
from lxml import etree

from dte_chile import representation as rep
from dte_chile.document_types import DTEType, ReferenceCode
from dte_chile.models import (
    DTE,
    AdditionalTax,
    GlobalDiscount,
    Issuer,
    Item,
    Receiver,
    Reference,
)
from dte_chile.parser import parse_documents
from dte_chile.xml_builder import _header, _items, build_document

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
_TS = dt.datetime(2026, 9, 30, 10, 0, 0)


def _issuer():
    return Issuer(
        rut="76158145-7",
        business_name="DISTRIBUIDORA DE BEBIDAS SPA",
        activity="Venta al por mayor de bebidas",
        economic_activity=463020,
        address="Calle 1",
        commune="Santiago",
        city="Santiago",
    )


def _receiver():
    return Receiver(
        rut="17099910-K",
        business_name="ALMACEN EJEMPLO LTDA",
        activity="Almacén",
        address="Calle 2",
        commune="Providencia",
    )


def _invoice(items=None, taxes=None, doc_type=DTEType.AFFECTED_INVOICE, **kw):
    return DTE(
        type=doc_type,
        folio=1,
        issue_date=dt.date(2026, 9, 30),
        issuer=_issuer(),
        receiver=_receiver(),
        items=items
        if items is not None
        else [
            Item("Vino tinto 750cc", quantity=12, unit_price=3000, additional_tax_code=25),
            Item("Bebida cola 1,5L", quantity=10, unit_price=1000, additional_tax_code=271),
            Item("Servilletas", quantity=5, unit_price=500),
        ],
        additional_taxes=taxes
        if taxes is not None
        else [AdditionalTax(code=25, rate=20.5), AdditionalTax(code=271, rate=18)],
        **kw,
    )


# --------------------------------------------------------------------------- #
#  Totales
# --------------------------------------------------------------------------- #
def test_ila_is_computed_over_its_own_lines_and_added_to_total():
    dte = _invoice()
    # Neto 36.000 + 10.000 + 2.500 = 48.500; IVA 19 % = 9.215.
    assert dte.net_amount == 48500
    assert dte.vat == 9215
    # ILA vinos: 36.000 × 20,5 % = 7.380; bebidas azucaradas: 10.000 × 18 % = 1.800.
    assert dte.additional_tax_amount == 7380 + 1800
    assert dte.total_amount == 48500 + 9215 + 9180


def test_line_discount_lowers_ila_base():
    dte = _invoice(
        items=[Item("Vino", quantity=10, unit_price=1000, discount_pct=10, additional_tax_code=25)],
        taxes=[AdditionalTax(code=25, rate=20.5)],
    )
    assert dte.additional_tax_amount == round(9000 * 0.205)


def test_explicit_amount_is_respected():
    tax = AdditionalTax(code=27, rate=10, amount=123)
    assert tax.amount_over([]) == 123


# --------------------------------------------------------------------------- #
#  XML
# --------------------------------------------------------------------------- #
def test_totals_carry_one_impto_reten_per_tax():
    doc = etree.Element("Documento")
    _header(doc, _invoice())
    totals = doc.find("Encabezado/Totales")
    nodes = totals.findall("ImptoReten")
    found = [(n.findtext("TipoImp"), n.findtext("TasaImp"), n.findtext("MontoImp")) for n in nodes]
    assert found == [
        ("25", "20.5", "7380"),
        ("271", "18", "1800"),
    ]
    assert totals.findtext("MntTotal") == str(48500 + 9215 + 9180)
    # Orden del XSD: IVA, ImptoReten, MntTotal.
    tags = [c.tag for c in totals]
    assert tags.index("IVA") < tags.index("ImptoReten") < tags.index("MntTotal")


def test_only_taxed_lines_carry_cod_imp_adic():
    doc = etree.Element("Documento")
    _items(doc, _invoice())
    codes = [[c.text for c in d.findall("CodImpAdic")] for d in doc.findall("Detalle")]
    assert codes == [["25"], ["271"], []]


# --------------------------------------------------------------------------- #
#  Validación
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"doc_type": DTEType.EXEMPT_INVOICE}, "no admite impuestos adicionales"),
        ({"taxes": [AdditionalTax(code=25, rate=20.5)]}, "no está declarado"),
        (
            {
                "items": [Item("Vino", quantity=1, unit_price=1000, additional_tax_code=25)],
            },
            "no grava ninguna línea",
        ),
        (
            {
                "items": [Item("X", quantity=1, unit_price=1, additional_tax_code=99)],
                "taxes": [AdditionalTax(code=99, rate=1)],
            },
            "no soportado",
        ),
        (
            {
                "items": [
                    Item("Vino", quantity=1, unit_price=1000, exempt=True, additional_tax_code=25)
                ],
                "taxes": [AdditionalTax(code=25, rate=20.5)],
            },
            "exenta",
        ),
        (
            {"global_discounts": [GlobalDiscount(kind="D", value_type="%", value=10)]},
            "descuentos o recargos",
        ),
    ],
)
def test_invalid_combinations_are_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        _invoice(**kwargs).validate_content()


def test_credit_note_admits_ila():
    note = _invoice(
        doc_type=DTEType.CREDIT_NOTE,
        references=[
            Reference(
                doc_type=33,
                folio="1",
                date=dt.date(2026, 9, 30),
                code=ReferenceCode.CORRECT_AMOUNTS,
                reason="DEVOLUCION",
            )
        ],
    )
    note.validate_content()


# --------------------------------------------------------------------------- #
#  Ida y vuelta: construir, leer, imprimir
# --------------------------------------------------------------------------- #
def test_parser_reads_ila_as_additional_not_retention(caf_factory):
    dte = _invoice()
    xml = etree.tostring(build_document(dte, caf_factory(33), _TS))
    (parsed,) = parse_documents(xml)
    assert parsed.dte.retentions == []
    assert [(t.code, t.amount) for t in parsed.dte.additional_taxes] == [(25, 7380), (271, 1800)]
    assert [i.additional_tax_code for i in parsed.dte.items] == [25, 271, None]
    assert parsed.dte.total_amount == dte.total_amount


def test_printed_document_shows_ila(caf_factory):
    html = rep.generate_html(
        _invoice(),
        build_document(_invoice(), caf_factory(33), _TS),
        rep.ResolutionInfo(number=0, date=dt.date(2026, 1, 1)),
    )
    assert "ILA vinos (20.5%)" in html
    assert "ILA bebidas azucaradas (18%)" in html


@pytest.mark.skipif(
    not (SCHEMAS / "dte" / "DTE_v10.xsd").exists(),
    reason="XSD del SII no presentes en schemas/ (ver schemas/README.md)",
)
def test_invoice_with_ila_valid_xsd(cert, caf_factory):
    from dte_chile.envelope import Cover, build_envelope, serialize
    from dte_chile.signer import sign_document
    from dte_chile.validation import Validator

    signed = [sign_document(build_document(_invoice(), caf_factory(33), _TS), cert)]
    cover = Cover(
        issuer_rut="76158145-7",
        sender_rut="77777777-7",
        resolution_date=dt.date(2026, 1, 1),
        subtotals=[(33, 1)],
    )
    Validator(SCHEMAS).validate(serialize(build_envelope(signed, cover, cert, _TS)))


# --------------------------------------------------------------------------- #
#  Libro de Compras y Ventas
# --------------------------------------------------------------------------- #
def _ila_book(operation_type):
    from dte_chile.book import BookCover, BookLine, OtherTax

    return BookCover(
        issuer_rut="76158145-7",
        sender_rut="77777777-7",
        period="2026-09",
        operation_type=operation_type,
        lines=[
            BookLine(
                33,
                10,
                dt.date(2026, 9, 30),
                "17099910-K",
                "Cliente",
                net_amount=48500,
                vat_amount=9215,
                total_amount=66895,
                other_taxes=[OtherTax(25, 7380, 20.5), OtherTax(271, 1800, 18)],
            ),
            BookLine(
                33,
                11,
                dt.date(2026, 9, 30),
                "17099910-K",
                "Cliente",
                net_amount=10000,
                vat_amount=1900,
                total_amount=13950,
                other_taxes=[OtherTax(25, 2050, 20.5)],
            ),
        ],
    )


@pytest.mark.parametrize("operation_type", ["VENTA", "COMPRA"])
def test_book_reports_ila_per_line_and_in_totals(cert, operation_type):
    from dte_chile.book import NS, build_book

    book = build_book(_ila_book(operation_type), cert, _TS)
    ns = {"s": NS}
    details = book.findall(".//s:Detalle", ns)
    first = [
        (o.findtext("s:CodImp", namespaces=ns), o.findtext("s:MntImp", namespaces=ns))
        for o in details[0].findall("s:OtrosImp", ns)
    ]
    assert first == [("25", "7380"), ("271", "1800")]
    totals = {
        t.findtext("s:CodImp", namespaces=ns): t.findtext("s:TotMntImp", namespaces=ns)
        for t in book.findall(".//s:TotOtrosImp", ns)
    }
    assert totals == {"25": "9430", "271": "1800"}


@pytest.mark.skipif(
    not (SCHEMAS / "dte" / "DTE_v10.xsd").exists(),
    reason="XSD del SII no presentes en schemas/ (ver schemas/README.md)",
)
@pytest.mark.parametrize("operation_type", ["VENTA", "COMPRA"])
def test_book_with_ila_valid_xsd(cert, operation_type):
    from dte_chile import exchange as ix
    from dte_chile.book import build_book
    from dte_chile.validation import Validator

    book = build_book(_ila_book(operation_type), cert, _TS)
    Validator(SCHEMAS).validate(ix.serialize(book))
