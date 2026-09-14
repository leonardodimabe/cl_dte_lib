"""Libro de Guías de Despacho (LibroGuia) — set de certificación 5038174.

El set pide construir el libro con las guías del set 5038173, marcando el caso 2
como facturado en el período y el caso 3 como anulado.
"""

import datetime as dt

import pytest
from lxml import etree

from dte_chile import signer
from dte_chile.document_types import DTEType, TransferType
from dte_chile.guide_book import (
    GuideBookCover,
    GuideBookLine,
    VoidStatus,
    build_guide_book,
    guide_line,
    serialize,
)
from dte_chile.models import DTE, Issuer, Item, Receiver
from dte_chile.validation import Validator

NS = "http://www.sii.cl/SiiDte"
TS = dt.datetime(2026, 11, 30, 18, 0, 0)


def _cover(lines=None):
    return GuideBookCover(
        issuer_rut="76158145-7",
        sender_rut="12291733-9",
        period="2026-11",
        resolution_date=dt.date(2026, 1, 1),
        notification_folio=5038174,
        lines=lines if lines is not None else _set_lines(),
    )


def _set_lines():
    """Las tres guías del set 5038173, tal como las pide el set del libro."""
    return [
        # Caso 1: traslado interno entre bodegas, sin precios.
        GuideBookLine(
            folio=1,
            date=dt.date(2026, 11, 3),
            transfer_type=TransferType.INTERNAL,
            receiver_rut="76158145-7",
            receiver_name="Industrias Ejemplo SpA",
        ),
        # Caso 2: guía de venta que se facturó dentro del período.
        GuideBookLine(
            folio=2,
            date=dt.date(2026, 11, 4),
            transfer_type=TransferType.SALE,
            receiver_rut="17099910-K",
            receiver_name="Cliente Ejemplo Ltda",
            net_amount=2586065,
            vat_amount=491352,
            total_amount=3077417,
            modified_amount=3077417,
            ref_doc_type=33,
            ref_folio=120,
            ref_date=dt.date(2026, 11, 10),
        ),
        # Caso 3: guía anulada (ya había sido enviada al SII).
        GuideBookLine(folio=3, voided=VoidStatus.AFTER_SENDING),
    ]


def _build(cover=None, cert=None):
    return build_guide_book(cover or _cover(), cert, TS)


# --------------------------------------------------------------------------- #
#  Carátula y resumen
# --------------------------------------------------------------------------- #
def test_cover_follows_xsd_order(cert):
    book = _build(cert=cert)
    cover = book.find(f"{{{NS}}}EnvioLibro/{{{NS}}}Caratula")
    assert [etree.QName(c).localname for c in cover] == [
        "RutEmisorLibro",
        "RutEnvia",
        "PeriodoTributario",
        "FchResol",
        "NroResol",
        "TipoLibro",
        "TipoEnvio",
        "FolioNotificacion",
    ]
    assert cover.findtext(f"{{{NS}}}TipoLibro") == "ESPECIAL"
    assert cover.findtext(f"{{{NS}}}FolioNotificacion") == "5038174"


def test_summary_splits_sales_from_transfers(cert):
    """La venta va en TotGuiaVenta; el traslado interno, en su TotTraslado."""
    book = _build(cert=cert)
    summary = book.find(f"{{{NS}}}EnvioLibro/{{{NS}}}ResumenPeriodo")

    assert summary.findtext(f"{{{NS}}}TotGuiaVenta") == "1"
    assert summary.findtext(f"{{{NS}}}TotMntGuiaVta") == "3077417"
    assert summary.findtext(f"{{{NS}}}TotGuiaAnulada") == "1"
    assert summary.findtext(f"{{{NS}}}TotMntModificado") == "3077417"

    transfers = summary.findall(f"{{{NS}}}TotTraslado")
    assert len(transfers) == 1
    assert transfers[0].findtext(f"{{{NS}}}TpoTraslado") == "5"
    assert transfers[0].findtext(f"{{{NS}}}CantGuia") == "1"
    # Sin precios el traslado interno no informa monto.
    assert transfers[0].find(f"{{{NS}}}MntGuia") is None


def test_void_before_sending_counts_as_annulled_folio(cert):
    """Anulado=1 suma en TotFolAnulado, no en TotGuiaAnulada."""
    lines = [GuideBookLine(folio=9, voided=VoidStatus.BEFORE_SENDING)]
    summary = _build(_cover(lines), cert).find(f"{{{NS}}}EnvioLibro/{{{NS}}}ResumenPeriodo")
    assert summary.findtext(f"{{{NS}}}TotFolAnulado") == "1"
    assert summary.find(f"{{{NS}}}TotGuiaAnulada") is None
    assert summary.findtext(f"{{{NS}}}TotGuiaVenta") == "0"


def test_annulled_guide_excluded_from_sales_totals(cert):
    """Una guía de venta anulada no aporta a TotGuiaVenta ni a su monto."""
    lines = [
        GuideBookLine(
            folio=4,
            date=dt.date(2026, 11, 5),
            transfer_type=TransferType.SALE,
            total_amount=500000,
            voided=VoidStatus.AFTER_SENDING,
        )
    ]
    summary = _build(_cover(lines), cert).find(f"{{{NS}}}EnvioLibro/{{{NS}}}ResumenPeriodo")
    assert summary.findtext(f"{{{NS}}}TotGuiaVenta") == "0"
    assert summary.findtext(f"{{{NS}}}TotMntGuiaVta") == "0"
    assert summary.findtext(f"{{{NS}}}TotGuiaAnulada") == "1"


def test_more_than_six_transfer_groups_is_rejected(cert):
    """El XSD sólo admite seis bloques TotTraslado."""
    lines = [
        GuideBookLine(folio=i, date=dt.date(2026, 11, 1), transfer_type=TransferType(code))
        for i, code in enumerate(range(2, 9), start=1)
    ]
    with pytest.raises(ValueError, match="tipos de traslado"):
        _build(_cover(lines), cert)


# --------------------------------------------------------------------------- #
#  Detalle
# --------------------------------------------------------------------------- #
def test_detail_of_invoiced_guide_carries_reference(cert):
    book = _build(cert=cert)
    details = book.findall(f"{{{NS}}}EnvioLibro/{{{NS}}}Detalle")
    assert len(details) == 3

    sale = details[1]
    assert [etree.QName(c).localname for c in sale] == [
        "Folio",
        "TpoOper",
        "FchDoc",
        "RUTDoc",
        "RznSoc",
        "MntNeto",
        "TasaImp",
        "IVA",
        "MntTotal",
        "MntModificado",
        "TpoDocRef",
        "FolioDocRef",
        "FchDocRef",
    ]
    assert sale.findtext(f"{{{NS}}}TpoDocRef") == "33"
    assert sale.findtext(f"{{{NS}}}FolioDocRef") == "120"


def test_detail_of_annulled_guide_is_only_folio_and_status(cert):
    book = _build(cert=cert)
    annulled = book.findall(f"{{{NS}}}EnvioLibro/{{{NS}}}Detalle")[2]
    assert [etree.QName(c).localname for c in annulled] == ["Folio", "Anulado"]
    assert annulled.findtext(f"{{{NS}}}Anulado") == "2"


def test_internal_transfer_detail_has_no_amounts(cert):
    book = _build(cert=cert)
    internal = book.findall(f"{{{NS}}}EnvioLibro/{{{NS}}}Detalle")[0]
    assert internal.findtext(f"{{{NS}}}TpoOper") == "5"
    assert internal.findtext(f"{{{NS}}}MntTotal") == "0"
    assert internal.find(f"{{{NS}}}MntNeto") is None


# --------------------------------------------------------------------------- #
#  Desde un DTE
# --------------------------------------------------------------------------- #
def test_guide_line_from_dte():
    guide = DTE(
        type=DTEType.DISPATCH_NOTE,
        folio=77,
        issue_date=dt.date(2026, 11, 4),
        issuer=Issuer(
            rut="76158145-7",
            business_name="Industrias Ejemplo SpA",
            activity="Fabricacion",
            economic_activity=281900,
            address="Camino 1",
            commune="Maipu",
        ),
        receiver=Receiver(
            rut="17099910-K",
            business_name="Cliente Ejemplo Ltda",
            activity="Comercio",
            address="Av. 2",
            commune="Providencia",
        ),
        items=[Item("ITEM 1", quantity=290, unit_price=6055)],
        transfer_type=TransferType.SALE,
    )
    line = guide_line(guide)
    assert line.folio == 77
    assert line.transfer_type is TransferType.SALE
    assert line.total_amount == guide.total_amount


def test_guide_line_rejects_non_dispatch_document():
    invoice = DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=1,
        issue_date=dt.date(2026, 11, 4),
        issuer=Issuer(
            rut="76158145-7",
            business_name="X",
            activity="Y",
            economic_activity=1,
            address="A",
            commune="C",
        ),
        receiver=Receiver(
            rut="17099910-K", business_name="X", activity="Y", address="A", commune="C"
        ),
        items=[Item("A", quantity=1, unit_price=1000)],
    )
    with pytest.raises(ValueError, match="no es una guía"):
        guide_line(invoice)


# --------------------------------------------------------------------------- #
#  Firma y XSD
# --------------------------------------------------------------------------- #
def test_book_is_signed_and_signature_verifies(cert):
    # Se reparsea a propósito: la firma debe seguir válida tras el round-trip.
    reparsed = etree.fromstring(serialize(_build(cert=cert)))
    assert signer.verify_signatures(reparsed) == [True]


def test_book_validates_against_official_xsd(cert):
    """Contra el LibroGuia_v10.xsd oficial del SII (schema_lgd.zip)."""
    validator = Validator("schemas")
    if not validator.available("LibroGuia"):
        pytest.skip("Falta el XSD oficial; corre schemas/download_schemas.ps1.")
    validator.validate(serialize(_build(cert=cert)))
