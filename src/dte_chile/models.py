"""Modelos de dominio del DTE como dataclasses.

Separan la *intención de negocio* (qué se quiere facturar) de la *representación
XML* (cómo lo exige el SII). El cálculo de totales vive aquí para que sea
testeable sin tocar XML ni criptografía.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .document_types import DTEType, ReferenceCode
from .rut import Rut

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

    @property
    def amount(self) -> int:
        return round(self.quantity * self.unit_price)


@dataclass
class Reference:
    """Referencia a otro documento (obligatoria en notas 56/61)."""

    doc_type: int  # TpoDocRef (ej. 33)
    folio: str  # FolioRef
    date: _dt.date  # FchRef
    code: ReferenceCode | None = None  # CodRef
    reason: str = ""  # RazonRef


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

    # --- Totales calculados ---
    @property
    def exempt_amount(self) -> int:
        if self.type.is_exempt:
            return sum(i.amount for i in self.items)
        return sum(i.amount for i in self.items if i.exempt)

    @property
    def net_amount(self) -> int:
        if self.type.is_exempt:
            return 0
        return sum(i.amount for i in self.items if not i.exempt)

    @property
    def vat(self) -> int:
        return round(self.net_amount * VAT_RATE / 100)

    @property
    def total_amount(self) -> int:
        return self.net_amount + self.vat + self.exempt_amount

    def validate(self) -> None:
        """Validaciones de negocio mínimas antes de construir el XML."""
        if not self.items:
            raise ValueError("El DTE debe tener al menos una línea de detalle.")
        if self.folio <= 0:
            raise ValueError("Folio inválido.")
        if self.type.is_note and not self.references:
            raise ValueError(f"El tipo {self.type} (nota) requiere al menos una Referencia.")
