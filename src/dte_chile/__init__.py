"""Motor de facturación electrónica (DTE) para Chile - SII.

Paquete standalone, sin dependencia de Odoo, para generar, timbrar, firmar y
enviar Documentos Tributarios Electrónicos.
"""

from . import customs_codes
from .bhe import BheClient, BheDocument
from .document_types import DispatchType, DTEType, ServiceIndicator, TransferType
from .errors import BheError, DteError, RcvError, SiiAuthError, SiiError, SiiUploadError
from .export_invoice import Customs, ExportDocument, ExportItem, PackageGroup
from .folio_report import FolioReportCover, ReportLine
from .folios import FolioError, FolioManager, FoliosExhausted
from .guide_book import GuideBookCover, GuideBookLine, VoidStatus
from .models import (
    DTE,
    Driver,
    GlobalDiscount,
    Issuer,
    Item,
    Receiver,
    Reference,
    Retention,
    Transport,
)
from .parser import ParsedDocument, ParseError, parse_documents
from .rcv import RCVClient, RcvDocument, to_book_lines
from .receipt import ReceiptCover
from .receipt_client import ReceiptClient, ReceiptEnvironment
from .rut import Rut, format_rut, validate_rut
from .settlement import Commission, Settlement, SettlementLine

__all__ = [
    "Rut",
    "validate_rut",
    "format_rut",
    "Issuer",
    "Receiver",
    "GlobalDiscount",
    "Item",
    "Reference",
    "DTE",
    "DTEType",
    "DispatchType",
    "TransferType",
    "ServiceIndicator",
    "ReceiptCover",
    "Settlement",
    "ExportDocument",
    "ExportItem",
    "Customs",
    "customs_codes",
    "PackageGroup",
    "SettlementLine",
    "Commission",
    "ReceiptClient",
    "ReceiptEnvironment",
    "FolioReportCover",
    "ReportLine",
    "Driver",
    "Transport",
    "Retention",
    "GuideBookCover",
    "GuideBookLine",
    "VoidStatus",
    "ParsedDocument",
    "ParseError",
    "parse_documents",
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
