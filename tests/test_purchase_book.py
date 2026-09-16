"""Libro de Compras — set de certificación 5038172.

Los tres casos que el libro de ventas no tiene: IVA de uso común (con factor de
proporcionalidad 0,60), IVA no recuperable por entrega gratuita, e IVA retenido
total de una factura de compra.
"""

import datetime as dt
from pathlib import Path

import pytest
from lxml import etree

from dte_chile.book import (
    NON_RECOVERABLE_FREE_DELIVERY,
    BookCover,
    BookLine,
    NonRecoverableVat,
    build_book,
    serialize,
)
from dte_chile.validation import Validator

NS = "{http://www.sii.cl/SiiDte}"
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
TS = dt.datetime(2026, 8, 31, 18, 0, 0)
PERIOD_DATE = dt.date(2026, 8, 10)

pytestmark = pytest.mark.skipif(
    not (SCHEMAS / "iecv" / "LibroCV_v10.xsd").exists(),
    reason="XSD del SII no presentes en schemas/",
)


def _vat(net: int) -> int:
    return round(net * 0.19)


def _set_5038172_lines():
    """Los siete documentos del set, con sus montos exactos."""
    return [
        # Factura del giro con derecho a crédito.
        BookLine(
            30,
            234,
            PERIOD_DATE,
            "17099910-K",
            "Proveedor A",
            net_amount=40670,
            vat_amount=_vat(40670),
            total_amount=40670 + _vat(40670),
        ),
        # Factura electrónica del giro, con parte exenta.
        BookLine(
            33,
            32,
            PERIOD_DATE,
            "17099910-K",
            "Proveedor B",
            exempt_amount=9928,
            net_amount=9526,
            vat_amount=_vat(9526),
            total_amount=9928 + 9526 + _vat(9526),
        ),
        # Factura con IVA de uso común: el IVA NO va en MntIVA.
        BookLine(
            30,
            781,
            PERIOD_DATE,
            "17099910-K",
            "Proveedor C",
            net_amount=30019,
            common_use_vat=_vat(30019),
            total_amount=30019 + _vat(30019),
        ),
        # Nota de crédito por descuento a la factura 234.
        BookLine(
            60,
            451,
            PERIOD_DATE,
            "17099910-K",
            "Proveedor A",
            net_amount=2846,
            vat_amount=_vat(2846),
            total_amount=2846 + _vat(2846),
        ),
        # Entrega gratuita del proveedor: el IVA no da crédito.
        BookLine(
            33,
            67,
            PERIOD_DATE,
            "17099910-K",
            "Proveedor D",
            net_amount=11305,
            non_recoverable_vat=[NonRecoverableVat(NON_RECOVERABLE_FREE_DELIVERY, _vat(11305))],
            total_amount=11305 + _vat(11305),
        ),
        # Factura de compra con retención TOTAL del IVA: el comprador retiene
        # el impuesto entero, así que no lo paga. El total es el neto, y la
        # retención se declara aparte.
        BookLine(
            46,
            9,
            PERIOD_DATE,
            "17099910-K",
            "Proveedor E",
            net_amount=10215,
            vat_amount=_vat(10215),
            retained_total_vat=_vat(10215),
            total_amount=10215,
        ),
        # Nota de crédito por descuento a la factura electrónica 32.
        BookLine(
            60,
            211,
            PERIOD_DATE,
            "17099910-K",
            "Proveedor B",
            net_amount=7246,
            vat_amount=_vat(7246),
            total_amount=7246 + _vat(7246),
        ),
    ]


def _cover(lines=None, factor=0.60):
    return BookCover(
        issuer_rut="77262159-0",
        sender_rut="12291733-9",
        period="2026-08",
        operation_type="COMPRA",
        resolution_date=dt.date(2026, 1, 1),
        notification_folio=5038172,
        proportionality_factor=factor,
        lines=_set_5038172_lines() if lines is None else lines,
    )


def _build(cert, **kw):
    return build_book(_cover(**kw), cert, TS)


def _totals_for(book, doc_type):
    for node in book.iter(f"{NS}TotalesPeriodo"):
        if node.findtext(f"{NS}TpoDoc") == str(doc_type):
            return node
    raise AssertionError(f"no hay TotalesPeriodo para el tipo {doc_type}")


# --------------------------------------------------------------------------- #
#  IVA de uso común
# --------------------------------------------------------------------------- #
def test_common_use_vat_is_not_counted_as_recoverable(cert):
    """El IVA de uso común va en IVAUsoComun, no en MntIVA."""
    book = _build(cert)
    detail = [d for d in book.iter(f"{NS}Detalle") if d.findtext(f"{NS}NroDoc") == "781"][0]
    assert detail.findtext(f"{NS}IVAUsoComun") == str(_vat(30019))
    # MntIVA se declara siempre, pero en cero: el IVA no está ahí.
    assert detail.findtext(f"{NS}MntIVA") == "0"


def test_proportionality_factor_drives_the_credit(cert):
    """Con factor 0,60 el crédito es el 60% del IVA de uso común."""
    totals = _totals_for(_build(cert), 30)
    common = _vat(30019)
    assert totals.findtext(f"{NS}TotOpIVAUsoComun") == "1"
    assert totals.findtext(f"{NS}TotIVAUsoComun") == str(common)
    assert totals.findtext(f"{NS}FctProp") == "0.60"
    assert totals.findtext(f"{NS}TotCredIVAUsoComun") == str(round(common * 0.60))


def test_without_factor_no_credit_is_declared(cert):
    """Sin factor no se puede calcular el crédito: se omite en vez de inventarlo."""
    totals = _totals_for(_build(cert, factor=None), 30)
    assert totals.findtext(f"{NS}TotIVAUsoComun") == str(_vat(30019))
    assert totals.find(f"{NS}FctProp") is None
    assert totals.find(f"{NS}TotCredIVAUsoComun") is None


# --------------------------------------------------------------------------- #
#  IVA no recuperable
# --------------------------------------------------------------------------- #
def test_free_delivery_vat_is_non_recoverable(cert):
    book = _build(cert)
    detail = [d for d in book.iter(f"{NS}Detalle") if d.findtext(f"{NS}NroDoc") == "67"][0]
    node = detail.find(f"{NS}IVANoRec")
    assert node.findtext(f"{NS}CodIVANoRec") == "4"  # entrega gratuita
    assert node.findtext(f"{NS}MntIVANoRec") == str(_vat(11305))


def test_non_recoverable_totals_group_by_reason(cert):
    totals = _totals_for(_build(cert), 33)
    node = totals.find(f"{NS}TotIVANoRec")
    assert node.findtext(f"{NS}CodIVANoRec") == "4"
    assert node.findtext(f"{NS}TotOpIVANoRec") == "1"
    assert node.findtext(f"{NS}TotMntIVANoRec") == str(_vat(11305))


# --------------------------------------------------------------------------- #
#  IVA retenido total
# --------------------------------------------------------------------------- #
def test_a_purchase_invoice_declares_the_retained_vat(cert):
    """Una factura de compra con retención total SÍ declara IVARetTotal.

    El XSD lo anota «(LV)» y de ahí salió la idea de que era campo del libro de
    ventas. Pero la validación 31 del SII lo admite «en liquidaciones,
    liquidaciones factura, FACTURAS DE COMPRA, notas de crédito y notas de
    débito», y una factura de compra se registra en el libro de compras de
    quien la emitió. Omitirlo costó un rechazo del set 5038172: «El Monto Total
    No Cuadra / No Informa Adecuadamente IVA Retenido Total».

    El total SÍ baja al neto. Lo fija el ejemplo del SII en «Ejemplos de
    Registro de Documentos en la IECV», con sus propias cifras:

        Neto 75.000 · 19% IVA a retener 14.250 · menos 19% IVA retenido 14.250
        Total 75.000

        <MntNeto>75000</MntNeto><MntIVA>14250</MntIVA>
        <IVARetTotal>14250</IVARetTotal><MntTotal>75000</MntTotal>
    """
    book = _build(cert)
    detail = [d for d in book.iter(f"{NS}Detalle") if d.findtext(f"{NS}NroDoc") == "9"][0]
    assert detail.findtext(f"{NS}MntIVA") == str(_vat(10215))
    assert detail.findtext(f"{NS}IVARetTotal") == str(_vat(10215))
    assert detail.findtext(f"{NS}MntTotal") == "10215"

    totals = _totals_for(book, 46)
    assert totals.findtext(f"{NS}TotIVARetTotal") == str(_vat(10215))
    assert totals.findtext(f"{NS}TotOpIVARetTotal") == "1"


def test_the_tax_rate_is_declared_even_when_the_vat_is_not_recoverable(cert):
    """«Reparo en Detalle - Falta [TasaImp]» en las dos líneas sin IVA recuperable.

    El IVA de uso común y el no recuperable también son IVA a una tasa, y la
    validación 25 del SII la exige «para al menos los siguientes documentos
    Facturas, Liquidación, Liquidaciones Factura, Facturas de Compra, entre
    otros». Mirando sólo `vat_amount` se omitía justo donde el impuesto había
    ido a parar a otro campo.
    """
    book = _build(cert)
    for folio in ("781", "67"):
        detail = [d for d in book.iter(f"{NS}Detalle") if d.findtext(f"{NS}NroDoc") == folio][0]
        assert detail.findtext(f"{NS}MntIVA") == "0", "esta línea no tiene IVA recuperable"
        assert detail.findtext(f"{NS}TasaImp") == "19", f"folio {folio} sin TasaImp"


# --------------------------------------------------------------------------- #
#  Operaciones con IVA recuperable
# --------------------------------------------------------------------------- #
def test_recoverable_operations_exclude_common_use(cert):
    """El tipo 30 trae dos documentos: uno con crédito y otro de uso común."""
    totals = _totals_for(_build(cert), 30)
    assert totals.findtext(f"{NS}TotDoc") == "2"
    assert totals.findtext(f"{NS}TotOpIVARec") == "1"


# --------------------------------------------------------------------------- #
#  XSD
# --------------------------------------------------------------------------- #
def test_purchase_book_validates_against_official_xsd(cert):
    Validator(SCHEMAS).validate(serialize(_build(cert)))


def test_summary_follows_xsd_order(cert):
    totals = _totals_for(_build(cert), 30)
    assert [etree.QName(c).localname for c in totals] == [
        "TpoDoc",
        "TotDoc",
        "TotMntExe",
        "TotMntNeto",
        "TotOpIVARec",
        "TotMntIVA",
        "TotOpIVAUsoComun",
        "TotIVAUsoComun",
        "FctProp",
        "TotCredIVAUsoComun",
        "TotMntTotal",
    ]


def test_sales_book_is_unaffected(cert):
    """El libro de ventas no debe ganar campos de compras."""
    lines = [
        BookLine(
            33,
            1,
            PERIOD_DATE,
            "17099910-K",
            "Cliente",
            net_amount=100000,
            vat_amount=19000,
            total_amount=119000,
        )
    ]
    cover = BookCover(
        issuer_rut="77262159-0",
        sender_rut="12291733-9",
        period="2026-08",
        operation_type="VENTA",
        lines=lines,
    )
    book = build_book(cover, cert, TS)
    totals = _totals_for(book, 33)
    # TotOpIVARec está anotado (LC): no corresponde a un libro de ventas.
    # TotMntPeriodo sí es (LV) y es por donde el SII cuadra el libro.
    assert [etree.QName(c).localname for c in totals] == [
        "TpoDoc",
        "TotDoc",
        "TotMntExe",
        "TotMntNeto",
        "TotMntIVA",
        "TotMntTotal",
        "TotMntPeriodo",
    ]
    assert totals.findtext(f"{NS}TotMntPeriodo") == "119000"
    Validator(SCHEMAS).validate(serialize(book))


# --------------------------------------------------------------------------- #
#  Notas de crédito y débito en el libro de ventas
# --------------------------------------------------------------------------- #
def test_a_note_declares_the_document_it_modifies(cert):
    """Sin TpoDocRef/FolioDocRef el SII no puede atar la nota a su factura."""
    lines = [
        BookLine(
            61,
            25,
            PERIOD_DATE,
            "60803000-K",
            "Cliente",
            net_amount=1000,
            vat_amount=190,
            total_amount=1190,
            ref_doc_type=33,
            ref_folio=19,
        )
    ]
    cover = BookCover(
        issuer_rut="77262159-0",
        sender_rut="12291733-9",
        period="2026-09",
        operation_type="VENTA",
        lines=lines,
    )
    detail = build_book(cover, cert, TS).find(f".//{NS}Detalle")

    assert detail.findtext(f"{NS}TpoDocRef") == "33"
    assert detail.findtext(f"{NS}FolioDocRef") == "19"
    # Orden del XSD: la referencia va después de la razón social y antes de los montos.
    tags = [etree.QName(c).localname for c in detail]
    assert tags.index("TpoDocRef") < tags.index("MntExe")


def test_the_reference_is_not_emitted_in_a_purchase_book(cert):
    """TpoDocRef está anotado (LV): es del libro de ventas."""
    lines = [
        BookLine(
            60,
            451,
            PERIOD_DATE,
            "76158145-7",
            "Proveedor",
            net_amount=1000,
            vat_amount=190,
            total_amount=1190,
            ref_doc_type=30,
            ref_folio=234,
        )
    ]
    cover = BookCover(
        issuer_rut="77262159-0",
        sender_rut="12291733-9",
        period="2026-09",
        operation_type="COMPRA",
        lines=lines,
    )
    detail = build_book(cover, cert, TS).find(f".//{NS}Detalle")
    assert detail.find(f"{NS}TpoDocRef") is None


def test_a_zero_value_document_still_declares_its_three_amounts(cert):
    """Una nota que corrige texto no mueve montos, pero los declara en cero.

    Una línea con sólo MntTotal=0 deja al Servicio sin los sumandos con que
    cuadra el libro.
    """
    lines = [BookLine(61, 25, PERIOD_DATE, "60803000-K", "Cliente", ref_doc_type=33, ref_folio=19)]
    cover = BookCover(
        issuer_rut="77262159-0",
        sender_rut="12291733-9",
        period="2026-09",
        operation_type="VENTA",
        lines=lines,
    )
    detail = build_book(cover, cert, TS).find(f".//{NS}Detalle")

    assert detail.findtext(f"{NS}MntExe") == "0"
    assert detail.findtext(f"{NS}MntNeto") == "0"
    assert detail.findtext(f"{NS}MntIVA") == "0"
    assert detail.findtext(f"{NS}MntTotal") == "0"
