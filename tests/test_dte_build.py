"""Tests del armado de dominio y XML sin criptografía (no requieren CAF/cert)."""

import datetime as dt

import pytest
from lxml import etree

from dte_chile.document_types import DTEType, ReferenceCode
from dte_chile.models import DTE, Issuer, Item, Receiver, Reference
from dte_chile.xml_builder import _header, _references


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
