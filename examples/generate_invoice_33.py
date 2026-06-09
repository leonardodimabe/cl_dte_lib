"""Ejemplo end-to-end del armado de una Factura Electrónica (33).

Muestra cómo se compone un DTE en el dominio y se serializa el <Documento>.
Las partes que requieren secretos (CAF, certificado .pfx) están marcadas: para
correr el timbrado y la firma necesitas archivos reales.

Ejecutar:
    python examples/generate_invoice_33.py
"""

import datetime as dt
import sys
from pathlib import Path

# Permite ejecutar el ejemplo sin instalar el paquete (usa src/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lxml import etree

from dte_chile.document_types import DTEType
from dte_chile.models import DTE, Issuer, Item, Receiver
from dte_chile.xml_builder import _header, _items


def main() -> None:
    dte = DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=101,
        issue_date=dt.date(2026, 6, 8),
        issuer=Issuer(
            rut="76192083-9",
            business_name="Comercial Ejemplo SpA",
            activity="Venta al por menor de artículos",
            economic_activity=479100,
            address="Av. Siempre Viva 742",
            commune="Santiago",
            city="Santiago",
        ),
        receiver=Receiver(
            rut="17099910-K",
            business_name="Cliente Ejemplo Ltda",
            activity="Servicios de consultoría",
            address="Calle Falsa 123",
            commune="Providencia",
        ),
        items=[
            Item('Notebook 14"', quantity=1, unit_price=450000),
            Item("Mouse inalámbrico", quantity=2, unit_price=12000),
        ],
    )

    dte.validate()
    print(f"Tipo .........: {dte.type} ({dte.type.label})")
    print(f"Folio ........: {dte.folio}")
    print(f"Neto .........: ${dte.net_amount:,}")
    print(f"IVA (19%) ....: ${dte.vat:,}")
    print(f"TOTAL ........: ${dte.total_amount:,}")
    print("-" * 50)

    # Encabezado + detalle (sin TED/firma, que requieren CAF y certificado).
    doc = etree.Element("Documento", ID=f"F{dte.folio}T{int(dte.type)}")
    _header(doc, dte)
    _items(doc, dte)
    print(etree.tostring(doc, pretty_print=True, encoding="unicode"))

    print("\nPara timbrar y firmar:")
    print("  1. load_caf('caf/CAF_33.xml')             # rango de folios + llave")
    print("  2. Certificate.from_pfx('certs/cert.pfx', '****')")
    print("  3. build_document(dte, caf, datetime.now())")
    print("  4. sign_document(doc, cert)               # requiere xmlsec")


if __name__ == "__main__":
    main()
