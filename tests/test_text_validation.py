"""Validación del texto antes de mandarlo al SII.

Los casos son los que llegan solos desde un ERP: comillas tipográficas que mete
Word, guiones largos copiados de la web, y razones sociales más largas de lo que
admite el XSD. El SII los rechaza con mensajes crípticos —o peor, el documento
sale con el nombre cortado— así que se atajan antes.
"""

import datetime as dt

import pytest

from dte_chile.document_types import DTEType
from dte_chile.models import DTE, Issuer, Item, Receiver, Reference
from dte_chile.text import (
    DocumentDataError,
    check,
    sanitize,
    truncate,
    unsupported_characters,
)


def _issuer(**over):
    data = {
        "rut": "77262159-0",
        "business_name": "CONSTRUCTORA DIMABE SPA",
        "activity": "OTRAS ACTIVIDADES ESPECIALIZADAS DE CONSTRUCCION",
        "economic_activity": 439000,
        "address": "MONS. FDO. DE BARRIONUEVO #1540",
        "commune": "RANCAGUA",
        "city": "RANCAGUA",
    }
    data.update(over)
    return Issuer(**data)


def _receiver(**over):
    data = {
        "rut": "17099910-K",
        "business_name": "CLIENTE EJEMPLO LTDA",
        "activity": "Comercio",
        "address": "Calle 2",
        "commune": "Providencia",
    }
    data.update(over)
    return Receiver(**data)


def _invoice(items=None, **over):
    return DTE(
        type=over.pop("type", DTEType.AFFECTED_INVOICE),
        folio=1,
        issue_date=dt.date(2026, 9, 1),
        issuer=over.pop("issuer", None) or _issuer(),
        receiver=over.pop("receiver", None) or _receiver(),
        items=items or [Item("Producto", quantity=1, unit_price=1000)],
        **over,
    )


# --------------------------------------------------------------------------- #
#  Caracteres que ISO-8859-1 no puede representar
# --------------------------------------------------------------------------- #
def test_accented_spanish_is_fine():
    """Las tildes y la ñ SÍ existen en ISO-8859-1: no deben molestar."""
    assert unsupported_characters("Ñuñoa, Peñalolén, Curicó") == []
    assert check("x", "MUÑOZ VERGARA S.A.") == []


@pytest.mark.parametrize(
    ("char", "descripcion"),
    [
        ("’", "apóstrofo tipográfico de Word"),
        ("“", "comilla doble tipográfica"),
        ("—", "guion largo"),
        ("€", "signo euro"),
        ("•", "viñeta"),
        ("\U0001f600", "emoji"),
    ],
)
def test_typographic_characters_are_detected(char, descripcion):
    assert unsupported_characters(f"ABC{char}DEF") == [char]


def test_problem_names_the_offending_character():
    problems = check("Emisor.RznSoc", "COMERCIAL O’HIGGINS LTDA", "RznSoc")
    assert len(problems) == 1
    message = str(problems[0])
    assert "Emisor.RznSoc" in message
    assert "ISO-8859-1" in message
    assert "U+2019" in message  # dice exactamente cuál es


def test_control_characters_are_rejected():
    problems = check("Detalle[1].NmbItem", "Producto\x00con nulo", "NmbItem")
    assert any("control" in p.problem for p in problems)


def test_tabs_and_newlines_are_tolerated():
    assert check("x", "linea1\nlinea2\ttab") == []


# --------------------------------------------------------------------------- #
#  Largos
# --------------------------------------------------------------------------- #
def test_over_length_is_reported_with_the_limit_and_the_actual_size():
    problems = check("Emisor.RznSoc", "X" * 120, "RznSoc")
    assert len(problems) == 1
    assert "máximo de 100" in problems[0].problem
    assert "tiene 120" in problems[0].problem


def test_limits_come_from_the_official_xsd():
    assert check("x", "A" * 80, "NmbItem") == []  # justo en el límite
    assert check("x", "A" * 81, "NmbItem")  # uno más, falla
    assert check("x", "AAAA", "UnmdItem") == []
    assert check("x", "AAAAA", "UnmdItem")


def test_unknown_tag_has_no_length_limit():
    assert check("x", "A" * 5000, "TagInventado") == []


def test_empty_values_are_not_a_problem():
    assert check("x", "", "RznSoc") == []
    assert check("x", None, "RznSoc") == []


# --------------------------------------------------------------------------- #
#  Validación del documento completo
# --------------------------------------------------------------------------- #
def test_clean_document_validates():
    _invoice().validate()


def test_document_reports_every_problem_at_once():
    """Lo importante: no fallar en el primero y obligar a reintentar."""
    dte = _invoice(
        items=[Item("Producto — especial", quantity=1, unit_price=1000)],
        issuer=_issuer(business_name="COMERCIAL O’HIGGINS " + "X" * 100),
        receiver=_receiver(activity="G" * 60),
    )
    with pytest.raises(DocumentDataError) as ex:
        dte.validate()

    problems = ex.value.problems
    campos = {p.field for p in problems}
    assert "Emisor.RznSoc" in campos
    assert "Receptor.GiroRecep" in campos
    assert "Detalle[1].NmbItem" in campos
    assert len(problems) >= 4  # razón social: largo Y carácter


def test_error_message_lists_the_fields():
    dte = _invoice(items=[Item("Café — molido", quantity=1, unit_price=1000)])
    with pytest.raises(DocumentDataError, match="Detalle\\[1\\].NmbItem"):
        dte.validate()


def test_item_position_is_reported():
    dte = _invoice(
        items=[
            Item("Bien", quantity=1, unit_price=1000),
            Item("Bien", quantity=1, unit_price=1000),
            Item("Malo •", quantity=1, unit_price=1000),
        ]
    )
    with pytest.raises(DocumentDataError) as ex:
        dte.validate()
    assert ex.value.problems[0].field == "Detalle[3].NmbItem"


def test_reference_reason_is_checked():
    dte = _invoice(type=DTEType.CREDIT_NOTE)
    dte.references = [Reference(doc_type=33, folio="1", date=dt.date(2026, 9, 1), reason="R" * 100)]
    with pytest.raises(DocumentDataError, match="RazonRef"):
        dte.validate()


def test_transport_fields_are_checked():
    from dte_chile.document_types import TransferType
    from dte_chile.models import Driver, Transport

    dte = _invoice(type=DTEType.DISPATCH_NOTE)
    dte.transfer_type = TransferType.SALE
    dte.transport = Transport(
        plate="PATENTE-DEMASIADO-LARGA",
        driver=Driver(rut="17099910-K", name="N" * 40),
        dest_address="Calle 1",
        dest_commune="Santiago",
    )
    with pytest.raises(DocumentDataError) as ex:
        dte.validate()
    campos = {p.field for p in ex.value.problems}
    assert "Transporte.Patente" in campos
    assert "Transporte.NombreChofer" in campos


# --------------------------------------------------------------------------- #
#  Saneado
# --------------------------------------------------------------------------- #
def test_sanitize_translates_office_characters():
    assert sanitize("O’HIGGINS") == "O'HIGGINS"
    assert sanitize("Producto — especial") == "Producto - especial"
    assert sanitize("“Comillas”") == '"Comillas"'
    assert sanitize("Uno…") == "Uno..."
    assert sanitize("espacio duro") == "espacio duro"


def test_sanitize_keeps_valid_spanish():
    assert sanitize("Ñuñoa Curicó") == "Ñuñoa Curicó"


def test_sanitize_strips_what_has_no_equivalent():
    limpio = sanitize("Producto \U0001f600 bueno")
    assert unsupported_characters(limpio) == []
    assert "Producto" in limpio and "bueno" in limpio


def test_sanitized_text_always_passes_the_check():
    sucio = "O’HIGGINS — café • \U0001f600 €"
    assert check("x", sanitize(sucio)) == []


def test_truncate_uses_the_official_limit():
    assert len(truncate("X" * 200, "RznSoc")) == 100
    assert truncate("X" * 200, "TagInventado") == "X" * 200


# --------------------------------------------------------------------------- #
#  Liquidación factura y exportación
# --------------------------------------------------------------------------- #
def test_settlement_validates_its_own_fields():
    from dte_chile.settlement import Commission, Settlement, SettlementLine

    settlement = Settlement(
        folio=1,
        issue_date=dt.date(2026, 9, 1),
        issuer=_issuer(),
        receiver=_receiver(),
        lines=[SettlementLine("33", "NETO FACTURAS — varias", 1000, quantity=1)],
        commissions=[Commission("COMISIÓN " + "C" * 80, 500)],
    )
    with pytest.raises(DocumentDataError) as ex:
        settlement.validate()
    campos = {p.field for p in ex.value.problems}
    assert "Detalle[1].NmbItem" in campos  # el guion largo
    assert "Comisiones[1].Glosa" in campos  # excede 60


def test_clean_settlement_validates():
    from dte_chile.settlement import Settlement, SettlementLine

    Settlement(
        folio=1,
        issue_date=dt.date(2026, 9, 1),
        issuer=_issuer(),
        receiver=_receiver(),
        lines=[SettlementLine("33", "NETO FACTURAS", 1000, quantity=1)],
    ).validate()


def test_export_validates_the_customs_free_text():
    from decimal import Decimal

    from dte_chile.export_invoice import Customs, ExportDocument, ExportItem, PackageGroup

    document = ExportDocument(
        type=DTEType.EXPORT_INVOICE,
        folio=1,
        issue_date=dt.date(2026, 9, 1),
        issuer=_issuer(),
        receiver=_receiver(),
        currency="DOLAR USA",
        items=[ExportItem("CHATARRA", quantity=Decimal(1), unit_price=Decimal(100))],
        customs=Customs(
            transport_name="N" * 50,  # admite 40
            booking="B" * 30,  # admite 20
            packages=[PackageGroup(kind_code=13, container_id="C" * 40)],  # admite 25
        ),
    )
    with pytest.raises(DocumentDataError) as ex:
        document.validate()
    campos = {p.field for p in ex.value.problems}
    assert "Aduana.NombreTransp" in campos
    assert "Aduana.Booking" in campos
    assert "Aduana.TipoBultos[1].IdContainer" in campos


def test_clean_export_validates():
    from decimal import Decimal

    from dte_chile.export_invoice import ExportDocument, ExportItem

    ExportDocument(
        type=DTEType.EXPORT_INVOICE,
        folio=1,
        issue_date=dt.date(2026, 9, 1),
        issuer=_issuer(),
        receiver=_receiver(),
        currency="DOLAR USA",
        items=[ExportItem("CHATARRA DE ALUMINIO", quantity=Decimal(1), unit_price=Decimal(100))],
    ).validate()
