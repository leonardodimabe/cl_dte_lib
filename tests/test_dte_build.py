"""Tests del armado de dominio y XML sin criptografía (no requieren CAF/cert)."""

import datetime as dt

import pytest
from lxml import etree

from dte_chile.document_types import DTEType, ReferenceCode
from dte_chile.models import DTE, GlobalDiscount, Issuer, Item, Receiver, Reference
from dte_chile.xml_builder import _global_discounts, _header, _items, _references


def _issuer():
    return Issuer(
        rut="76192083-9",
        business_name="Comercial Ejemplo SpA",
        activity="Venta al por menor",
        economic_activity=479100,
        address="Av. Siempre Viva 742",
        commune="Santiago",
        city="Santiago",
    )


def _receiver():
    return Receiver(
        rut="17099910-K",
        business_name="Cliente Ejemplo Ltda",
        activity="Servicios",
        address="Calle Falsa 123",
        commune="Providencia",
    )


def test_totals_affected_invoice():
    dte = DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=101,
        issue_date=dt.date(2026, 6, 8),
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("Producto A", quantity=2, unit_price=10000)],
    )
    assert dte.net_amount == 20000
    assert dte.vat == 3800
    assert dte.total_amount == 23800
    assert dte.exempt_amount == 0


def test_totals_exempt_invoice():
    dte = DTE(
        type=DTEType.EXEMPT_INVOICE,
        folio=5,
        issue_date=dt.date(2026, 6, 8),
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("Servicio exento", quantity=1, unit_price=50000)],
    )
    assert dte.net_amount == 0
    assert dte.vat == 0
    assert dte.exempt_amount == 50000
    assert dte.total_amount == 50000


def test_credit_note_requires_reference():
    dte = DTE(
        type=DTEType.CREDIT_NOTE,
        folio=9,
        issue_date=dt.date(2026, 6, 8),
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("Anulación", quantity=1, unit_price=10000)],
    )
    with pytest.raises(ValueError, match="Referencia"):
        dte.validate()

    dte.references.append(
        Reference(
            doc_type=33,
            folio="101",
            date=dt.date(2026, 6, 1),
            code=ReferenceCode.CANCEL_DOCUMENT,
            reason="Anula factura 101",
        )
    )
    dte.validate()  # ya no lanza


def test_header_exempt_has_no_vat():
    dte = DTE(
        type=DTEType.EXEMPT_INVOICE,
        folio=5,
        issue_date=dt.date(2026, 6, 8),
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("Servicio", quantity=1, unit_price=50000)],
    )
    doc = etree.Element("Documento")
    _header(doc, dte)
    assert doc.find("Encabezado/Totales/IVA") is None
    assert doc.findtext("Encabezado/Totales/MntExe") == "50000"
    assert doc.findtext("Encabezado/Totales/MntTotal") == "50000"


def test_reference_is_built():
    dte = DTE(
        type=DTEType.CREDIT_NOTE,
        folio=9,
        issue_date=dt.date(2026, 6, 8),
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("Anulación", quantity=1, unit_price=10000)],
        references=[
            Reference(33, "101", dt.date(2026, 6, 1), ReferenceCode.CANCEL_DOCUMENT, "Anula")
        ],
    )
    doc = etree.Element("Documento")
    _references(doc, dte)
    assert doc.findtext("Referencia/TpoDocRef") == "33"
    assert doc.findtext("Referencia/FolioRef") == "101"
    assert doc.findtext("Referencia/CodRef") == "1"


# --------------------------------------------------------------------------- #
#  Descuentos por línea y globales (set de certificación 5038170 casos 2 y 4)
# --------------------------------------------------------------------------- #
def _invoice(items, global_discounts=()):
    return DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=200,
        issue_date=dt.date(2026, 8, 27),
        issuer=_issuer(),
        receiver=_receiver(),
        items=items,
        global_discounts=list(global_discounts),
    )


def test_line_discount_pct_reduces_item_amount():
    item = Item("Pañuelo AFECTO", quantity=673, unit_price=5230, discount_pct=9)
    assert item.gross_amount == 3519790
    assert item.discount == 316781  # 9% del bruto
    assert item.amount == 3203009


def test_line_discount_amount_wins_over_pct():
    item = Item("X", quantity=1, unit_price=1000, discount_pct=10, discount_amount=250)
    assert item.discount == 250
    assert item.amount == 750


def test_case_5038170_2_totals():
    """Caso real del set: dos líneas afectas con 9% y 20% de descuento."""
    dte = _invoice(
        [
            Item("Pañuelo AFECTO", quantity=673, unit_price=5230, discount_pct=9),
            Item("ITEM 2 AFECTO", quantity=615, unit_price=4283, discount_pct=20),
        ]
    )
    assert dte.net_amount == 5310245
    assert dte.vat == 1008947
    assert dte.total_amount == 6319192


def test_case_5038170_4_global_discount_only_hits_affected_base():
    """Descuento global 20% sobre los ítems afectos; el exento no se toca."""
    dte = _invoice(
        [
            Item("ITEM 1 AFECTO", quantity=360, unit_price=5226),
            Item("ITEM 2 AFECTO", quantity=152, unit_price=6258),
            Item("ITEM 3 SERVICIO EXENTO", quantity=2, unit_price=6822, exempt=True),
        ],
        [GlobalDiscount(value=20, reason="DESCUENTO GLOBAL ITEMES AFECTOS")],
    )
    assert dte.base_affected == 2832576
    assert dte.net_amount == 2266061
    assert dte.exempt_amount == 13644  # intacto
    assert dte.vat == 430552
    assert dte.total_amount == 2710257


def test_global_surcharge_adds_to_net():
    dte = _invoice(
        [Item("A", quantity=1, unit_price=100000)],
        [GlobalDiscount(value=10, kind="R", value_type="%")],
    )
    assert dte.net_amount == 110000


def test_global_discount_fixed_amount():
    dte = _invoice(
        [Item("A", quantity=1, unit_price=100000)],
        [GlobalDiscount(value=15000, value_type="$")],
    )
    assert dte.net_amount == 85000


def test_multiple_globals_apply_over_original_base_not_cascaded():
    dte = _invoice(
        [Item("A", quantity=1, unit_price=100000)],
        [GlobalDiscount(value=10), GlobalDiscount(value=10)],
    )
    assert dte.net_amount == 80000  # 100000 - 10000 - 10000, no 81000


def test_global_discount_cannot_leave_negative_total():
    dte = _invoice(
        [Item("A", quantity=1, unit_price=1000)],
        [GlobalDiscount(value=2000, value_type="$")],
    )
    with pytest.raises(ValueError, match="negativo"):
        dte.validate()


def test_exempt_global_discount_requires_exempt_lines():
    dte = _invoice(
        [Item("A", quantity=1, unit_price=1000)],
        [GlobalDiscount(value=10, scope="exento")],
    )
    with pytest.raises(ValueError, match="exentas"):
        dte.validate()


def test_global_discount_on_exempt_invoice_hits_exempt_base():
    dte = DTE(
        type=DTEType.EXEMPT_INVOICE,
        folio=201,
        issue_date=dt.date(2026, 8, 27),
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("HORAS PROGRAMADOR", quantity=13, unit_price=7358, unit="Hora")],
        global_discounts=[GlobalDiscount(value=10)],
    )
    assert dte.exempt_amount == 86089  # 95654 - 9565
    assert dte.net_amount == 0
    assert dte.total_amount == 86089


def test_global_discount_rejects_bad_movement_type():
    with pytest.raises(ValueError, match="TpoMov"):
        GlobalDiscount(value=10, kind="Z")


def test_line_discount_is_emitted_in_xml():
    dte = _invoice([Item("Pañuelo", quantity=673, unit_price=5230, discount_pct=9)])
    doc = etree.Element("Documento")
    _items(doc, dte)
    detail = doc.find("Detalle")
    assert detail.findtext("DescuentoPct") == "9"
    assert detail.findtext("DescuentoMonto") == "316781"
    assert detail.findtext("MontoItem") == "3203009"


def test_global_discount_block_follows_xsd_order():
    dte = _invoice(
        [
            Item("A", quantity=1, unit_price=1000),
            Item("B", quantity=1, unit_price=1000, exempt=True),
        ],
        [
            GlobalDiscount(value=20, reason="DESCUENTO GLOBAL ITEMES AFECTOS"),
            GlobalDiscount(value=5, kind="R", value_type="$", scope="exento"),
        ],
    )
    doc = etree.Element("Documento")
    _global_discounts(doc, dte)
    blocks = doc.findall("DscRcgGlobal")
    assert len(blocks) == 2
    assert [c.tag for c in blocks[0]] == [
        "NroLinDR",
        "TpoMov",
        "GlosaDR",
        "TpoValor",
        "ValorDR",
    ]
    assert blocks[0].findtext("NroLinDR") == "1"
    assert blocks[0].findtext("TpoMov") == "D"
    # El ámbito exento sí lleva IndExeDR; el afecto lo omite.
    assert blocks[0].find("IndExeDR") is None
    assert blocks[1].findtext("IndExeDR") == "1"
    assert blocks[1].findtext("TpoValor") == "$"


# --------------------------------------------------------------------------- #
#  Orden y cotas que impone el XSD (regresiones detectadas contra el XSD real)
# --------------------------------------------------------------------------- #
def test_totals_put_net_before_exempt():
    """El XSD ordena MntNeto antes que MntExe; al revés el SII rechaza."""
    dte = _invoice(
        [
            Item("AFECTO", quantity=1, unit_price=10000),
            Item("EXENTO", quantity=1, unit_price=5000, exempt=True),
        ]
    )
    doc = etree.Element("Documento")
    _header(doc, dte)
    totals = doc.find("Encabezado/Totales")
    assert [c.tag for c in totals] == ["MntNeto", "MntExe", "TasaIVA", "IVA", "MntTotal"]


def test_exempt_line_indicator_comes_before_the_item_name():
    """IndExe va antes de NmbItem en la secuencia del Detalle."""
    dte = _invoice([Item("SERVICIO EXENTO", quantity=1, unit_price=5000, exempt=True)])
    doc = etree.Element("Documento")
    _items(doc, dte)
    assert [c.tag for c in doc.find("Detalle")][:3] == ["NroLinDet", "IndExe", "NmbItem"]


def test_zero_unit_price_omits_prcitem():
    """PrcItem es Dec12_6Type (mínimo 0.000001): en cero se omite."""
    dte = _invoice([Item("ANULA", quantity=1, unit_price=0)])
    doc = etree.Element("Documento")
    _items(doc, dte)
    detail = doc.find("Detalle")
    assert detail.find("PrcItem") is None
    assert detail.findtext("QtyItem") == "1"
    assert detail.findtext("MontoItem") == "0"


def test_zero_quantity_omits_qtyitem():
    dte = _invoice([Item("SIN CANTIDAD", quantity=0, unit_price=1000)])
    doc = etree.Element("Documento")
    _items(doc, dte)
    detail = doc.find("Detalle")
    assert detail.find("QtyItem") is None
    assert detail.findtext("MontoItem") == "0"


# --------------------------------------------------------------------------- #
#  Sucursal del emisor
# --------------------------------------------------------------------------- #
def test_branch_is_emitted_before_the_address():
    """Orden del XSD: Acteco, Sucursal?, CdgSIISucur?, DirOrigen..."""
    issuer = _issuer()
    issuer.branch_name = "CASA MATRIZ"
    issuer.branch_code = 4229993
    dte = _invoice([Item("A", quantity=1, unit_price=1000)])
    dte.issuer = issuer

    doc = etree.Element("Documento")
    _header(doc, dte)
    emisor = doc.find("Encabezado/Emisor")
    assert [c.tag for c in emisor] == [
        "RUTEmisor",
        "RznSoc",
        "GiroEmis",
        "Acteco",
        "Sucursal",
        "CdgSIISucur",
        "DirOrigen",
        "CmnaOrigen",
        "CiudadOrigen",
    ]
    assert emisor.findtext("CdgSIISucur") == "4229993"


def test_branch_is_omitted_when_not_informed():
    doc = etree.Element("Documento")
    _header(doc, _invoice([Item("A", quantity=1, unit_price=1000)]))
    emisor = doc.find("Encabezado/Emisor")
    assert emisor.find("Sucursal") is None
    assert emisor.find("CdgSIISucur") is None


# --------------------------------------------------------------------------- #
#  Totales sin monto afecto
# --------------------------------------------------------------------------- #
def _note_totals(items):
    dte = DTE(
        type=DTEType.CREDIT_NOTE,
        folio=7,
        issue_date=dt.date(2026, 9, 1),
        issuer=_issuer(),
        receiver=_receiver(),
        items=items,
        references=[
            Reference(
                doc_type=34,
                folio="1",
                date=dt.date(2026, 9, 1),
                code=ReferenceCode.CORRECT_AMOUNTS,
                reason="MODIFICA MONTO",
            )
        ],
    )
    doc = etree.Element("Documento")
    _header(doc, dte)
    totals = doc.find("Encabezado/Totales")
    return {etree.QName(node).localname: node.text for node in totals}


def test_a_note_with_only_exempt_amounts_declares_no_vat_rate():
    """Sin monto afecto no hay tasa que declarar: sería inventar un impuesto.

    Es el caso de la nota de crédito que corrige una factura exenta, donde el
    SII pide expresamente que no aparezca el IVA.
    """
    totals = _note_totals([Item("HORAS PROGRAMADOR", quantity=1, unit_price=920, exempt=True)])
    assert totals == {"MntExe": "920", "MntTotal": "920"}


def test_a_note_with_affected_amounts_still_declares_the_rate():
    totals = _note_totals([Item("ITEM AFECTO", quantity=1, unit_price=1000)])
    assert totals["MntNeto"] == "1000"
    assert totals["TasaIVA"] == "19"
    assert totals["IVA"] == "190"


def test_a_zero_value_note_keeps_mntneto():
    """Corrección de texto: no mueve montos, pero MntNeto=0 es lo esperable."""
    totals = _note_totals([Item("CORRIGE GIRO", quantity=1, unit_price=0)])
    assert totals == {"MntNeto": "0", "MntTotal": "0"}
