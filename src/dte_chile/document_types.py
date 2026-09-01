"""Tipos de DTE soportados por el MVP y sus características."""

from __future__ import annotations

from enum import IntEnum


class DTEType(IntEnum):
    """Códigos de tipo de documento según el SII."""

    AFFECTED_INVOICE = 33  # Factura afecta
    EXEMPT_INVOICE = 34  # Factura exenta
    RECEIPT = 39  # Boleta electrónica afecta
    EXEMPT_RECEIPT = 41  # Boleta electrónica exenta
    SETTLEMENT_INVOICE = 43  # Liquidación factura (mandatario → mandante)
    PURCHASE_INVOICE = 46  # Factura de compra (la emite el comprador)
    DISPATCH_NOTE = 52  # Guía de despacho
    DEBIT_NOTE = 56  # Nota de débito
    CREDIT_NOTE = 61  # Nota de crédito
    EXPORT_INVOICE = 110  # Factura de exportación
    EXPORT_DEBIT_NOTE = 111  # Nota de débito de exportación
    EXPORT_CREDIT_NOTE = 112  # Nota de crédito de exportación

    @property
    def is_exempt(self) -> bool:
        """True si el documento es exento de IVA (no lleva monto neto/IVA)."""
        return self in (DTEType.EXEMPT_INVOICE, DTEType.EXEMPT_RECEIPT)

    @property
    def is_receipt(self) -> bool:
        """True si es boleta: va en el sobre EnvioBOLETA, no en EnvioDTE."""
        return self in (DTEType.RECEIPT, DTEType.EXEMPT_RECEIPT)

    @property
    def is_purchase_invoice(self) -> bool:
        """True si es factura de compra: la emite el comprador, no el vendedor."""
        return self is DTEType.PURCHASE_INVOICE

    @property
    def is_dispatch_note(self) -> bool:
        """True si es guía de despacho (lleva IndTraslado y sección Transporte)."""
        return self is DTEType.DISPATCH_NOTE

    @property
    def is_note(self) -> bool:
        """True si requiere bloque de Referencia obligatorio (notas)."""
        return self in (
            DTEType.DEBIT_NOTE,
            DTEType.CREDIT_NOTE,
            DTEType.EXPORT_DEBIT_NOTE,
            DTEType.EXPORT_CREDIT_NOTE,
        )

    @property
    def is_export(self) -> bool:
        """True si va en la raíz <Exportaciones> y se factura en moneda extranjera."""
        return self in (
            DTEType.EXPORT_INVOICE,
            DTEType.EXPORT_DEBIT_NOTE,
            DTEType.EXPORT_CREDIT_NOTE,
        )

    @property
    def label(self) -> str:
        return {
            DTEType.AFFECTED_INVOICE: "Factura Electrónica",
            DTEType.EXEMPT_INVOICE: "Factura Electrónica Exenta",
            DTEType.RECEIPT: "Boleta Electrónica",
            DTEType.EXEMPT_RECEIPT: "Boleta Electrónica Exenta",
            DTEType.SETTLEMENT_INVOICE: "Liquidación Factura Electrónica",
            DTEType.EXPORT_INVOICE: "Factura de Exportación Electrónica",
            DTEType.EXPORT_DEBIT_NOTE: "Nota de Débito de Exportación Electrónica",
            DTEType.EXPORT_CREDIT_NOTE: "Nota de Crédito de Exportación Electrónica",
            DTEType.PURCHASE_INVOICE: "Factura de Compra Electrónica",
            DTEType.DISPATCH_NOTE: "Guía de Despacho Electrónica",
            DTEType.DEBIT_NOTE: "Nota de Débito Electrónica",
            DTEType.CREDIT_NOTE: "Nota de Crédito Electrónica",
        }[self]


# Códigos de referencia para notas (campo CodRef)
class ReferenceCode(IntEnum):
    CANCEL_DOCUMENT = 1  # anula documento de referencia
    CORRECT_TEXT = 2  # corrige glosa/texto (sin afectar montos)
    CORRECT_AMOUNTS = 3  # corrige montos


class DispatchType(IntEnum):
    """TipoDespacho: por cuenta de quién va el despacho de los bienes."""

    RECEIVER = 1  # por cuenta del receptor (cliente, o vendedor en factura de compra)
    ISSUER_TO_CUSTOMER = 2  # por cuenta del emisor, a instalaciones del cliente
    ISSUER_TO_OTHER = 3  # por cuenta del emisor, a otras instalaciones (ej. obra)

    @property
    def label(self) -> str:
        return {
            DispatchType.RECEIVER: "Por cuenta del receptor",
            DispatchType.ISSUER_TO_CUSTOMER: "Por cuenta del emisor, a instalaciones del cliente",
            DispatchType.ISSUER_TO_OTHER: "Por cuenta del emisor, a otras instalaciones",
        }[self]


class TransferType(IntEnum):
    """IndTraslado: motivo del traslado. Solo para guías de despacho.

    El valor 1 es el único que constituye venta (Art. 2 del DL 825); el resto
    son traslados que no la constituyen.
    """

    SALE = 1  # operación constituye venta
    SALE_TO_BE_MADE = 2  # ventas por efectuar
    CONSIGNMENT = 3  # consignaciones
    FREE_DELIVERY = 4  # entrega gratuita
    INTERNAL = 5  # traslados internos
    OTHER_NON_SALE = 6  # otros traslados no venta
    RETURN = 7  # devolución de mercaderías
    EXPORT_TRANSFER = 8  # traslado para exportación (no venta)
    EXPORT_SALE = 9  # venta para exportación

    @property
    def is_sale(self) -> bool:
        return self is TransferType.SALE

    @property
    def label(self) -> str:
        return {
            TransferType.SALE: "Operación constituye venta",
            TransferType.SALE_TO_BE_MADE: "Ventas por efectuar",
            TransferType.CONSIGNMENT: "Consignaciones",
            TransferType.FREE_DELIVERY: "Entrega gratuita",
            TransferType.INTERNAL: "Traslados internos",
            TransferType.OTHER_NON_SALE: "Otros traslados no venta",
            TransferType.RETURN: "Devolución de mercaderías",
            TransferType.EXPORT_TRANSFER: "Traslado para exportación (no venta)",
            TransferType.EXPORT_SALE: "Venta para exportación",
        }[self]


class ServiceIndicator(IntEnum):
    """IndServicio: obligatorio en la boleta, describe qué se está boleteando."""

    PERIODIC_SERVICE = 1  # servicios periódicos
    PERIODIC_HOME_SERVICE = 2  # servicios periódicos domiciliarios
    SALES_AND_SERVICE = 3  # boleta de ventas y servicios (el caso normal)
    THIRD_PARTY_SHOW = 4  # espectáculo por cuenta de terceros
