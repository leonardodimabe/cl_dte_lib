"""Round-trip: construir un DTE, firmarlo y volver a leerlo desde el XML.

Es lo que permite reimprimir un documento a partir del sobre ya emitido, sin
guardar nada en el servicio.
"""

import datetime as dt
from collections import Counter

import pytest
from lxml import etree

from dte_chile import parser
from dte_chile.document_types import DispatchType, DTEType, ReferenceCode, TransferType
from dte_chile.envelope import Cover, build_envelope
from dte_chile.envelope import serialize as serialize_envelope
from dte_chile.models import (
    DTE,
    Driver,
    GlobalDiscount,
    Issuer,
    Item,
    Receiver,
    Reference,
    Transport,
)
from dte_chile.signer import sign_document
from dte_chile.xml_builder import build_document

TS = dt.datetime(2026, 8, 28, 10, 0, 0)
ISSUE_DATE = dt.date(2026, 11, 3)


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
        city="Santiago",
    )


def _envelope(documents, cert, caf_factory):
    signed = [
        sign_document(build_document(d, caf_factory(int(d.type)), TS), cert) for d in documents
    ]
    cover = Cover(
        issuer_rut="76158145-7",
        sender_rut="77777777-7",
        resolution_date=dt.date(2026, 1, 1),
        subtotals=sorted(Counter(int(d.type) for d in documents).items()),
    )
    return serialize_envelope(build_envelope(signed, cover, cert, TS))


def _invoice_with_everything():
    return DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=4,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[
            Item("ITEM 1 AFECTO", quantity=360, unit_price=5226, discount_pct=9),
            Item("ITEM 2 AFECTO", quantity=152, unit_price=6258),
            Item("ITEM 3 SERVICIO EXENTO", quantity=2, unit_price=6822, exempt=True, unit="Un"),
        ],
        global_discounts=[GlobalDiscount(value=20, reason="DESCUENTO GLOBAL ITEMES AFECTOS")],
    )


def test_round_trip_preserves_totals(cert, caf_factory):
    original = _invoice_with_everything()
    xml = _envelope([original], cert, caf_factory)

    parsed = parser.parse_documents(xml)
    assert len(parsed) == 1
    back = parsed[0].dte

    assert back.folio == original.folio
    assert back.type is original.type
    assert back.issue_date == original.issue_date
    assert back.net_amount == original.net_amount
    assert back.exempt_amount == original.exempt_amount
    assert back.vat == original.vat
    assert back.total_amount == original.total_amount


def test_round_trip_preserves_line_discounts_and_exemption(cert, caf_factory):
    original = _invoice_with_everything()
    back = parser.parse_documents(_envelope([original], cert, caf_factory))[0].dte

    assert back.items[0].discount_pct == 9
    assert back.items[0].discount == original.items[0].discount
    assert back.items[0].amount == original.items[0].amount
    assert back.items[2].exempt is True
    assert back.items[2].unit == "Un"
    assert back.items[1].exempt is False


def test_round_trip_preserves_global_discount(cert, caf_factory):
    original = _invoice_with_everything()
    back = parser.parse_documents(_envelope([original], cert, caf_factory))[0].dte

    discount = back.global_discounts[0]
    assert discount.value == 20
    assert discount.kind == "D"
    assert discount.value_type == "%"
    assert discount.scope == "afecto"
    assert discount.reason == "DESCUENTO GLOBAL ITEMES AFECTOS"


def test_round_trip_preserves_references(cert, caf_factory):
    note = DTE(
        type=DTEType.CREDIT_NOTE,
        folio=1,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("ANULA", quantity=1, unit_price=0)],
        references=[
            Reference(
                doc_type=33,
                folio="4",
                date=ISSUE_DATE,
                code=ReferenceCode.CANCEL_DOCUMENT,
                reason="ANULA FACTURA",
            )
        ],
    )
    back = parser.parse_documents(_envelope([note], cert, caf_factory))[0].dte

    reference = back.references[0]
    assert reference.doc_type == 33
    assert reference.folio == "4"
    assert reference.date == ISSUE_DATE
    assert reference.code is ReferenceCode.CANCEL_DOCUMENT
    assert reference.reason == "ANULA FACTURA"


def test_round_trip_preserves_transport(cert, caf_factory):
    """Los campos de la Res. Ex. N°154 deben volver completos desde el XML."""
    guide = DTE(
        type=DTEType.DISPATCH_NOTE,
        folio=2,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("ITEM 1", quantity=290, unit_price=6055)],
        dispatch_type=DispatchType.ISSUER_TO_CUSTOMER,
        transfer_type=TransferType.SALE,
        transport=Transport(
            plate="ABCD12",
            trailer_plate="WXYZ99",
            carrier_rut="76158145-7",
            driver=Driver(rut="17099910-K", name="Juan Perez Soto"),
            dest_address="Av. Cliente 100",
            dest_commune="Providencia",
            dest_city="Santiago",
            departure_date=ISSUE_DATE,
            departure_time=dt.time(8, 30, 0),
            arrival_date=dt.date(2026, 11, 4),
        ),
    )
    back = parser.parse_documents(_envelope([guide], cert, caf_factory))[0].dte

    assert back.dispatch_type is DispatchType.ISSUER_TO_CUSTOMER
    assert back.transfer_type is TransferType.SALE
    transport = back.transport
    assert transport.plate == "ABCD12"
    assert transport.trailer_plate == "WXYZ99"
    assert transport.carrier_rut.value == "76158145-7"
    assert transport.driver.name == "Juan Perez Soto"
    assert transport.departure_time == dt.time(8, 30, 0)
    assert transport.arrival_date == dt.date(2026, 11, 4)


def test_parses_every_document_of_a_batch(cert, caf_factory):
    """Un sobre de lote trae varios <Documento>: deben salir todos, en orden."""
    documents = [
        _invoice_with_everything(),
        DTE(
            type=DTEType.CREDIT_NOTE,
            folio=1,
            issue_date=ISSUE_DATE,
            issuer=_issuer(),
            receiver=_receiver(),
            items=[Item("ANULA", quantity=1, unit_price=0)],
            references=[Reference(doc_type=33, folio="4", date=ISSUE_DATE, reason="ANULA")],
        ),
    ]
    parsed = parser.parse_documents(_envelope(documents, cert, caf_factory))
    assert [p.dte.type for p in parsed] == [DTEType.AFFECTED_INVOICE, DTEType.CREDIT_NOTE]
    assert [p.dte.folio for p in parsed] == [4, 1]


def test_parsed_element_still_carries_the_original_ted(cert, caf_factory):
    """El TED debe salir del XML tal cual: es lo que firmó el CAF."""
    parsed = parser.parse_documents(_envelope([_invoice_with_everything()], cert, caf_factory))[0]
    ted = [n for n in parsed.element if etree.QName(n).localname == "TED"]
    assert len(ted) == 1


def test_mismatched_totals_are_rejected(cert, caf_factory):
    """Si el detalle no reproduce el MntTotal timbrado, mejor fallar que imprimir."""
    xml = _envelope([_invoice_with_everything()], cert, caf_factory)
    root = etree.fromstring(xml)
    for node in root.iter():
        if etree.QName(node).localname == "MntTotal":
            node.text = "999999"
            break

    with pytest.raises(parser.ParseError, match="no cuadran"):
        parser.parse_documents(root)


def test_xml_without_documents_is_rejected():
    with pytest.raises(parser.ParseError, match="ningún <Documento>"):
        parser.parse_documents(b"<EnvioDTE/>")


def test_round_trip_preserves_retentions(cert, caf_factory):
    """La retención debe volver del XML: sin ella el total releído no cuadra."""
    from dte_chile.models import Retention

    purchase = DTE(
        type=DTEType.PURCHASE_INVOICE,
        folio=1,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[
            Item("Producto 1", quantity=709, unit_price=5610),
            Item("Producto 2", quantity=31, unit_price=3045),
        ],
        retentions=[Retention()],
    )
    back = parser.parse_documents(_envelope([purchase], cert, caf_factory))[0].dte

    assert len(back.retentions) == 1
    assert back.retentions[0].code == 15
    assert back.retentions[0].amount == purchase.vat
    assert back.retained_amount == purchase.retained_amount
    assert back.total_amount == purchase.total_amount == purchase.net_amount


def test_round_trip_preserves_the_branch(cert, caf_factory):
    """La sucursal debe volver del XML para reimprimir el documento igual."""
    invoice = _invoice_with_everything()
    invoice.issuer.branch_name = "CASA MATRIZ"
    invoice.issuer.branch_code = 4229993

    back = parser.parse_documents(_envelope([invoice], cert, caf_factory))[0].dte
    assert back.issuer.branch_name == "CASA MATRIZ"
    assert back.issuer.branch_code == 4229993
