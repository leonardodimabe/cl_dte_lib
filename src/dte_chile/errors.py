"""Jerarquía de excepciones del motor.

Todas heredan de ``DteError``, así un cliente (p.ej. la capa FastAPI o el módulo
Odoo) puede capturar una sola base y, si quiere, distinguir el caso concreto.
"""

from __future__ import annotations


class DteError(Exception):
    """Base de todas las excepciones del motor DTE."""


class SiiError(DteError):
    """Error comunicándose con el SII (red, SOAP, respuesta inválida)."""


class SiiAuthError(SiiError):
    """Fallo de autenticación con el SII (semilla, token o sesión web)."""


class SiiUploadError(SiiError):
    """El SII rechazó el envío del sobre (DTEUpload)."""


class RcvError(SiiError):
    """Error consultando el Registro de Compra y Venta."""
