"""Guía de despacho (52) y los campos de transporte de la Res. Ex. N°154.

La Res. Ex. N°154 (2025), prorrogada por la Res. Ex. N°52, rige desde el
2026-11-01: suma PatenteCarro/FchSalida/HraSalida/FchLlegada y vuelve
obligatorios patente, transportista, chofer y destino en todo documento que
acompaña traslado de bienes.
"""

import datetime as dt

import pytest
from lxml import etree

from dte_chile.document_types import DispatchType, DTEType, TransferType
from dte_chile.models import DTE, Driver, Issuer, Item, Receiver, Transport
from dte_chile.xml_builder import _header

BEFORE = dt.date(2026, 10, 31)  # víspera de la entrada en vigencia
AFTER = dt.date(2026, 11, 1)  # ya rige la Res. 154


def _issuer():
    return Issuer(
        rut="76192083-9",
        business_name="Industrias Ejemplo SpA",
        activity="Fabricacion de maquinaria",
        economic_activity=281900,
        address="Camino Melipilla 1234",
        commune="Maipu",
        city="Santiago",
    )


def _receiver(rut="17099910-K"):
    return Receiver(
        rut=rut,
        business_name="Cliente Ejemplo Ltda",
        activity="Comercio",
        address="Av. Cliente 100",
        commune="Providencia",
    )


def _full_transport():
    return Transport(
        plate="ABCD12",
        trailer_plate="WXYZ99",
        carrier_rut="76192083-9",
        driver=Driver(rut="17099910-K", name="Juan Perez Soto"),
        dest_address="Av. Cliente 100",
        dest_commune="Providencia",
        dest_city="Santiago",
        departure_date=AFTER,
        departure_time=dt.time(8, 30, 0),
        arrival_date=AFTER,
    )


# Centinela: distingue "no me pasaron transporte" de "pásalo en None a propósito".
_DEFAULT = object()


def _guide(
    *, issue_date=AFTER, receiver=None, transport=_DEFAULT, transfer=TransferType.SALE, **kw
):
    return DTE(
        type=DTEType.DISPATCH_NOTE,
        folio=10,
        issue_date=issue_date,
        issuer=_issuer(),
        receiver=receiver or _receiver(),
        items=[Item("ITEM 1", quantity=290, unit_price=6055)],
        transfer_type=transfer,
        transport=_full_transport() if transport is _DEFAULT else transport,
        **kw,
    )


# --------------------------------------------------------------------------- #
#  Dominio
# --------------------------------------------------------------------------- #
def test_dispatch_note_is_not_a_note_nor_exempt():
    assert DTEType.DISPATCH_NOTE.is_dispatch_note
    assert not DTEType.DISPATCH_NOTE.is_note
    assert not DTEType.DISPATCH_NOTE.is_exempt


def test_dispatch_note_requires_transfer_type():
    with pytest.raises(ValueError, match="tipo de traslado"):
        _guide(transfer=None).validate()


def test_transfer_type_rejected_on_non_dispatch_document():
    dte = DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=1,
        issue_date=AFTER,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("A", quantity=1, unit_price=1000)],
        transfer_type=TransferType.SALE,
    )
    with pytest.raises(ValueError, match="guías de despacho"):
        dte.validate()


def test_internal_transfer_requires_receiver_equal_to_issuer():
    """Caso 5038173-1: en traslado interno el receptor es el propio emisor."""
    with pytest.raises(ValueError, match="traslado interno"):
        _guide(transfer=TransferType.INTERNAL).validate()

    _guide(transfer=TransferType.INTERNAL, receiver=_receiver(rut="76192083-9")).validate()


def test_internal_transfer_without_prices_totals_zero():
    """El set no da precios para el traslado interno: los totales quedan en cero."""
    guide = _guide(transfer=TransferType.INTERNAL, receiver=_receiver(rut="76192083-9"))
    guide.items = [Item("ITEM 1", quantity=74, unit_price=0)]
    guide.validate()
    assert guide.total_amount == 0


def test_case_5038173_2_totals():
    guide = _guide(dispatch_type=DispatchType.ISSUER_TO_CUSTOMER)
    guide.items = [
        Item("ITEM 1", quantity=290, unit_price=6055),
        Item("ITEM 2", quantity=559, unit_price=1485),
    ]
    assert guide.net_amount == 2586065
    assert guide.vat == 491352
    assert guide.total_amount == 3077417


def test_case_5038173_3_totals():
    guide = _guide(dispatch_type=DispatchType.RECEIVER)
    guide.items = [
        Item("ITEM 1", quantity=152, unit_price=1780),
        Item("ITEM 2", quantity=350, unit_price=4743),
    ]
    assert guide.net_amount == 1930610
    assert guide.total_amount == 2297426


# --------------------------------------------------------------------------- #
#  Res. Ex. N°154
# --------------------------------------------------------------------------- #
def test_res154_fields_not_required_before_effective_date():
    """Un documento anterior al 2026-11-01 sigue siendo válido sin transporte."""
    _guide(issue_date=BEFORE, transport=None).validate()


def test_res154_fields_required_from_effective_date():
    with pytest.raises(ValueError, match="Res. Ex. N°154"):
        _guide(issue_date=AFTER, transport=None).validate()


def test_res154_error_names_the_missing_fields():
    incomplete = _full_transport()
    incomplete.plate = ""
    incomplete.arrival_date = None
    with pytest.raises(ValueError) as ex:
        _guide(transport=incomplete).validate()
    assert "patente del vehículo" in str(ex.value)
    assert "fecha de llegada" in str(ex.value)
    assert "hora de salida" not in str(ex.value)  # ese sí venía informado


def test_trailer_plate_is_optional_under_res154():
    """PatenteCarro se agregó por la Res. 154, pero sólo aplica si hay remolque."""
    transport = _full_transport()
    transport.trailer_plate = ""
    _guide(transport=transport).validate()


def test_invoice_accompanying_goods_also_needs_res154_fields():
    """La Res. 154 alcanza a la factura que ampara el traslado, no sólo a la guía."""
    invoice = DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=1,
        issue_date=AFTER,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("A", quantity=1, unit_price=1000)],
        dispatch_type=DispatchType.ISSUER_TO_CUSTOMER,
    )
    assert invoice.accompanies_goods
    with pytest.raises(ValueError, match="Res. Ex. N°154"):
        invoice.validate()

    invoice.transport = _full_transport()
    invoice.validate()


# --------------------------------------------------------------------------- #
#  XML
# --------------------------------------------------------------------------- #
def test_iddoc_carries_dispatch_and_transfer_type():
    guide = _guide(dispatch_type=DispatchType.ISSUER_TO_CUSTOMER)
    document = etree.Element("Documento")
    _header(document, guide)
    id_doc = document.find("Encabezado/IdDoc")
    assert [c.tag for c in id_doc] == ["TipoDTE", "Folio", "FchEmis", "TipoDespacho", "IndTraslado"]
    assert id_doc.findtext("TipoDTE") == "52"
    assert id_doc.findtext("TipoDespacho") == "2"
    assert id_doc.findtext("IndTraslado") == "1"


def test_transport_block_follows_xsd_order():
    """FchSalida/HraSalida/FchLlegada van al final, después de donde iría Aduana."""
    document = etree.Element("Documento")
    _header(document, _guide())
    transport = document.find("Encabezado/Transporte")
    assert [c.tag for c in transport] == [
        "Patente",
        "PatenteCarro",
        "RUTTrans",
        "Chofer",
        "DirDest",
        "CmnaDest",
        "CiudadDest",
        "FchSalida",
        "HraSalida",
        "FchLlegada",
    ]
    assert transport.findtext("Chofer/RUTChofer") == "17099910-K"
    assert transport.findtext("Chofer/NombreChofer") == "Juan Perez Soto"
    assert transport.findtext("HraSalida") == "08:30:00"


def test_transport_is_placed_between_receptor_and_totales():
    document = etree.Element("Documento")
    _header(document, _guide())
    assert [c.tag for c in document.find("Encabezado")] == [
        "IdDoc",
        "Emisor",
        "Receptor",
        "Transporte",
        "Totales",
    ]


def test_no_transport_block_when_absent():
    document = etree.Element("Documento")
    _header(document, _guide(issue_date=BEFORE, transport=None))
    assert document.find("Encabezado/Transporte") is None
