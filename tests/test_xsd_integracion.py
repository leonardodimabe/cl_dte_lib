"""Valida documentos generados contra los XSD OFICIALES del SII.

Se salta automáticamente si los esquemas no están en schemas/ (no se versionan;
ver schemas/README.md). Cubre los documentos que no requieren CAF para armarse.
"""

import datetime as dt
from pathlib import Path

import pytest

from dte_chile import exchange as ix
from dte_chile.book import BookCover, BookLine, build_book
from dte_chile.validation import Validator

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"

pytestmark = pytest.mark.skipif(
    not (SCHEMAS / "dte" / "DTE_v10.xsd").exists(),
    reason="XSD del SII no presentes en schemas/ (ver schemas/README.md)",
)

_ENVELOPE = ix.ReceivedEnvelope(
    envelope_name="DTE_test.xml",
    set_dte_id="SetDoc",
    digest="QUJDMTIz",
    issuer_rut="76158145-7",
    receiver_rut="77777777-7",
    documents=[
        ix.ReceivedDocument(33, 3027, dt.date(2026, 6, 8), "76158145-7", "77777777-7", 564060)
    ],
)
_TS = dt.datetime(2026, 6, 8, 11, 0, 0)


def test_receipt_acknowledgment_valid_xsd(cert):
    Validator(SCHEMAS).validate(ix.serialize(ix.build_receipt_acknowledgment(_ENVELOPE, cert, _TS)))


def test_result_valid_xsd(cert):
    Validator(SCHEMAS).validate(ix.serialize(ix.build_result_response(_ENVELOPE, cert, _TS)))


def test_receipts_envelope_valid_xsd(cert):
    Validator(SCHEMAS).validate(
        ix.serialize(ix.build_receipts_envelope(_ENVELOPE, cert, _TS, location="Bodega"))
    )


def test_book_valid_xsd(cert):
    cover = BookCover(
        issuer_rut="76158145-7",
        sender_rut="77777777-7",
        period="2026-06",
        lines=[
            BookLine(
                33,
                3027,
                dt.date(2026, 6, 8),
                "17099910-K",
                "Cliente",
                net_amount=474000,
                vat_amount=90060,
                total_amount=564060,
            )
        ],
    )
    Validator(SCHEMAS).validate(ix.serialize(build_book(cover, cert, _TS)))


# --------------------------------------------------------------------------- #
#  Sobre con varios DTE (así se entrega cada set de certificación)
# --------------------------------------------------------------------------- #
def _issuer():
    from dte_chile.models import Issuer

    return Issuer(
        rut="76158145-7",
        business_name="DEMO SPA",
        activity="Venta al por menor de maquinaria",
        economic_activity=471000,
        address="Calle 1",
        commune="Santiago",
        city="Santiago",
    )


def _receiver():
    from dte_chile.models import Receiver

    return Receiver(
        rut="17099910-K",
        business_name="CLIENTE SPA",
        activity="Compra",
        address="Calle 2",
        commune="Providencia",
    )


def test_multi_document_envelope_valid_xsd(cert, caf_factory):
    """Factura con descuentos + nota que la referencia + guía, en UN solo sobre."""
    from dte_chile.document_types import DispatchType, DTEType, ReferenceCode, TransferType
    from dte_chile.envelope import Cover, build_envelope, serialize
    from dte_chile.models import DTE, Driver, GlobalDiscount, Item, Reference, Transport
    from dte_chile.signer import sign_document
    from dte_chile.xml_builder import build_document

    issue_date = dt.date(2026, 11, 3)

    invoice = DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=1,
        issue_date=issue_date,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[
            Item("ITEM 1 AFECTO", quantity=360, unit_price=5226, discount_pct=9),
            Item("ITEM 3 SERVICIO EXENTO", quantity=2, unit_price=6822, exempt=True),
        ],
        global_discounts=[GlobalDiscount(value=20, reason="DESCUENTO GLOBAL")],
    )
    credit_note = DTE(
        type=DTEType.CREDIT_NOTE,
        folio=1,
        issue_date=issue_date,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("ANULA", quantity=1, unit_price=0)],
        references=[
            Reference(
                doc_type=33,
                folio="1",
                date=issue_date,
                code=ReferenceCode.CANCEL_DOCUMENT,
                reason="ANULA FACTURA",
            )
        ],
    )
    guide = DTE(
        type=DTEType.DISPATCH_NOTE,
        folio=1,
        issue_date=issue_date,
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
            dest_address="Calle 2",
            dest_commune="Providencia",
            dest_city="Santiago",
            departure_date=issue_date,
            departure_time=dt.time(8, 30, 0),
            arrival_date=issue_date,
        ),
    )

    documents = [invoice, credit_note, guide]
    signed = [
        sign_document(build_document(d, caf_factory(int(d.type)), _TS), cert) for d in documents
    ]
    cover = Cover(
        issuer_rut="76158145-7",
        sender_rut="77777777-7",
        resolution_date=dt.date(2026, 1, 1),
        subtotals=[(33, 1), (52, 1), (61, 1)],
    )
    xml = serialize(build_envelope(signed, cover, cert, _TS))

    Validator(SCHEMAS).validate(xml)


def test_purchase_invoice_with_retention_valid_xsd(cert, caf_factory):
    """Factura de compra (46) con retención total del IVA — set 5038180."""
    from dte_chile.document_types import DTEType, ReferenceCode
    from dte_chile.envelope import Cover, build_envelope, serialize
    from dte_chile.models import DTE, Item, Reference, Retention
    from dte_chile.signer import sign_document
    from dte_chile.xml_builder import build_document

    issue_date = dt.date(2026, 8, 31)
    # Ojo: en la factura de compra el receptor es el PROVEEDOR, no el cliente.
    purchase = DTE(
        type=DTEType.PURCHASE_INVOICE,
        folio=1,
        issue_date=issue_date,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[
            Item("Producto 1", quantity=709, unit_price=5610),
            Item("Producto 2", quantity=31, unit_price=3045),
        ],
        retentions=[Retention()],
    )
    credit_note = DTE(
        type=DTEType.CREDIT_NOTE,
        folio=1,
        issue_date=issue_date,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[
            Item("Producto 1", quantity=236, unit_price=5610),
            Item("Producto 2", quantity=10, unit_price=3045),
        ],
        references=[
            Reference(
                doc_type=46,
                folio="1",
                date=issue_date,
                code=ReferenceCode.CORRECT_AMOUNTS,
                reason="DEVOLUCION DE MERCADERIA ITEMS 1 Y 2",
            )
        ],
        retentions=[Retention()],
    )
    documents = [purchase, credit_note]
    signed = [
        sign_document(build_document(d, caf_factory(int(d.type)), _TS), cert) for d in documents
    ]
    cover = Cover(
        issuer_rut="76158145-7",
        sender_rut="77777777-7",
        resolution_date=dt.date(2026, 1, 1),
        subtotals=[(46, 1), (61, 1)],
    )
    Validator(SCHEMAS).validate(serialize(build_envelope(signed, cover, cert, _TS)))
