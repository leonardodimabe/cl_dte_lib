"""Documentos de exportación (110/111/112) — sets 5038176 y 5038177.

Lo que se verifica: montos decimales sin arrastre de float, totales exentos sin
IVA, el bloque Aduana completo, y el flete y el seguro informados dos veces
—encabezado y recargo global— como exige el set.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from dte_chile import signer
from dte_chile.customs_codes import (
    COUNTRIES,
    MEASURE_UNITS,
    PACKAGE_TYPES,
    PORTS,
    SALE_CLAUSES,
    SALE_MODES,
    TRANSPORT_ROUTES,
    code_for,
)
from dte_chile.document_types import DTEType, ReferenceCode
from dte_chile.envelope import Cover, build_envelope
from dte_chile.envelope import serialize as serialize_envelope
from dte_chile.export_invoice import (
    MAX_PACKAGE_GROUPS,
    Customs,
    ExportDocument,
    ExportItem,
    PackageGroup,
    build_export,
)
from dte_chile.models import GlobalDiscount, Issuer, Receiver, Reference
from dte_chile.signer import sign_document
from dte_chile.validation import Validator

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
TS = dt.datetime(2026, 8, 31, 12, 0, 0)
ISSUE_DATE = dt.date(2026, 8, 31)

pytestmark = pytest.mark.skipif(
    not (SCHEMAS / "dte" / "DTE_v10.xsd").exists(), reason="XSD del SII no presentes"
)


def _exporter():
    return Issuer(
        rut="77262159-0",
        business_name="CONSTRUCTORA DIMABE SPA",
        activity="OTRAS ACTIVIDADES ESPECIALIZADAS DE CONSTRUCCION",
        economic_activity=439000,
        address="MONS. FDO. DE BARRIONUEVO #1540 MARIA FERNANDA",
        commune="RANCAGUA",
        city="RANCAGUA",
    )


def _foreign_buyer():
    # El receptor extranjero se identifica con el RUT genérico 55555555-5.
    return Receiver(
        rut="55555555-5",
        business_name="IMPORTADORA EXTRANJERA SA",
        activity="Importacion",
        address="Calle Extranjera 1",
        commune="Barcelona",
    )


def _document(items, doc_type=DTEType.EXPORT_INVOICE, **kw):
    return ExportDocument(
        type=doc_type,
        folio=kw.pop("folio", 1),
        issue_date=ISSUE_DATE,
        issuer=_exporter(),
        receiver=_foreign_buyer(),
        currency=kw.pop("currency", "DOLAR USA"),
        items=items,
        **kw,
    )


def _case_5038176_1():
    """Chatarra de aluminio en libras esterlinas, con flete y seguro."""
    return _document(
        [ExportItem("CHATARRA DE ALUMINIO", quantity=872, unit_price=177, unit="LT")],
        currency="LIBRA EST",
        customs=Customs(
            sale_mode=code_for(SALE_MODES, "EN CONSIGNACION CON UN MINIMO A FIRME"),
            sale_clause=code_for(SALE_CLAUSES, "CFR"),
            clause_total="4631.13",
            transport_route=code_for(TRANSPORT_ROUTES, "MARITIMA, FLUVIAL Y LACUSTRE"),
            loading_port=code_for(PORTS, "SAN ANTONIO"),
            unloading_port=code_for(PORTS, "BARCELONA"),
            tare_unit=code_for(MEASURE_UNITS, "PAR"),
            gross_weight_unit=code_for(MEASURE_UNITS, "LT"),
            net_weight_unit=code_for(MEASURE_UNITS, "LT"),
            total_packages=87,
            packages=[PackageGroup(kind_code=code_for(PACKAGE_TYPES, "ROLLOS"), quantity=87)],
            freight="3574.57",
            insurance="2759.05",
            receiver_country=code_for(COUNTRIES, "ESPAÑA"),
            destination_country=code_for(COUNTRIES, "ESPAÑA"),
        ),
        global_charges=[
            GlobalDiscount(value=Decimal("3574.57"), kind="R", value_type="$", reason="FLETE"),
            GlobalDiscount(value=Decimal("2759.05"), kind="R", value_type="$", reason="SEGURO"),
        ],
    )


def _case_5038177_2():
    """Dos líneas, una con 5% de descuento, y recargo global por comisiones."""
    return _document(
        [
            ExportItem(
                "CAJAS CIRUELAS TIERNIZADAS SIN CAROZO CALIBRE 60/70",
                quantity=239,
                unit_price=114,
                unit="KN",
                discount_pct=5,
            ),
            ExportItem(
                "CAJAS DE PASAS DE UVA FLAME MORENA SIN SEMILLA MEDIANAS",
                quantity=164,
                unit_price=62,
                unit="KN",
            ),
        ],
        customs=Customs(
            sale_mode=code_for(SALE_MODES, "A FIRME"),
            sale_clause=code_for(SALE_CLAUSES, "FOB"),
            clause_total="831.17",
            transport_route=code_for(TRANSPORT_ROUTES, "AEREO"),
            freight="115.14",
            insurance="15.95",
            total_packages=24,
            receiver_country=code_for(COUNTRIES, "ALEMANIA"),
            destination_country=code_for(COUNTRIES, "ALEMANIA"),
        ),
        global_charges=[
            GlobalDiscount(value=Decimal("115.14"), kind="R", value_type="$", reason="FLETE"),
            GlobalDiscount(value=Decimal("15.95"), kind="R", value_type="$", reason="SEGURO"),
        ],
    )


# --------------------------------------------------------------------------- #
#  Tipos
# --------------------------------------------------------------------------- #
def test_export_types_are_recognised():
    assert DTEType.EXPORT_INVOICE.is_export
    assert DTEType.EXPORT_DEBIT_NOTE.is_export
    assert DTEType.EXPORT_CREDIT_NOTE.is_export
    assert not DTEType.AFFECTED_INVOICE.is_export
    # Las notas de exportación también exigen referencia.
    assert DTEType.EXPORT_CREDIT_NOTE.is_note


def test_non_export_type_is_rejected():
    with pytest.raises(ValueError, match="no es de exportación"):
        _document([ExportItem("A", quantity=1, unit_price=1)], doc_type=DTEType.AFFECTED_INVOICE)


# --------------------------------------------------------------------------- #
#  Montos decimales
# --------------------------------------------------------------------------- #
def test_amounts_keep_their_decimals():
    """Con float, 0.1+0.2 no da 0.3; con Decimal las cifras del set salen exactas."""
    item = ExportItem("A", quantity="3", unit_price="4631.13")
    assert item.line_amount == Decimal("13893.39")


def test_line_discount_is_applied_on_the_decimal_amount():
    item = ExportItem("A", quantity=239, unit_price=114, discount_pct=5)
    assert item.gross_amount == Decimal("27246")
    assert item.line_amount == Decimal("25883.70")


def test_export_totals_have_no_vat():
    doc = _case_5038176_1()
    base = Decimal("872") * Decimal("177")
    assert doc.base_amount == base
    # El total suma el flete y el seguro, que van como recargos globales.
    assert doc.exempt_amount == base + Decimal("3574.57") + Decimal("2759.05")
    assert doc.total_amount == doc.exempt_amount


def test_case_5038177_2_totals():
    doc = _case_5038177_2()
    assert doc.base_amount == Decimal("25883.70") + Decimal("10168")
    assert doc.total_amount == doc.base_amount + Decimal("115.14") + Decimal("15.95")


# --------------------------------------------------------------------------- #
#  Validación
# --------------------------------------------------------------------------- #
def test_currency_is_required():
    doc = _document([ExportItem("A", quantity=1, unit_price=1)], currency="")
    with pytest.raises(ValueError, match="moneda"):
        doc.validate()


def test_export_notes_require_a_reference():
    doc = _document(
        [ExportItem("A", quantity=1, unit_price=1)], doc_type=DTEType.EXPORT_CREDIT_NOTE
    )
    with pytest.raises(ValueError, match="Referencia"):
        doc.validate()


def test_package_group_limit():
    with pytest.raises(ValueError, match="10 grupos de bultos"):
        Customs(packages=[PackageGroup(kind_code=1) for _ in range(MAX_PACKAGE_GROUPS + 1)])


# --------------------------------------------------------------------------- #
#  XML
# --------------------------------------------------------------------------- #
def test_root_is_exportaciones(cert, caf_factory):
    root = build_export(_case_5038176_1(), caf_factory(110), TS)
    assert root.tag == "Exportaciones"
    assert root.get("ID") == "F1T110"


def test_totals_are_currency_exempt_and_total(cert, caf_factory):
    root = build_export(_case_5038176_1(), caf_factory(110), TS)
    totals = root.find("Encabezado/Totales")
    assert [c.tag for c in totals] == ["TpoMoneda", "MntExe", "MntTotal"]
    assert totals.findtext("TpoMoneda") == "LIBRA EST"
    # 872 × 177 = 154.344, más flete 3.574,57 y seguro 2.759,05.
    assert totals.findtext("MntTotal") == "160677.62"


def test_customs_block_follows_xsd_order(cert, caf_factory):
    root = build_export(_case_5038176_1(), caf_factory(110), TS)
    customs = root.find("Encabezado/Transporte/Aduana")
    tags = [c.tag for c in customs]
    assert tags == [
        "CodModVenta",
        "CodClauVenta",
        "TotClauVenta",
        "CodViaTransp",
        "CodPtoEmbarque",
        "CodPtoDesemb",
        "CodUnidMedTara",
        "CodUnidPesoBruto",
        "CodUnidPesoNeto",
        "TotBultos",
        "TipoBultos",
        "MntFlete",
        "MntSeguro",
        "CodPaisRecep",
        "CodPaisDestin",
    ]
    assert customs.findtext("TotClauVenta") == "4631.13"


def test_freight_and_insurance_appear_twice(cert, caf_factory):
    """El set lo pide explícitamente: en el encabezado y como recargos globales."""
    root = build_export(_case_5038176_1(), caf_factory(110), TS)
    customs = root.find("Encabezado/Transporte/Aduana")
    assert customs.findtext("MntFlete") == "3574.57"
    assert customs.findtext("MntSeguro") == "2759.05"

    charges = root.findall("DscRcgGlobal")
    assert len(charges) == 2
    assert [c.findtext("GlosaDR") for c in charges] == ["FLETE", "SEGURO"]
    assert [c.findtext("ValorDR") for c in charges] == ["3574.57", "2759.05"]
    assert all(c.findtext("TpoMov") == "R" for c in charges)


def test_package_group_is_emitted(cert, caf_factory):
    root = build_export(_case_5038176_1(), caf_factory(110), TS)
    group = root.find("Encabezado/Transporte/Aduana/TipoBultos")
    assert group.findtext("CodTpoBultos") == "13"  # ROLLOS, segun el Compendio
    assert group.findtext("CantBultos") == "87"


def test_line_discount_and_unit_reach_the_xml(cert, caf_factory):
    root = build_export(_case_5038177_2(), caf_factory(110), TS)
    detail = root.find("Detalle")
    assert detail.findtext("UnmdItem") == "KN"
    assert detail.findtext("DescuentoPct") == "5"
    assert detail.findtext("MontoItem") == "25883.7"


def test_no_customs_block_when_absent(cert, caf_factory):
    doc = _document([ExportItem("A", quantity=1, unit_price=1)])
    root = build_export(doc, caf_factory(110), TS)
    assert root.find("Encabezado/Transporte") is None


# --------------------------------------------------------------------------- #
#  XSD oficial
# --------------------------------------------------------------------------- #
def _envelope(doc, cert, caf_factory):
    signed = sign_document(build_export(doc, caf_factory(int(doc.type)), TS), cert)
    cover = Cover(
        issuer_rut="77262159-0",
        sender_rut="12291733-9",
        resolution_date=dt.date(2026, 1, 1),
        subtotals=[(int(doc.type), 1)],
    )
    return serialize_envelope(build_envelope([signed], cover, cert, TS))


def test_export_invoice_validates_against_official_xsd(cert, caf_factory):
    Validator(SCHEMAS).validate(_envelope(_case_5038176_1(), cert, caf_factory))


def test_second_set_case_validates(cert, caf_factory):
    Validator(SCHEMAS).validate(_envelope(_case_5038177_2(), cert, caf_factory))


def test_export_credit_note_validates(cert, caf_factory):
    """Caso 5038176-2: NC de exportación por devolución, al mismo precio unitario."""
    note = _document(
        [ExportItem("CHATARRA DE ALUMINIO", quantity=291, unit_price=177, unit="LT")],
        doc_type=DTEType.EXPORT_CREDIT_NOTE,
        currency="LIBRA EST",
        references=[
            Reference(
                doc_type=110,
                folio="1",
                date=ISSUE_DATE,
                code=ReferenceCode.CORRECT_AMOUNTS,
                reason="DEVOLUCION DE MERCADERIA",
            )
        ],
    )
    assert note.total_amount == Decimal("291") * Decimal("177")
    Validator(SCHEMAS).validate(_envelope(note, cert, caf_factory))


def test_export_debit_note_validates(cert, caf_factory):
    """Caso 5038176-3: ND que anula la nota de crédito."""
    note = _document(
        [ExportItem("ANULA NOTA DE CREDITO", amount="1")],
        doc_type=DTEType.EXPORT_DEBIT_NOTE,
        currency="LIBRA EST",
        references=[
            Reference(
                doc_type=112,
                folio="1",
                date=ISSUE_DATE,
                code=ReferenceCode.CANCEL_DOCUMENT,
                reason="ANULA NOTA DE CREDITO",
            )
        ],
    )
    Validator(SCHEMAS).validate(_envelope(note, cert, caf_factory))


def test_signature_verifies(cert, caf_factory):
    xml = _envelope(_case_5038176_1(), cert, caf_factory)
    results = signer.verify_signatures(etree.fromstring(xml))
    assert results and all(results)


# --------------------------------------------------------------------------- #
#  Códigos de Aduana
# --------------------------------------------------------------------------- #
def test_set_codes_resolve_against_the_official_tables():
    """Los nombres que da el set deben existir en el Compendio, no inventarse."""
    assert code_for(COUNTRIES, "España") == 517
    assert code_for(COUNTRIES, "ALEMANIA") == 563
    assert code_for(PORTS, "San Antonio") == 906
    assert code_for(PORTS, "BARCELONA") == 563
    assert code_for(PORTS, "Bremen") == 591
    assert code_for(TRANSPORT_ROUTES, "MARITIMA, FLUVIAL Y LACUSTRE") == 1
    assert code_for(TRANSPORT_ROUTES, "AEREO") == 4
    assert code_for(SALE_CLAUSES, "CFR") == 2
    assert code_for(SALE_CLAUSES, "FOB") == 5
    assert code_for(MEASURE_UNITS, "LT") == 9
    assert code_for(MEASURE_UNITS, "KN") == 6
    assert code_for(PACKAGE_TYPES, "ROLLOS") == 13
    assert code_for(SALE_MODES, "A FIRME") == 1


def test_lookup_ignores_accents_and_case():
    assert code_for(COUNTRIES, "españa") == code_for(COUNTRIES, "ESPANA")


def test_unknown_name_fails_loudly_with_suggestions():
    """Mejor reventar que mandarle al SII un código inventado."""
    with pytest.raises(KeyError) as ex:
        code_for(PORTS, "PUERTO INEXISTENTE")
    assert "No hay código de Aduana" in str(ex.value)

    with pytest.raises(KeyError, match="Quisiste decir"):
        code_for(COUNTRIES, "ALEMAN")
