"""Tipos de DTE soportados por el MVP y sus características."""

from __future__ import annotations

from enum import IntEnum


class DTEType(IntEnum):
    """Códigos de tipo de documento según el SII."""

    AFFECTED_INVOICE = 33  # Factura afecta
    EXEMPT_INVOICE = 34  # Factura exenta
    DEBIT_NOTE = 56  # Nota de débito
    CREDIT_NOTE = 61  # Nota de crédito

    @property
    def is_exempt(self) -> bool:
        """True si el documento es exento de IVA (no lleva monto neto/IVA)."""
        return self is DTEType.EXEMPT_INVOICE

    @property
    def is_note(self) -> bool:
        """True si requiere bloque de Referencia obligatorio (notas)."""
        return self in (DTEType.DEBIT_NOTE, DTEType.CREDIT_NOTE)

    @property
    def label(self) -> str:
        return {
            DTEType.AFFECTED_INVOICE: "Factura Electrónica",
            DTEType.EXEMPT_INVOICE: "Factura Electrónica Exenta",
            DTEType.DEBIT_NOTE: "Nota de Débito Electrónica",
            DTEType.CREDIT_NOTE: "Nota de Crédito Electrónica",
        }[self]


# Códigos de referencia para notas (campo CodRef)
class ReferenceCode(IntEnum):
    CANCEL_DOCUMENT = 1  # anula documento de referencia
    CORRECT_TEXT = 2  # corrige glosa/texto (sin afectar montos)
    CORRECT_AMOUNTS = 3  # corrige montos
