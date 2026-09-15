"""Liquidación Factura Electrónica (43) — set de certificación 5038178.

Lo distintivo del 43: raíz ``<Liquidacion>`` en vez de ``<Documento>``, cada
línea declara qué tipo de documento liquida, los montos admiten negativos, y la
comisión del mandatario se RESTA del total.
"""

import datetime as dt
from pathlib import Path

import pytest
from lxml import etree

from dte_chile.document_types import DTEType
from dte_chile.envelope import Cover, build_envelope
from dte_chile.envelope import serialize as serialize_envelope
from dte_chile.models import Issuer, Receiver, Reference
from dte_chile.settlement import (
    MAX_COMMISSIONS,
    OTHER_CHARGE,
    Commission,
    Settlement,
    SettlementLine,
    build_settlement,
)
from dte_chile.signer import sign_document
from dte_chile.validation import Validator

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
TS = dt.datetime(2026, 8, 31, 12, 0, 0)
ISSUE_DATE = dt.date(2026, 8, 31)

pytestmark = pytest.mark.skipif(
    not (SCHEMAS / "dte" / "DTE_v10.xsd").exists(), reason="XSD del SII no presentes"
)


def _agent():
    """El mandatario: quien vende por cuenta de otro y emite la liquidación."""
    return Issuer(
        rut="77262159-0",
        business_name="CONSTRUCTORA DIMABE SPA",
        activity="OTRAS ACTIVIDADES ESPECIALIZADAS DE CONSTRUCCION",
        economic_activity=439000,
        address="MONS. FDO. DE BARRIONUEVO #1540 MARIA FERNANDA",
        commune="RANCAGUA",
        city="RANCAGUA",
    )


def _principal():
    """El mandante: por cuya cuenta se vendió."""
    return Receiver(
        rut="17099910-K",
        business_name="MANDANTE EJEMPLO LTDA",
        activity="Comercio al por mayor",
        address="Calle Mandante 456",
        commune="Providencia",
        city="Santiago",
    )


def _line(liquidated_type, name, amount, quantity=None, exempt=False):
    return SettlementLine(liquidated_type, name, amount, quantity=quantity, exempt=exempt)


def _settlement(lines, commissions=(), folio=1, **kw):
    return Settlement(
        folio=folio,
        issue_date=ISSUE_DATE,
        issuer=_agent(),
        receiver=_principal(),
        lines=lines,
        commissions=list(commissions),
        **kw,
    )


def _case_1():
    return _settlement(
        [
            _line("33", "NETO FACTURAS", 180269, 4),
            _line("33", "EXENTO FACTURAS", 50865, 3, exempt=True),
            _line("33", "NETO FACTURAS ELECTRONICAS", 35540, 14),
            _line("33", "EXENTO FACTURAS ELECTRONICAS", 33838, 10, exempt=True),
        ]
    )


def _case_2():
    """Trae notas de crédito en negativo y boletas."""
    return _settlement(
        [
            _line("33", "NETO FACTURA ELECTRONICA 4254", 19972, 1),
            _line("33", "EXENTO FACTURA ELECTRONICA 4254", 13567, 1, exempt=True),
            _line("33", "NETO FACTURA ELECTRONICA 4768", 168315, 1),
            _line("33", "EXENTO FACTURA ELECTRONICA 4768", 101584, 1, exempt=True),
            _line("61", "NETO NOTA DE CREDITO 328", -20536, 1),
            _line("61", "EXENTO NOTA DE CREDITO 328", -12063, 1, exempt=True),
            _line("39", "BOLETAS", 1604804, 2129),
        ]
    )


def _case_3():
    """Trae comisiones."""
    return _settlement(
        [
            _line("33", "NETO FACTURA ELECTRONICA 1515", 103648, 1),
            _line("33", "NETO FACTURAS ELECTRONICAS", 45578, 78),
            _line("33", "EXENTO FACTURAS ELECTRONICAS", 37094, 14, exempt=True),
        ],
        [
            Commission("NETO COMISION FIJA", 866),
            Commission("NETO COMISION VARIABLE", 2279),
        ],
    )


def _case_4():
    """Liquida una liquidación anterior, en negativo, y una comisión negativa."""
    return _settlement(
        [
            _line("33", "NETO ANTICIPO FACTURACION", 550000, 78),
            _line("33", "NETO FACTURAS", 98625, 14),
            _line("33", "EXENTO FACTURAS", 208950, 15, exempt=True),
            _line("33", "NETO FACTURAS ELECTRONICAS", 34828, 12),
            _line("33", "EXENTO FACTURAS ELECTRONICAS", 402028, 4, exempt=True),
            _line("61", "NETO NOTA DE CREDITO 1981", -31286, 1),
            _line("43", "NETO LIQUIDACION FACTURA ELECTRONICA 4554", -43935, 1),
            _line("43", "EXENTO LIQUIDACION FACTURA ELECTRONICA 4554", -44226, 1, exempt=True),
        ],
        [
            Commission("NETO COMISION CONSIGNACION", 630),
            Commission("NETO COMISIONES LIQ FACT ELECT 4554", -2197),
        ],
    )


# --------------------------------------------------------------------------- #
#  Totales
# --------------------------------------------------------------------------- #
def test_case_1_totals():
    s = _case_1()
    assert s.net_amount == 215809
    assert s.exempt_amount == 84703
    assert s.vat == 41004
    assert s.total_amount == 341516


def test_case_2_negative_lines_subtract():
    """Las notas de crédito entran restando, no sumando."""
    s = _case_2()
    assert s.net_amount == 19972 + 168315 - 20536 + 1604804
    assert s.exempt_amount == 13567 + 101584 - 12063
    assert s.total_amount == s.net_amount + s.exempt_amount + s.vat


def test_case_3_commission_is_subtracted_from_the_total():
    s = _case_3()
    assert s.commission_net == 3145
    assert s.commission_vat == 598
    assert s.total_amount == 149226 + 37094 + 28353 - 3145 - 598
    assert s.total_amount == 210930


def test_case_4_handles_a_negative_commission():
    """Se devuelve comisión de una liquidación anterior: el neto de comisión baja."""
    s = _case_4()
    assert s.commission_net == 630 - 2197
    # Una comisión negativa AUMENTA lo que se liquida al mandante.
    assert s.total_amount > s.net_amount + s.exempt_amount + s.vat


def test_commission_vat_is_the_rate_over_the_total_net():
    """Validación 38 del SII, literal: «Para las liquidaciones factura, el IVA
    de las Comisiones debe ser igual a la tasa del IVA (19%) por el Valor Neto
    de las Comisiones».

    No es lo mismo que sumar el IVA de cada comisión: 630 y -2197 dan 120 y
    -417 —bien redondeados por separado— que suman -297, mientras la tasa sobre
    el neto total (-1567) da -297,73, o sea -298. El SII lo reportó en el libro
    de ventas del set 5038178: «Reparo en Calculo de [ValComIVA] T:[43]-F:[4]».
    """
    s = _case_4()
    assert s.commission_net == -1567
    assert s.commission_vat == -298
    # Y no el -297 de sumar los redondeos de cada comisión.
    assert sum(c.vat for c in s.commissions) == -297


def test_commission_vat_can_be_given_explicitly():
    """Si el llamador lo fija a mano manda él: declara algo que no sale de la tasa."""
    s = _settlement([_line("33", "A", 1000)], [Commission("COMISION", 1000, vat_amount=123)])
    assert s.commission_vat == 123


# --------------------------------------------------------------------------- #
#  Validación
# --------------------------------------------------------------------------- #
def test_liquidated_type_is_required_and_short():
    with pytest.raises(ValueError, match="TpoDocLiq"):
        SettlementLine("", "A", 1000)
    with pytest.raises(ValueError, match="TpoDocLiq"):
        SettlementLine("3333", "A", 1000)


def test_settlement_needs_lines():
    with pytest.raises(ValueError, match="al menos una línea"):
        _settlement([]).validate()


def test_commission_kind_is_validated():
    with pytest.raises(ValueError, match="TipoMovim"):
        Commission("X", 100, kind="Z")


def test_commission_limit_is_enforced():
    commissions = [Commission(f"C{i}", 100) for i in range(MAX_COMMISSIONS + 1)]
    with pytest.raises(ValueError, match="20 bloques"):
        _settlement([_line("33", "A", 1000)], commissions).validate()


# --------------------------------------------------------------------------- #
#  XML
# --------------------------------------------------------------------------- #
def test_root_is_liquidacion_not_documento(cert, caf_factory):
    root = build_settlement(_case_1(), caf_factory(43), TS)
    assert root.tag == "Liquidacion"
    assert root.get("ID") == "F1T43"


def test_each_line_declares_what_it_liquidates(cert, caf_factory):
    root = build_settlement(_case_2(), caf_factory(43), TS)
    details = root.findall("Detalle")
    assert [d.findtext("TpoDocLiq") for d in details] == [
        "33",
        "33",
        "33",
        "33",
        "61",
        "61",
        "39",
    ]
    # TpoDocLiq va antes del nombre, según el orden del XSD.
    assert [c.tag for c in details[0]][:3] == ["NroLinDet", "TpoDocLiq", "NmbItem"]


def test_negative_amounts_survive_to_the_xml(cert, caf_factory):
    root = build_settlement(_case_2(), caf_factory(43), TS)
    amounts = [d.findtext("MontoItem") for d in root.findall("Detalle")]
    assert "-20536" in amounts
    assert "-12063" in amounts


def test_commissions_block_follows_xsd_order(cert, caf_factory):
    root = build_settlement(_case_3(), caf_factory(43), TS)
    blocks = root.findall("Comisiones")
    assert len(blocks) == 2
    assert [c.tag for c in blocks[0]] == [
        "NroLinCom",
        "TipoMovim",
        "Glosa",
        "ValComNeto",
        "ValComExe",
        "ValComIVA",
    ]
    assert blocks[0].findtext("TipoMovim") == "C"
    assert blocks[0].findtext("ValComNeto") == "866"


def test_other_charge_uses_its_own_movement_type(cert, caf_factory):
    settlement = _settlement(
        [_line("33", "A", 1000)], [Commission("FLETE", 500, kind=OTHER_CHARGE)]
    )
    root = build_settlement(settlement, caf_factory(43), TS)
    assert root.find("Comisiones").findtext("TipoMovim") == "O"


def test_totals_aggregate_the_commissions(cert, caf_factory):
    root = build_settlement(_case_3(), caf_factory(43), TS)
    totals = root.find("Encabezado/Totales")
    assert [c.tag for c in totals] == [
        "MntNeto",
        "MntExe",
        "TasaIVA",
        "IVA",
        "Comisiones",
        "MntTotal",
    ]
    commissions = totals.find("Comisiones")
    assert commissions.findtext("ValComNeto") == "3145"
    assert commissions.findtext("ValComIVA") == "598"


def test_no_commissions_block_when_there_are_none(cert, caf_factory):
    root = build_settlement(_case_1(), caf_factory(43), TS)
    assert root.find("Comisiones") is None
    assert root.find("Encabezado/Totales/Comisiones") is None


def test_reference_is_emitted(cert, caf_factory):
    settlement = _case_1()
    settlement.references = [
        Reference(doc_type=43, folio="4554", date=ISSUE_DATE, reason="LIQUIDACION ANTERIOR")
    ]
    root = build_settlement(settlement, caf_factory(43), TS)
    assert root.find("Referencia").findtext("FolioRef") == "4554"


# --------------------------------------------------------------------------- #
#  XSD oficial
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("build_case", [_case_1, _case_2, _case_3, _case_4])
def test_set_cases_validate_against_official_xsd(build_case, cert, caf_factory):
    settlement = build_case()
    signed = sign_document(build_settlement(settlement, caf_factory(43), TS), cert)
    cover = Cover(
        issuer_rut="77262159-0",
        sender_rut="12291733-9",
        resolution_date=dt.date(2026, 1, 1),
        subtotals=[(43, 1)],
    )
    Validator(SCHEMAS).validate(serialize_envelope(build_envelope([signed], cover, cert, TS)))


def test_settlement_type_label():
    assert DTEType.SETTLEMENT_INVOICE == 43
    assert DTEType.SETTLEMENT_INVOICE.label == "Liquidación Factura Electrónica"


def test_signature_verifies(cert, caf_factory):
    from dte_chile import signer

    signed = sign_document(build_settlement(_case_1(), caf_factory(43), TS), cert)
    cover = Cover("77262159-0", "12291733-9", dt.date(2026, 1, 1), subtotals=[(43, 1)])
    xml = serialize_envelope(build_envelope([signed], cover, cert, TS))
    results = signer.verify_signatures(etree.fromstring(xml))
    assert results and all(results)
