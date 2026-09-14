"""Factura de compra (46) y retenciones — set de certificación 5038180.

La factura de compra la emite el COMPRADOR (cambio de sujeto): el receptor del
documento es el proveedor, y lo normal es que el comprador retenga todo el IVA
(código 15), que entera él al SII. Por eso el total a pagar es el neto.
"""

import datetime as dt

import pytest
from lxml import etree

from dte_chile.document_types import DTEType, ReferenceCode
from dte_chile.models import (
    DTE,
    VAT_RETENTION_TOTAL,
    Issuer,
    Item,
    Receiver,
    Reference,
    Retention,
)
from dte_chile.representation import is_cedible
from dte_chile.xml_builder import _header

ISSUE_DATE = dt.date(2026, 8, 31)


def _buyer():
    return Issuer(
        rut="77262159-0",
        business_name="EMPRESA DE CERTIFICACION SPA",
        activity="Venta al por mayor de maquinaria",
        economic_activity=465900,
        address="Avenida Principal 1234",
        commune="Santiago",
        city="Santiago",
    )


def _supplier():
    return Receiver(
        rut="17099910-K",
        business_name="PROVEEDOR EJEMPLO LTDA",
        activity="Servicios",
        address="Calle Falsa 123",
        commune="Providencia",
    )


# Centinela: el default no puede ser Retention() porque la instancia se
# compartiría entre llamadas (la dataclass es mutable).
_DEFAULT_RETENTION = object()


def _purchase(items=None, retentions=_DEFAULT_RETENTION, doc_type=DTEType.PURCHASE_INVOICE, **kw):
    if retentions is _DEFAULT_RETENTION:
        retentions = (Retention(),)
    return DTE(
        type=doc_type,
        folio=1,
        issue_date=ISSUE_DATE,
        issuer=_buyer(),
        receiver=_supplier(),
        items=items
        or [
            Item("Producto 1", quantity=709, unit_price=5610),
            Item("Producto 2", quantity=31, unit_price=3045),
        ],
        retentions=list(retentions),
        **kw,
    )


# --------------------------------------------------------------------------- #
#  Tipo de documento
# --------------------------------------------------------------------------- #
def test_purchase_invoice_flags():
    assert DTEType.PURCHASE_INVOICE.is_purchase_invoice
    assert not DTEType.PURCHASE_INVOICE.is_note
    assert not DTEType.PURCHASE_INVOICE.is_exempt
    assert not DTEType.PURCHASE_INVOICE.is_dispatch_note
    assert DTEType.PURCHASE_INVOICE.label == "Factura de Compra Electrónica"


def test_purchase_invoice_has_a_transferable_copy():
    """El set exige adjuntar ejemplar cedible de la factura de compra."""
    assert is_cedible(_purchase()) is True


# --------------------------------------------------------------------------- #
#  Totales con retención
# --------------------------------------------------------------------------- #
def test_case_5038180_1_totals():
    dte = _purchase()
    assert dte.net_amount == 4071885
    assert dte.vat == 773658
    assert dte.retained_amount == 773658
    # El IVA retenido lo entera el comprador: al proveedor se le paga el neto.
    assert dte.total_amount == 4071885


def test_retention_defaults_to_the_whole_vat():
    retention = Retention()
    assert retention.code == VAT_RETENTION_TOTAL
    assert retention.amount is None
    assert retention.amount_over(12345) == 12345


def test_partial_retention_uses_its_own_amount():
    dte = _purchase(
        items=[Item("A", quantity=1, unit_price=100000)],
        retentions=[Retention(amount=10000)],
    )
    assert dte.vat == 19000
    assert dte.retained_amount == 10000
    assert dte.total_amount == 100000 + 19000 - 10000


def test_without_retention_total_is_net_plus_vat():
    dte = _purchase(items=[Item("A", quantity=1, unit_price=100000)], retentions=())
    assert dte.retained_amount == 0
    assert dte.total_amount == 119000


def test_credit_note_over_a_purchase_invoice_also_retains():
    """Caso 5038180-2: la NC sobre la factura de compra arrastra la retención."""
    dte = _purchase(
        doc_type=DTEType.CREDIT_NOTE,
        items=[
            Item("Producto 1", quantity=236, unit_price=5610),
            Item("Producto 2", quantity=10, unit_price=3045),
        ],
    )
    dte.references = [
        Reference(
            doc_type=46,
            folio="1",
            date=ISSUE_DATE,
            code=ReferenceCode.CORRECT_AMOUNTS,
            reason="DEVOLUCION DE MERCADERIA ITEMS 1 Y 2",
        )
    ]
    dte.validate()
    assert dte.net_amount == 236 * 5610 + 10 * 3045
    assert dte.retained_amount == dte.vat
    assert dte.total_amount == dte.net_amount


# --------------------------------------------------------------------------- #
#  Validación
# --------------------------------------------------------------------------- #
def test_retention_rejected_on_a_plain_invoice():
    """El SII sólo acepta <ImptoReten> en 46, 56 y 61."""
    dte = _purchase(doc_type=DTEType.AFFECTED_INVOICE)
    with pytest.raises(ValueError, match="no admite retenciones"):
        dte.validate()


def test_retention_cannot_exceed_the_document():
    dte = _purchase(
        items=[Item("A", quantity=1, unit_price=1000)],
        retentions=[Retention(amount=99999)],
    )
    with pytest.raises(ValueError, match="superan el monto"):
        dte.validate()


# --------------------------------------------------------------------------- #
#  XML
# --------------------------------------------------------------------------- #
def test_imptoreten_follows_iva_and_precedes_mnttotal():
    """Orden del XSD: ... TasaIVA, IVA, ImptoReten, MntTotal."""
    document = etree.Element("Documento")
    _header(document, _purchase())
    totals = document.find("Encabezado/Totales")
    assert [c.tag for c in totals] == [
        "MntNeto",
        "TasaIVA",
        "IVA",
        "ImptoReten",
        "MntTotal",
    ]
    retention = totals.find("ImptoReten")
    # La tasa va aunque no se indique: la retención total (15) retiene el IVA
    # entero, así que su tasa ES la del IVA. Sin ella el SII acepta el documento
    # con reparo: «(HED-2-302) Tasa no corresponde [19.00] <> [0.00]».
    assert [c.tag for c in retention] == ["TipoImp", "TasaImp", "MontoImp"]
    assert retention.findtext("TipoImp") == "15"
    assert retention.findtext("TasaImp") == "19"
    assert retention.findtext("MontoImp") == "773658"


def test_rate_is_emitted_when_given():
    document = etree.Element("Documento")
    _header(document, _purchase(retentions=[Retention(rate=19)]))
    retention = document.find("Encabezado/Totales/ImptoReten")
    assert [c.tag for c in retention] == ["TipoImp", "TasaImp", "MontoImp"]
    assert retention.findtext("TasaImp") == "19"


def test_document_type_46_is_emitted():
    document = etree.Element("Documento")
    _header(document, _purchase())
    assert document.findtext("Encabezado/IdDoc/TipoDTE") == "46"


def test_no_imptoreten_block_when_there_is_no_retention():
    document = etree.Element("Documento")
    _header(document, _purchase(retentions=()))
    assert document.find("Encabezado/Totales/ImptoReten") is None


def test_a_zero_retention_is_not_declared():
    """La nota que anula no tiene IVA sobre el cual retener.

    Emitir <ImptoReten> con MontoImp=0 anunciaría un impuesto que el documento
    no tiene.
    """
    dte = DTE(
        type=DTEType.DEBIT_NOTE,
        folio=3,
        issue_date=dt.date(2026, 9, 1),
        issuer=_buyer(),
        receiver=_supplier(),
        items=[Item("ANULA NOTA DE CREDITO", quantity=1, unit_price=0)],
        retentions=[Retention()],
        references=[
            Reference(
                doc_type=61,
                folio="2",
                date=dt.date(2026, 9, 1),
                code=ReferenceCode.CANCEL_DOCUMENT,
                reason="ANULA NOTA DE CREDITO ELECTRONICA",
            )
        ],
    )
    doc = etree.Element("Documento")
    _header(doc, dte)

    assert doc.find("Encabezado/Totales/ImptoReten") is None


def test_a_real_retention_is_still_declared():
    dte = DTE(
        type=DTEType.PURCHASE_INVOICE,
        folio=1,
        issue_date=dt.date(2026, 9, 1),
        issuer=_buyer(),
        receiver=_supplier(),
        items=[Item("Producto 1", quantity=709, unit_price=5610)],
        retentions=[Retention()],
    )
    doc = etree.Element("Documento")
    _header(doc, dte)

    assert doc.findtext("Encabezado/Totales/ImptoReten/MontoImp") == str(dte.vat)
