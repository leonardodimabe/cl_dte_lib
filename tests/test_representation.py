"""Tests de la representación impresa y el PDF417 (sin timbrar de verdad)."""

import datetime as dt

from lxml import etree

from dte_chile import representation as rep
from dte_chile.document_types import DTEType
from dte_chile.models import DTE, Issuer, Item, Receiver


def _document_with_ted():
    """Documento mínimo con un <TED> de juguete (no firmado)."""
    doc = etree.Element("Documento", ID="F1T33")
    ted = etree.SubElement(doc, "TED", version="1.0")
    dd = etree.SubElement(ted, "DD")
    etree.SubElement(dd, "RE").text = "76158145-7"
    etree.SubElement(dd, "F").text = "1"
    frmt = etree.SubElement(ted, "FRMT", algoritmo="SHA1withRSA")
    frmt.text = "ZmFrZQ=="
    return doc


def _dte():
    return DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=1,
        issue_date=dt.date(2026, 6, 8),
        issuer=Issuer(
            "76158145-7",
            "MUNOZ Y MADARIAGA LIMITADA",
            "Venta",
            479100,
            "Av. Siempre Viva 742",
            "Santiago",
        ),
        receiver=Receiver(
            "17099910-K", "Cliente Ejemplo Ltda", "Servicios", "Calle Falsa 123", "Providencia"
        ),
        items=[Item("Notebook 14", 1, 450000), Item("Mouse", 2, 12000)],
    )


def test_pdf417_png_valid():
    png = rep.generate_pdf417_png(b"<TED version='1.0'><DD></DD></TED>")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # firma PNG
    assert len(png) > 100


def test_ted_bytes_without_declaration():
    doc = _document_with_ted()
    raw = rep.ted_bytes(doc)
    assert raw.startswith(b"<TED")
    assert b"<?xml" not in raw
    assert b"<FRMT" in raw


def test_html_has_key_data():
    html = rep.generate_html(
        _dte(),
        _document_with_ted(),
        rep.ResolutionInfo(number=0, date=dt.date(2026, 6, 8)),
    )
    assert "FACTURA ELECTRÓNICA" in html
    assert "N° 1" in html
    assert "76.158.145-7" in html  # RUT con formato
    assert "data:image/png;base64," in html  # timbre embebido
    assert "$564.060" in html  # total con separador de miles
    assert "MUNOZ Y MADARIAGA LIMITADA" in html


def test_html_exempt_hides_vat():
    dte = _dte()
    dte.type = DTEType.EXEMPT_INVOICE
    html = rep.generate_html(
        dte,
        _document_with_ted(),
        rep.ResolutionInfo(number=0, date=dt.date(2026, 6, 8)),
    )
    assert "IVA (19%)" not in html
    assert "Exento" in html
