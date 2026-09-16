"""Modelos de dominio del DTE como dataclasses.

Separan la *intención de negocio* (qué se quiere facturar) de la *representación
XML* (cómo lo exige el SII). El cálculo de totales vive aquí para que sea
testeable sin tocar XML ni criptografía.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .document_types import (
    DispatchType,
    DTEType,
    ReferenceCode,
    ServiceIndicator,
    TransferType,
)
from .rut import Rut
from .text import (
    DocumentDataError,
    FieldProblem,
    check,
    issuer_problems,
    receiver_problems,
    reference_problems,
)

VAT_RATE = 19  # %


@dataclass
class Issuer:
    rut: Rut
    business_name: str
    activity: str
    economic_activity: int  # código Acteco
    address: str
    commune: str
    city: str = ""
    # Sucursal desde la que se emite. El código lo entrega el SII y se ve en
    # "Mi SII → Direcciones"; identifica el domicilio que respalda el documento.
    branch_name: str = ""  # Sucursal
    branch_code: int | None = None  # CdgSIISucur

    def __post_init__(self) -> None:
        if not isinstance(self.rut, Rut):
            self.rut = Rut(str(self.rut))


@dataclass
class Receiver:
    rut: Rut
    business_name: str
    activity: str
    address: str
    commune: str
    city: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.rut, Rut):
            self.rut = Rut(str(self.rut))


@dataclass
class Item:
    """Línea de detalle. Los montos son enteros (pesos chilenos, sin decimales)."""

    name: str
    quantity: float
    unit_price: int
    exempt: bool = False  # True → no afecto a IVA dentro de doc afecto
    description: str = ""
    unit: str = ""
    # Descuento de la línea: se informa el % (DescuentoPct) y/o el monto
    # (DescuentoMonto). Si solo se da el %, el monto se deriva del bruto.
    discount_pct: float = 0.0
    discount_amount: int | None = None

    @property
    def gross_amount(self) -> int:
        """Cantidad × precio unitario, ANTES del descuento de línea."""
        return round(self.quantity * self.unit_price)

    @property
    def discount(self) -> int:
        """DescuentoMonto efectivo de la línea."""
        if self.discount_amount is not None:
            return self.discount_amount
        if self.discount_pct:
            return round(self.gross_amount * self.discount_pct / 100)
        return 0

    @property
    def amount(self) -> int:
        """MontoItem: bruto menos el descuento de línea."""
        return self.gross_amount - self.discount


# Ámbito del descuento/recargo global → IndExeDR del XSD.
_SCOPE_INDICATOR: dict[str, str | None] = {
    "afecto": None,  # sin IndExeDR: aplica sobre los montos afectos
    "exento": "1",
    "no_afecto": "2",
}


@dataclass
class GlobalDiscount:
    """Descuento o recargo global del documento (<DscRcgGlobal>).

    ``kind`` es TpoMov ("D" descuento / "R" recargo) y ``value_type`` es TpoValor
    ("%" porcentaje / "$" monto fijo). ``scope`` decide sobre qué base se aplica
    y qué IndExeDR se emite.
    """

    value: float  # ValorDR
    kind: str = "D"  # TpoMov
    value_type: str = "%"  # TpoValor
    reason: str = ""  # GlosaDR
    scope: str = "afecto"

    def __post_init__(self) -> None:
        self.kind = self.kind.upper()
        if self.kind not in ("D", "R"):
            raise ValueError("TpoMov debe ser 'D' (descuento) o 'R' (recargo).")
        if self.value_type not in ("%", "$"):
            raise ValueError("TpoValor debe ser '%' o '$'.")
        if self.scope not in _SCOPE_INDICATOR:
            raise ValueError(f"Ambito invalido: {self.scope!r}.")
        if self.value < 0:
            raise ValueError("ValorDR no puede ser negativo (usa kind='R' para recargo).")

    @property
    def sign(self) -> int:
        return -1 if self.kind == "D" else 1

    @property
    def exempt_indicator(self) -> str | None:
        return _SCOPE_INDICATOR[self.scope]

    def amount_over(self, base: int) -> int:
        """Monto que descuenta/recarga sobre ``base`` (la base ya neta de linea)."""
        if self.value_type == "%":
            return round(base * self.value / 100)
        return round(self.value)


@dataclass
class Reference:
    """Referencia a otro documento (obligatoria en notas 56/61)."""

    # TpoDocRef: el código del documento (33, 61...), o "SET" en la factura de
    # certificación. Vacío en la referencia de caso de una boleta, que va en
    # CodRef (ver receipt._references).
    doc_type: int | str | None
    folio: str  # FolioRef
    # FchRef. Opcional a propósito: el XSD de la BOLETA no define este campo,
    # así que una referencia de boleta no tiene fecha que informar.
    date: _dt.date | None = None
    # CodRef: en factura, el código numérico (1 anula, 2 corrige texto, 3
    # corrige montos). En boleta es alfanumérico: el set pide «SET».
    code: ReferenceCode | str | None = None
    reason: str = ""  # RazonRef


# Código SII del "IVA retenido total" (tabla 4 del Formato DTE). Es el que usa
# la factura de compra cuando el comprador retiene todo el IVA (cambio de sujeto).
VAT_RETENTION_TOTAL = 15

# Documentos donde el SII acepta el bloque <ImptoReten>.
_RETENTION_ALLOWED = (
    DTEType.PURCHASE_INVOICE,
    DTEType.DEBIT_NOTE,
    DTEType.CREDIT_NOTE,
)


@dataclass
class Retention:
    """Impuesto o retención adicional del documento (<ImptoReten>).

    Con ``amount=None`` la retención toma el IVA completo, que es el caso normal
    del código 15 (retención total) en la factura de compra.
    """

    code: int = VAT_RETENTION_TOTAL  # TipoImp
    amount: int | None = None  # MontoImp; None → el IVA completo
    rate: float | None = None  # TasaImp

    def amount_over(self, vat: int) -> int:
        return self.amount if self.amount is not None else vat


@dataclass
class Driver:
    """Chofer que realiza el traslado (<Chofer>)."""

    rut: Rut
    name: str  # NombreChofer, máx. 30

    def __post_init__(self) -> None:
        if not isinstance(self.rut, Rut):
            self.rut = Rut(str(self.rut))


@dataclass
class Transport:
    """Sección <Transporte> del encabezado.

    La Res. Ex. N°154 (2025) sumó ``trailer_plate``, ``departure_date``,
    ``departure_time`` y ``arrival_date``, y volvió obligatorios patente,
    transportista, chofer y dirección de destino en todo documento que acompaña
    traslado de bienes. Rige desde el 2026-11-01 (prorrogada por Res. Ex. N°52).
    """

    plate: str = ""  # Patente
    trailer_plate: str = ""  # PatenteCarro (Res. 154)
    carrier_rut: Rut | None = None  # RUTTrans
    driver: Driver | None = None  # Chofer
    dest_address: str = ""  # DirDest
    dest_commune: str = ""  # CmnaDest
    dest_city: str = ""  # CiudadDest
    departure_date: _dt.date | None = None  # FchSalida (Res. 154)
    departure_time: _dt.time | None = None  # HraSalida (Res. 154)
    arrival_date: _dt.date | None = None  # FchLlegada (Res. 154)

    def __post_init__(self) -> None:
        if self.carrier_rut is not None and not isinstance(self.carrier_rut, Rut):
            self.carrier_rut = Rut(str(self.carrier_rut))

    def missing_res154_fields(self) -> list[str]:
        """Campos que la Res. 154 exige y que aquí faltan."""
        required = {
            "patente del vehículo": self.plate,
            "RUT del transportista": self.carrier_rut,
            "chofer (RUT y nombre)": self.driver,
            "dirección de destino": self.dest_address,
            "comuna de destino": self.dest_commune,
            "fecha de salida": self.departure_date,
            "hora de salida": self.departure_time,
            "fecha de llegada": self.arrival_date,
        }
        return [name for name, value in required.items() if not value]


# Entrada en vigencia de la Res. Ex. N°154 (prorrogada seis meses por la
# Res. Ex. N°52): desde esta fecha sus campos dejan de ser opcionales.
RES154_EFFECTIVE_DATE = _dt.date(2026, 11, 1)


@dataclass
class DTE:
    """Documento Tributario Electrónico completo en el dominio."""

    type: DTEType
    folio: int
    issue_date: _dt.date
    issuer: Issuer
    receiver: Receiver
    items: list[Item] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    global_discounts: list[GlobalDiscount] = field(default_factory=list)
    # Traslado de bienes (guía de despacho, o factura que acompaña el traslado).
    dispatch_type: DispatchType | None = None  # TipoDespacho
    transfer_type: TransferType | None = None  # IndTraslado (solo guías)
    transport: Transport | None = None
    # Impuestos/retenciones adicionales. En la factura de compra, la retención
    # total del IVA (código 15) es lo habitual: el comprador la entera al SII.
    retentions: list[Retention] = field(default_factory=list)
    # Boleta: los precios de las líneas vienen con IVA incluido, así que el neto
    # se deriva del bruto en vez de sumarse. Se declara con IndMntNeto sólo
    # cuando NO es así (el XSD sólo admite el valor 2 = "son valores netos").
    prices_include_vat: bool = False
    service_indicator: ServiceIndicator | None = None  # IndServicio (boletas)

    # --- Totales calculados ---
    @property
    def base_exempt(self) -> int:
        """Suma de las líneas exentas, antes de descuentos/recargos globales."""
        if self.type.is_exempt:
            return sum(i.amount for i in self.items)
        return sum(i.amount for i in self.items if i.exempt)

    @property
    def base_affected(self) -> int:
        """Suma de las líneas afectas, antes de descuentos/recargos globales."""
        if self.type.is_exempt:
            return 0
        return sum(i.amount for i in self.items if not i.exempt)

    def _apply_global(self, base: int, scopes: tuple[str, ...]) -> int:
        """Aplica sobre ``base`` los DscRcgGlobal cuyo ámbito esté en ``scopes``.

        El porcentaje se calcula siempre sobre la base original, no en cascada:
        así lo espera el SII cuando hay varios descuentos/recargos globales.
        """
        total = base
        for dr in self.global_discounts:
            if dr.scope in scopes:
                total += dr.sign * dr.amount_over(base)
        return total

    @property
    def exempt_amount(self) -> int:
        # En un documento exento no hay base afecta: todo global cae sobre el exento.
        scopes = (
            ("afecto", "exento", "no_afecto") if self.type.is_exempt else ("exento", "no_afecto")
        )
        return self._apply_global(self.base_exempt, scopes)

    @property
    def net_amount(self) -> int:
        if self.type.is_exempt:
            return 0
        affected = self._apply_global(self.base_affected, ("afecto",))
        if self.prices_include_vat:
            # El bruto ya trae el IVA: el neto se despeja dividiendo por 1,19.
            return round(affected / (1 + VAT_RATE / 100))
        return affected

    @property
    def vat(self) -> int:
        if self.prices_include_vat:
            # Se calcula por diferencia para que neto + IVA dé exactamente el
            # bruto cobrado; con round() sobre el neto podría descuadrar en $1.
            return self._apply_global(self.base_affected, ("afecto",)) - self.net_amount
        return round(self.net_amount * VAT_RATE / 100)

    @property
    def retained_amount(self) -> int:
        """Suma de las retenciones (<ImptoReten>), que rebajan el total a pagar."""
        return sum(r.amount_over(self.vat) for r in self.retentions)

    @property
    def total_amount(self) -> int:
        # El SII define MntTotal = neto + exento + IVA - retenciones: en una
        # factura de compra con retención total, al proveedor se le paga el neto.
        return self.net_amount + self.vat + self.exempt_amount - self.retained_amount

    def validate(self) -> None:
        """Validaciones de negocio mínimas antes de construir el XML."""
        if self.folio <= 0:
            raise ValueError("Folio inválido.")
        self.validate_content()

    def validate_content(self) -> None:
        """Valida todo salvo el folio.

        Se separa del folio a propósito: permite revisar la entrada ANTES de
        asignar un folio del CAF, para que un documento malformado no queme uno.
        """
        if not self.items:
            raise ValueError("El DTE debe tener al menos una línea de detalle.")
        if self.type.is_note and not self.references:
            raise ValueError(f"El tipo {self.type} (nota) requiere al menos una Referencia.")
        for dr in self.global_discounts:
            if dr.scope != "afecto" and not self.base_exempt:
                raise ValueError(
                    "Hay un descuento/recargo global exento pero el documento no tiene "
                    "líneas exentas sobre las cuales aplicarlo."
                )
        if self.net_amount < 0 or self.exempt_amount < 0:
            raise ValueError("Los descuentos globales dejan un total negativo.")
        self._validate_transfer()
        self._validate_retentions()
        self.validate_text()

    def text_problems(self) -> list[FieldProblem]:
        """Revisa todo el texto del documento contra lo que acepta el SII.

        Devuelve la lista completa: la idea es corregir todo de una vez en vez
        de descubrir los problemas de a uno, reintento tras reintento.
        """
        problems: list[FieldProblem] = []

        problems += issuer_problems(self.issuer)
        problems += receiver_problems(self.receiver)

        for position, item in enumerate(self.items, start=1):
            where = f"Detalle[{position}]"
            problems += check(f"{where}.NmbItem", item.name, "NmbItem")
            problems += check(f"{where}.DscItem", item.description, "DscItem")
            problems += check(f"{where}.UnmdItem", item.unit, "UnmdItem")

        problems += reference_problems(self.references)

        for position, discount in enumerate(self.global_discounts, start=1):
            problems += check(f"DscRcgGlobal[{position}].GlosaDR", discount.reason, "GlosaDR")

        transport = self.transport
        if transport is not None:
            problems += check("Transporte.Patente", transport.plate, "Patente")
            problems += check("Transporte.PatenteCarro", transport.trailer_plate, "PatenteCarro")
            problems += check("Transporte.DirDest", transport.dest_address, "DirDest")
            problems += check("Transporte.CmnaDest", transport.dest_commune, "CmnaDest")
            problems += check("Transporte.CiudadDest", transport.dest_city, "CiudadDest")
            if transport.driver is not None:
                problems += check("Transporte.NombreChofer", transport.driver.name, "NombreChofer")
        return problems

    def validate_text(self) -> None:
        """Lanza ``DocumentDataError`` con todos los problemas de texto, si los hay."""
        problems = self.text_problems()
        if problems:
            raise DocumentDataError(problems)

    def _validate_retentions(self) -> None:
        if self.retentions and self.type not in _RETENTION_ALLOWED:
            allowed = ", ".join(str(int(t)) for t in _RETENTION_ALLOWED)
            raise ValueError(
                f"El tipo {int(self.type)} no admite retenciones; el SII sólo las "
                f"acepta en {allowed}."
            )
        if self.retained_amount > self.net_amount + self.vat + self.exempt_amount:
            raise ValueError("Las retenciones superan el monto del documento.")

    @property
    def accompanies_goods(self) -> bool:
        """True si el documento ampara traslado de bienes (guía, o factura con despacho)."""
        return self.type.is_dispatch_note or self.dispatch_type is not None

    def _validate_transfer(self) -> None:
        if self.type.is_dispatch_note and self.transfer_type is None:
            raise ValueError("La guía de despacho requiere indicar el tipo de traslado.")
        if self.transfer_type is not None and not self.type.is_dispatch_note:
            raise ValueError("El tipo de traslado (IndTraslado) es sólo para guías de despacho.")
        if self.transfer_type is TransferType.INTERNAL and self.issuer.rut != self.receiver.rut:
            raise ValueError(
                "En un traslado interno el receptor debe ser el mismo emisor "
                f"({self.issuer.rut.value} ≠ {self.receiver.rut.value})."
            )

        # Res. Ex. N°154: sus campos sólo se exigen desde su entrada en vigencia,
        # para no romper documentos emitidos antes.
        if not self.accompanies_goods or self.issue_date < RES154_EFFECTIVE_DATE:
            return
        missing = self.transport.missing_res154_fields() if self.transport else ["todos"]
        if missing:
            raise ValueError(
                "Faltan datos de transporte que la Res. Ex. N°154 exige desde el "
                f"{RES154_EFFECTIVE_DATE.isoformat()}: {', '.join(missing)}."
            )
