"""Motor de facturación electrónica (DTE) para Chile - SII.

Paquete standalone, sin dependencia de Odoo, para generar, timbrar, firmar y
enviar Documentos Tributarios Electrónicos.
"""

from .bhe import BheClient, BheDocument
from .document_types import DTEType
from .errors import BheError, DteError, RcvError, SiiAuthError, SiiError, SiiUploadError
from .folios import FolioError, FolioManager, FoliosExhausted
from .models import DTE, Issuer, Item, Receiver, Reference
from .rcv import RCVClient, RcvDocument, to_book_lines
from .rut import Rut, format_rut, validate_rut

__all__ = [
    "Rut",
    "validate_rut",
    "format_rut",
    "Issuer",
    "Receiver",
    "Item",
    "Reference",
    "DTE",
    "DTEType",
    "FolioManager",
    "FolioError",
    "FoliosExhausted",
    "RCVClient",
    "RcvDocument",
    "to_book_lines",
    "BheClient",
    "BheDocument",
    "DteError",
    "SiiError",
    "SiiAuthError",
    "SiiUploadError",
    "RcvError",
    "BheError",
]

__version__ = "0.3.0"
