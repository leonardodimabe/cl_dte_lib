"""Documentos de exportación: factura (110), nota de débito (111) y de crédito (112).

Como la liquidación, tienen raíz propia en el XSD —``<Exportaciones>``— pero se
apartan del resto por dos cosas de fondo:

- **Los montos son decimales**, no enteros. Se factura en moneda extranjera, así
  que aquí se trabaja con ``Decimal`` y no con pesos redondeados. Usar float
  arrastraría error en cifras como 4631.13.
- **Los totales no llevan IVA**: la exportación es exenta. ``Totales`` es sólo
  ``TpoMoneda``, ``MntExe`` y ``MntTotal``.

Además llevan el bloque ``<Aduana>``, con los datos del embarque. Sus códigos
—modalidad y cláusula de venta, vía de transporte, puertos, países, unidades de
medida, tipos de bulto— salen de las **tablas de Aduana** que el SII publica
aparte; este módulo los transporta pero no los interpreta.

El flete y el seguro van dos veces, como exige el set de certificación: en los
campos informativos del encabezado (``MntFlete``/``MntSeguro``) y además como
dos líneas de recargo global.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

from .caf import CAF
from .document_types import DTEType
from .models import GlobalDiscount, Issuer, Receiver, Reference
from .ted import build_ted
from .text import (
    DocumentDataError,
    FieldProblem,
    check,
    issuer_problems,
    receiver_problems,
    reference_problems,
)

# Los montos de exportación admiten 4 decimales (Dec14_4Type del XSD).
AMOUNT_PLACES = Decimal("0.0001")

# Tipos que usan la raíz <Exportaciones>.
EXPORT_TYPES = (
    DTEType.EXPORT_INVOICE,
    DTEType.EXPORT_DEBIT_NOTE,
    DTEType.EXPORT_CREDIT_NOTE,
)

# El XSD admite hasta 10 grupos de bultos.
MAX_PACKAGE_GROUPS = 10


def _amount(value) -> Decimal:
    """Normaliza a Decimal con 4 decimales, sin pasar por float."""
    return Decimal(str(value)).quantize(AMOUNT_PLACES, rounding=ROUND_HALF_UP)


@dataclass
class ExportItem:
    """Línea de un documento de exportación, en moneda extranjera."""

    name: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    unit: str = ""  # UnmdItem
    description: str = ""
    discount_pct: Decimal | None = None
    surcharge_pct: Decimal | None = None
    # Si no se informa, se calcula desde cantidad × precio con su descuento.
    amount: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("quantity", "unit_price", "amount", "discount_pct", "surcharge_pct"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, Decimal(str(value)))

    @property
    def gross_amount(self) -> Decimal:
        if self.quantity is None or self.unit_price is None:
            return _amount(self.amount or 0)
        return _amount(self.quantity * self.unit_price)

    @property
    def line_amount(self) -> Decimal:
        """MontoItem: bruto con su descuento o recargo de línea aplicado.

        ``amount`` reemplaza a cantidad × precio como **base**, no al monto
        final: una línea que informa su valor y además un recargo debe salir
        con el recargo dentro. Declarar ``RecargoPct`` y no sumarlo dejaba el
        MontoItem contradiciendo su propio porcentaje.
        """
        total = self.gross_amount
        if self.discount_pct:
            total -= total * self.discount_pct / 100
        if self.surcharge_pct:
            total += total * self.surcharge_pct / 100
        return _amount(total)


@dataclass
class PackageGroup:
    """Un grupo de bultos del embarque (<TipoBultos>)."""

    kind_code: int  # CodTpoBultos, según tabla de Aduana
    quantity: int | None = None  # CantBultos
    marks: str = ""  # Marcas
    container_id: str = ""  # IdContainer
    seal: str = ""  # Sello
    seal_issuer: str = ""  # EmisorSello


@dataclass
class OtherCurrency:
    """``<OtraMoneda>``: los mismos montos, expresados en otra moneda.

    En exportación el documento va en moneda extranjera y el SII pide además su
    equivalente en pesos. El XSD la declara opcional, pero el Servicio la exige:
    sin ella responde «(HED-3-834) Exportacion: seccion (OtraMoneda) obligatoria
    - montos en pesos chilenos» y rechaza el documento.

    Sólo se indica el tipo de cambio; los montos se derivan de los del propio
    documento. Escribirlos aparte permitiría que dejaran de cuadrar con el
    total, que es justo lo que el SII contrasta.
    """

    #: Pesos por unidad de la moneda del documento (TpoCambio).
    exchange_rate: Decimal
    #: TpoMoneda de destino. Tabla de monedas de Aduana.
    currency: str = "PESO CL"

    def __post_init__(self) -> None:
        self.exchange_rate = Decimal(str(self.exchange_rate))
        if self.exchange_rate <= 0:
            raise ValueError("El tipo de cambio debe ser mayor que cero.")

    def convert(self, amount: Decimal) -> Decimal:
        """El monto en la otra moneda, redondeado como el peso: sin decimales."""
        return (amount * self.exchange_rate).quantize(Decimal(1), rounding=ROUND_HALF_UP)


@dataclass
class Customs:
    """Bloque ``<Aduana>``. Los códigos vienen de las tablas de Aduana del SII."""

    sale_mode: int | None = None  # CodModVenta
    sale_clause: int | None = None  # CodClauVenta
    clause_total: Decimal | None = None  # TotClauVenta
    transport_route: int | None = None  # CodViaTransp
    transport_name: str = ""  # NombreTransp
    carrier_rut: str = ""  # RUTCiaTransp
    carrier_name: str = ""  # NomCiaTransp
    booking: str = ""  # Booking
    operator: str = ""  # Operador
    loading_port: int | None = None  # CodPtoEmbarque
    unloading_port: int | None = None  # CodPtoDesemb
    tare: Decimal | None = None  # Tara
    tare_unit: int | None = None  # CodUnidMedTara
    gross_weight: Decimal | None = None  # PesoBruto
    gross_weight_unit: int | None = None  # CodUnidPesoBruto
    net_weight: Decimal | None = None  # PesoNeto
    net_weight_unit: int | None = None  # CodUnidPesoNeto
    total_items: int | None = None  # TotItems
    total_packages: int | None = None  # TotBultos
    packages: list[PackageGroup] = field(default_factory=list)  # TipoBultos
    freight: Decimal | None = None  # MntFlete
    insurance: Decimal | None = None  # MntSeguro
    receiver_country: int | None = None  # CodPaisRecep
    destination_country: int | None = None  # CodPaisDestin

    def __post_init__(self) -> None:
        for name in ("clause_total", "tare", "gross_weight", "net_weight", "freight", "insurance"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, Decimal(str(value)))
        if len(self.packages) > MAX_PACKAGE_GROUPS:
            raise ValueError(
                f"El XSD admite {MAX_PACKAGE_GROUPS} grupos de bultos y hay {len(self.packages)}."
            )


@dataclass
class ExportDocument:
    """Factura, nota de débito o nota de crédito de exportación."""

    type: DTEType
    folio: int
    issue_date: _dt.date
    issuer: Issuer
    receiver: Receiver
    currency: str  # TpoMoneda, p.ej. "DOLAR USA" o "LIBRA EST"
    items: list[ExportItem] = field(default_factory=list)
    global_charges: list[GlobalDiscount] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    customs: Customs | None = None
    payment_mode: int | None = None  # FmaPagExp
    service_indicator: int | None = None  # IndServicio
    #: <OtraMoneda>: los mismos montos en pesos. Obligatoria en exportación.
    other_currency: OtherCurrency | None = None
    # <Extranjero>: identificación del comprador de fuera de Chile.
    foreign_id: str = ""  # NumId (pasaporte, tax id del país, etc.)
    receiver_nationality: int | None = None  # Nacionalidad: código de país de Aduana

    def __post_init__(self) -> None:
        if self.type not in EXPORT_TYPES:
            allowed = ", ".join(str(int(t)) for t in EXPORT_TYPES)
            raise ValueError(
                f"El tipo {int(self.type)} no es de exportación; se esperaba {allowed}."
            )

    # --- Totales: la exportación es exenta, no hay neto ni IVA ---
    @property
    def base_amount(self) -> Decimal:
        return _amount(sum((item.line_amount for item in self.items), Decimal(0)))

    @property
    def exempt_amount(self) -> Decimal:
        base = self.base_amount
        total = base
        for charge in self.global_charges:
            amount = (
                base * Decimal(str(charge.value)) / 100
                if charge.value_type == "%"
                else Decimal(str(charge.value))
            )
            total += amount if charge.kind == "R" else -amount
        return _amount(total)

    @property
    def total_amount(self) -> Decimal:
        return self.exempt_amount

    def validate(self) -> None:
        if self.folio <= 0:
            raise ValueError("Folio inválido.")
        self.validate_content()

    def validate_content(self) -> None:
        if not self.items:
            raise ValueError("El documento de exportación debe tener al menos una línea.")
        if not self.currency:
            raise ValueError("Falta la moneda de la operación (TpoMoneda).")
        if self.other_currency is None:
            # El XSD la declara opcional (minOccurs="0"), así que un documento
            # sin ella valida contra el esquema y el SII lo rechaza igual con
            # «(HED-3-834) Exportacion: seccion (OtraMoneda) obligatoria». Se
            # exige acá porque es el único sitio donde se detecta antes de
            # gastar un folio.
            raise ValueError(
                "Falta <OtraMoneda>: el SII la exige en los documentos de exportación,"
                " con los montos en pesos chilenos y el tipo de cambio."
            )
        for position, group in enumerate(self.customs.packages if self.customs else [], start=1):
            if not group.marks.strip():
                # Otro campo opcional en el XSD que el SII exige: responde
                # «(HED-2-804) Exportacion : Campo obligatorio : Marcas».
                raise ValueError(
                    f"Falta Marcas en el grupo de bultos {position}: el SII lo exige"
                    " cuando el documento declara <TipoBultos>."
                )
        if self.type in (DTEType.EXPORT_DEBIT_NOTE, DTEType.EXPORT_CREDIT_NOTE):
            if not self.references:
                raise ValueError(
                    f"El tipo {int(self.type)} (nota de exportación) requiere una Referencia."
                )
        if self.total_amount < 0:
            raise ValueError("Los descuentos globales dejan un total negativo.")
        self.validate_text()

    def text_problems(self) -> list[FieldProblem]:
        """Revisa el texto, incluidos los campos libres del bloque Aduana."""
        problems = issuer_problems(self.issuer) + receiver_problems(self.receiver)
        for position, item in enumerate(self.items, start=1):
            where = f"Detalle[{position}]"
            problems += check(f"{where}.NmbItem", item.name, "NmbItem")
            problems += check(f"{where}.DscItem", item.description, "DscItem")
            problems += check(f"{where}.UnmdItem", item.unit, "UnmdItem")
        for position, charge in enumerate(self.global_charges, start=1):
            problems += check(f"DscRcgGlobal[{position}].GlosaDR", charge.reason, "GlosaDR")

        customs = self.customs
        if customs is not None:
            problems += check("Aduana.NombreTransp", customs.transport_name, "NombreTransp")
            problems += check("Aduana.NomCiaTransp", customs.carrier_name, "NomCiaTransp")
            problems += check("Aduana.Booking", customs.booking, "Booking")
            problems += check("Aduana.Operador", customs.operator, "Operador")
            for position, group in enumerate(customs.packages, start=1):
                where = f"Aduana.TipoBultos[{position}]"
                problems += check(f"{where}.Marcas", group.marks, "Marcas")
                problems += check(f"{where}.IdContainer", group.container_id, "IdContainer")
                problems += check(f"{where}.Sello", group.seal, "Sello")
                problems += check(f"{where}.EmisorSello", group.seal_issuer, "EmisorSello")
        return problems + reference_problems(self.references)

    def validate_text(self) -> None:
        problems = self.text_problems()
        if problems:
            raise DocumentDataError(problems)


def build_export(document: ExportDocument, caf: CAF, timestamp: _dt.datetime) -> etree._Element:
    """Devuelve el nodo ``<Exportaciones>`` listo para firmar."""
    document.validate()

    root = etree.Element("Exportaciones", ID=f"F{document.folio}T{int(document.type)}")
    _header(root, document)
    _items(root, document)
    _global_charges(root, document)
    _references(root, document)

    root.append(build_ted(document, caf, timestamp))
    etree.SubElement(root, "TmstFirma").text = timestamp.replace(microsecond=0).isoformat()
    return root


# --------------------------------------------------------------------------- #
#  Secciones
# --------------------------------------------------------------------------- #
def _header(root: etree._Element, doc: ExportDocument) -> None:
    header = etree.SubElement(root, "Encabezado")

    id_doc = etree.SubElement(header, "IdDoc")
    _t(id_doc, "TipoDTE", str(int(doc.type)))
    _t(id_doc, "Folio", str(doc.folio))
    _t(id_doc, "FchEmis", doc.issue_date.isoformat())
    if doc.service_indicator is not None:
        _t(id_doc, "IndServicio", str(doc.service_indicator))
    if doc.payment_mode is not None:
        _t(id_doc, "FmaPagExp", str(doc.payment_mode))

    issuer = etree.SubElement(header, "Emisor")
    _t(issuer, "RUTEmisor", doc.issuer.rut.value)
    _t(issuer, "RznSoc", doc.issuer.business_name)
    _t(issuer, "GiroEmis", doc.issuer.activity[:80])
    _t(issuer, "Acteco", str(doc.issuer.economic_activity))
    if doc.issuer.branch_code is not None:
        _t(issuer, "CdgSIISucur", str(doc.issuer.branch_code))
    _t(issuer, "DirOrigen", doc.issuer.address[:70])
    _t(issuer, "CmnaOrigen", doc.issuer.commune)
    if doc.issuer.city:
        _t(issuer, "CiudadOrigen", doc.issuer.city)

    receiver = etree.SubElement(header, "Receptor")
    _t(receiver, "RUTRecep", doc.receiver.rut.value)
    _t(receiver, "RznSocRecep", doc.receiver.business_name[:100])
    # <Extranjero> identifica al comprador de fuera de Chile. La nacionalidad es
    # el código de país de Aduana y va antes del giro (orden del XSD).
    if doc.foreign_id or doc.receiver_nationality is not None:
        foreign = etree.SubElement(receiver, "Extranjero")
        if doc.foreign_id:
            _t(foreign, "NumId", doc.foreign_id[:20])
        if doc.receiver_nationality is not None:
            _t(foreign, "Nacionalidad", str(doc.receiver_nationality))
    if doc.receiver.activity:
        _t(receiver, "GiroRecep", doc.receiver.activity[:40])
    _t(receiver, "DirRecep", doc.receiver.address[:70])
    _t(receiver, "CmnaRecep", doc.receiver.commune)
    if doc.receiver.city:
        _t(receiver, "CiudadRecep", doc.receiver.city)

    _transport(header, doc)

    totals = etree.SubElement(header, "Totales")
    _t(totals, "TpoMoneda", doc.currency)
    _t(totals, "MntExe", _fmt(doc.exempt_amount))
    _t(totals, "MntTotal", _fmt(doc.total_amount))

    # <OtraMoneda> va después de <Totales>, dentro de <Encabezado>, y es el
    # último hijo que admite el XSD ahí.
    otra = doc.other_currency
    if otra is not None:
        node = etree.SubElement(header, "OtraMoneda")
        _t(node, "TpoMoneda", otra.currency)
        _t(node, "TpoCambio", _fmt(otra.exchange_rate))
        # La exportación es exenta: lo que hay es monto exento y total, no neto
        # ni IVA. Se declaran los dos porque el SII contrasta el total.
        _t(node, "MntExeOtrMnda", _fmt(otra.convert(doc.exempt_amount)))
        _t(node, "MntTotOtrMnda", _fmt(otra.convert(doc.total_amount)))


def _transport(header: etree._Element, doc: ExportDocument) -> None:
    if doc.customs is None:
        return
    transport = etree.SubElement(header, "Transporte")
    customs = etree.SubElement(transport, "Aduana")
    c = doc.customs

    _num(customs, "CodModVenta", c.sale_mode)
    _num(customs, "CodClauVenta", c.sale_clause)
    _dec(customs, "TotClauVenta", c.clause_total)
    _num(customs, "CodViaTransp", c.transport_route)
    _text(customs, "NombreTransp", c.transport_name, 40)
    _text(customs, "RUTCiaTransp", c.carrier_rut, 10)
    _text(customs, "NomCiaTransp", c.carrier_name, 40)
    _text(customs, "Booking", c.booking, 20)
    _text(customs, "Operador", c.operator, 20)
    _num(customs, "CodPtoEmbarque", c.loading_port)
    _num(customs, "CodPtoDesemb", c.unloading_port)
    _dec(customs, "Tara", c.tare)
    _num(customs, "CodUnidMedTara", c.tare_unit)
    _dec(customs, "PesoBruto", c.gross_weight)
    _num(customs, "CodUnidPesoBruto", c.gross_weight_unit)
    _dec(customs, "PesoNeto", c.net_weight)
    _num(customs, "CodUnidPesoNeto", c.net_weight_unit)
    _num(customs, "TotItems", c.total_items)
    _num(customs, "TotBultos", c.total_packages)
    for group in c.packages:
        node = etree.SubElement(customs, "TipoBultos")
        _num(node, "CodTpoBultos", group.kind_code)
        _num(node, "CantBultos", group.quantity)
        _text(node, "Marcas", group.marks, 90)
        _text(node, "IdContainer", group.container_id, 20)
        _text(node, "Sello", group.seal, 20)
        _text(node, "EmisorSello", group.seal_issuer, 20)
    _dec(customs, "MntFlete", c.freight)
    _dec(customs, "MntSeguro", c.insurance)
    _num(customs, "CodPaisRecep", c.receiver_country)
    _num(customs, "CodPaisDestin", c.destination_country)


def _items(root: etree._Element, doc: ExportDocument) -> None:
    for position, item in enumerate(doc.items, start=1):
        detail = etree.SubElement(root, "Detalle")
        _t(detail, "NroLinDet", str(position))
        _t(detail, "NmbItem", item.name[:80])
        if item.description:
            _t(detail, "DscItem", item.description[:1000])
        _dec(detail, "QtyItem", item.quantity)
        _text(detail, "UnmdItem", item.unit, 4)
        _dec(detail, "PrcItem", item.unit_price)
        _dec(detail, "DescuentoPct", item.discount_pct)
        _dec(detail, "RecargoPct", item.surcharge_pct)
        _t(detail, "MontoItem", _fmt(item.line_amount))


def _global_charges(root: etree._Element, doc: ExportDocument) -> None:
    """El set exige informar flete y seguro también como recargos globales."""
    for position, charge in enumerate(doc.global_charges, start=1):
        node = etree.SubElement(root, "DscRcgGlobal")
        _t(node, "NroLinDR", str(position))
        _t(node, "TpoMov", charge.kind)
        if charge.reason:
            _t(node, "GlosaDR", charge.reason[:45])
        _t(node, "TpoValor", charge.value_type)
        _t(node, "ValorDR", _fmt(Decimal(str(charge.value))))


def _references(root: etree._Element, doc: ExportDocument) -> None:
    for position, reference in enumerate(doc.references, start=1):
        node = etree.SubElement(root, "Referencia")
        _t(node, "NroLinRef", str(position))
        _t(node, "TpoDocRef", str(reference.doc_type))
        _t(node, "FolioRef", str(reference.folio))
        if reference.date is not None:
            _t(node, "FchRef", reference.date.isoformat())
        if reference.code is not None:
            _t(node, "CodRef", str(int(reference.code)))
        if reference.reason:
            _t(node, "RazonRef", reference.reason[:90])


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _t(parent: etree._Element, tag: str, value: str) -> None:
    etree.SubElement(parent, tag).text = value


def _num(parent: etree._Element, tag: str, value: int | None) -> None:
    if value is not None:
        _t(parent, tag, str(value))


def _dec(parent: etree._Element, tag: str, value: Decimal | None) -> None:
    if value is not None:
        _t(parent, tag, _fmt(value))


def _text(parent: etree._Element, tag: str, value: str, limit: int) -> None:
    if value:
        _t(parent, tag, value[:limit])


def _fmt(value: Decimal) -> str:
    """Formatea sin ceros decimales sobrantes: 4631.1300 → '4631.13'."""
    normalized = Decimal(value).quantize(AMOUNT_PLACES, rounding=ROUND_HALF_UP).normalize()
    text = format(normalized, "f")
    return text
