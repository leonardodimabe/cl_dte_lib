"""Representación impresa: descuentos, traslado y ejemplar cedible.

El set de certificación exige que los descuentos —por línea y globales— aparezcan
en el impreso, con separador de miles con punto, y que se adjunte el ejemplar
tributario y el cedible.
"""

import datetime as dt

from lxml import etree

from dte_chile import representation as rep
from dte_chile.document_types import DispatchType, DTEType, TransferType
from dte_chile.models import DTE, Driver, GlobalDiscount, Issuer, Item, Receiver, Transport

RESOLUTION = rep.ResolutionInfo(number=0, date=dt.date(2026, 1, 1))
ISSUE_DATE = dt.date(2026, 11, 3)


def _document_with_ted():
    """Documento mínimo con un <TED> de juguete (no firmado)."""
    doc = etree.Element("Documento", ID="F1T33")
    ted = etree.SubElement(doc, "TED", version="1.0")
    dd = etree.SubElement(ted, "DD")
    etree.SubElement(dd, "RE").text = "76158145-7"
    etree.SubElement(dd, "F").text = "1"
    etree.SubElement(ted, "FRMT", algoritmo="SHA1withRSA").text = "ZmFrZQ=="
    return doc


def _issuer():
    return Issuer(
        rut="76158145-7",
        business_name="DIMABE LIMITADA",
        activity="Fabricacion de maquinaria",
        economic_activity=281900,
        address="Camino Melipilla 1234",
        commune="Maipu",
        city="Santiago",
    )


def _receiver(rut="17099910-K"):
    return Receiver(
        rut=rut,
        business_name="CLIENTE EJEMPLO LTDA",
        activity="Comercio al por mayor",
        address="Av. Cliente 100",
        commune="Providencia",
    )


def _invoice(items, global_discounts=(), doc_type=DTEType.AFFECTED_INVOICE):
    return DTE(
        type=doc_type,
        folio=4,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(),
        items=items,
        global_discounts=list(global_discounts),
    )


def _render(dte, **kwargs):
    return rep.generate_html(dte, _document_with_ted(), RESOLUTION, **kwargs)


def _render_copies(dte):
    return rep.generate_copies(dte, _document_with_ted(), RESOLUTION)


# --------------------------------------------------------------------------- #
#  Descuentos impresos
# --------------------------------------------------------------------------- #
def test_line_discount_is_printed_with_percent_and_amount():
    dte = _invoice([Item("ITEM 1 AFECTO", quantity=360, unit_price=5226, discount_pct=9)])
    html = _render(dte)
    assert "Desc." in html
    assert "9% ($169.322)" in html


def test_flat_line_discount_is_printed_as_amount():
    dte = _invoice([Item("ITEM", quantity=1, unit_price=100000, discount_amount=15000)])
    assert "$15.000" in _render(dte)


def test_discount_column_is_hidden_when_no_line_has_one():
    dte = _invoice([Item("ITEM", quantity=1, unit_price=1000)])
    assert "Desc." not in _render(dte)


def test_global_discount_is_printed_with_its_amount():
    dte = _invoice(
        [
            Item("ITEM 1 AFECTO", quantity=360, unit_price=5226),
            Item("ITEM 2 AFECTO", quantity=152, unit_price=6258),
            Item("ITEM 3 SERVICIO EXENTO", quantity=2, unit_price=6822, exempt=True),
        ],
        [GlobalDiscount(value=20, reason="DESCUENTO GLOBAL ITEMES AFECTOS")],
    )
    html = _render(dte)
    assert "Descuento global 20% — DESCUENTO GLOBAL ITEMES AFECTOS" in html
    assert "-$566.515" in html  # 20% de la base afecta
    assert "$2.710.257" in html  # total del caso 5038170-4


def test_global_surcharge_is_printed_with_a_plus_sign():
    dte = _invoice(
        [Item("ITEM", quantity=1, unit_price=100000)],
        [GlobalDiscount(value=10, kind="R", reason="COMISION")],
    )
    html = _render(dte)
    assert "Recargo global 10%" in html
    assert "+$10.000" in html


def test_figures_use_a_dot_as_thousands_separator():
    """El set lo pide explícitamente."""
    dte = _invoice([Item("ITEM", quantity=2129, unit_price=1604804)])
    html = _render(dte)
    assert "2.129" in html  # cantidad
    assert "$1.604.804" in html  # precio
    assert "76.158.145-7" in html  # RUT


def test_exempt_line_is_flagged():
    dte = _invoice(
        [
            Item("AFECTO", quantity=1, unit_price=1000),
            Item("SERVICIO EXENTO", quantity=1, unit_price=500, exempt=True),
        ]
    )
    assert "EXENTO" in _render(dte)


# --------------------------------------------------------------------------- #
#  Ejemplar cedible
# --------------------------------------------------------------------------- #
def test_tax_copy_has_no_cession_block():
    """Manual de muestras impresas: el tributario va «sin identificación de destino»
    y sin el cuadro de acuse de recibo, que es sólo del cedible."""
    html = _render(_invoice([Item("ITEM", quantity=1, unit_price=1000)]))
    assert "TRIBUTARIO" not in html
    assert "CEDIBLE" not in html
    assert "Ley 19.983" not in html


def test_copies_include_tax_and_transferable():
    html = _render_copies(_invoice([Item("ITEM", quantity=1, unit_price=1000)]))
    assert '<div class="destino">CEDIBLE</div>' in html
    assert "Ley 19.983" in html
    assert html.count('class="doc"') == 2


def test_exempt_invoice_also_gets_a_transferable_copy():
    dte = _invoice(
        [Item("HORAS PROGRAMADOR", quantity=13, unit_price=7358, unit="Hora")],
        doc_type=DTEType.EXEMPT_INVOICE,
    )
    html = _render_copies(dte)
    assert "CEDIBLE" in html
    assert "IVA (19%)" not in html


def test_credit_note_has_no_transferable_copy():
    dte = _invoice([Item("ANULA", quantity=1, unit_price=0)], doc_type=DTEType.CREDIT_NOTE)
    assert rep.is_cedible(dte) is False
    assert _render_copies(dte).count('class="doc"') == 1


# --------------------------------------------------------------------------- #
#  Guía de despacho
# --------------------------------------------------------------------------- #
def _guide(transfer_type=TransferType.SALE, receiver=None, items=None):
    return DTE(
        type=DTEType.DISPATCH_NOTE,
        folio=2,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=receiver or _receiver(),
        items=items or [Item("ITEM 1", quantity=290, unit_price=6055)],
        dispatch_type=DispatchType.ISSUER_TO_CUSTOMER,
        transfer_type=transfer_type,
        transport=Transport(
            plate="ABCD12",
            trailer_plate="WXYZ99",
            carrier_rut="76158145-7",
            driver=Driver(rut="17099910-K", name="Juan Perez Soto"),
            dest_address="Av. Cliente 100",
            dest_commune="Providencia",
            departure_date=ISSUE_DATE,
            departure_time=dt.time(8, 30, 0),
            arrival_date=dt.date(2026, 11, 4),
        ),
    )


def test_guide_prints_the_transport_data_required_by_res154():
    html = _render(_guide())
    assert "Operación constituye venta" in html
    assert "ABCD12" in html
    assert "WXYZ99" in html
    assert "Juan Perez Soto" in html
    assert "03-11-2026 08:30:00" in html  # salida
    assert "04-11-2026" in html  # llegada


def test_sale_guide_gets_a_transferable_copy():
    assert rep.is_cedible(_guide()) is True
    assert _render_copies(_guide()).count('class="doc"') == 2


def test_internal_transfer_guide_has_no_transferable_copy():
    """El set lo dice: si el traslado no constituye venta, el cedible es inoficioso."""
    guide = _guide(
        transfer_type=TransferType.INTERNAL,
        receiver=_receiver(rut="76158145-7"),
        items=[Item("ITEM 1", quantity=74, unit_price=0)],
    )
    assert rep.is_cedible(guide) is False
    html = _render_copies(guide)
    assert html.count('class="doc"') == 1
    assert "Traslados internos" in html


def test_line_without_price_shows_a_dash_instead_of_zero():
    """En la columna de precio; el monto y los totales sí van en $0, que es lo real."""
    guide = _guide(
        transfer_type=TransferType.INTERNAL,
        receiver=_receiver(rut="76158145-7"),
        items=[Item("ITEM 1", quantity=74, unit_price=0)],
    )
    html = _render(guide)
    # Fila: #, nombre, cantidad, precio, monto.
    assert '<td class="r">74</td><td class="r">—</td><td class="r">$0</td>' in html
    assert "<td>Monto Total</td><td class='r'>$0</td>" in html


def test_invoice_without_transfer_has_no_transport_block():
    html = _render(_invoice([Item("ITEM", quantity=1, unit_price=1000)]))
    assert "Traslado" not in html


# --------------------------------------------------------------------------- #
#  Referencias
# --------------------------------------------------------------------------- #
def test_reference_shows_the_document_name_not_just_its_code():
    from dte_chile.document_types import ReferenceCode
    from dte_chile.models import Reference

    dte = _invoice([Item("ANULA", quantity=1, unit_price=0)], doc_type=DTEType.CREDIT_NOTE)
    dte.references = [
        Reference(
            doc_type=33,
            folio="4",
            date=ISSUE_DATE,
            code=ReferenceCode.CANCEL_DOCUMENT,
            reason="ANULA FACTURA",
        )
    ]
    html = _render(dte)
    assert "Factura electrónica N° 4" in html
    assert "03-11-2026" in html
    assert "ANULA FACTURA" in html
