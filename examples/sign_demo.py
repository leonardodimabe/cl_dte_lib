"""Demo: cargar un certificado .pfx real y firmar un <Documento> con XMLDSig.

Valida el flujo de firma sin necesidad de CAF (omite el TED, que no es requisito
para probar la firma enveloped). NO envía nada al SII.

Uso:
    set PFX_PASS=********              (o $env:PFX_PASS en PowerShell)
    python examples/sign_demo.py "C:\\ruta\\firma.pfx"
"""

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lxml import etree

from dte_chile import signer
from dte_chile.certificate import Certificate
from dte_chile.document_types import DTEType
from dte_chile.models import DTE, Issuer, Item, Receiver
from dte_chile.xml_builder import _header, _items


def build_demo_doc() -> etree._Element:
    dte = DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=101,
        issue_date=dt.date(2026, 6, 8),
        issuer=Issuer(
            "76192083-9",
            "Comercial Ejemplo SpA",
            "Venta",
            479100,
            "Av. Siempre Viva 742",
            "Santiago",
            "Santiago",
        ),
        receiver=Receiver(
            "17099910-K", "Cliente Ejemplo Ltda", "Servicios", "Calle Falsa 123", "Providencia"
        ),
        items=[Item("Notebook 14", quantity=1, unit_price=450000)],
    )
    doc = etree.Element("Documento", ID=f"F{dte.folio}T{int(dte.type)}")
    _header(doc, dte)
    _items(doc, dte)
    return doc


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PFX_PATH", "")
    password = os.environ.get("PFX_PASS", "")
    if not path or not password:
        print("Falta ruta del .pfx (arg1) o PFX_PASS en entorno.")
        sys.exit(2)

    cert = Certificate.from_pfx(path, password)
    print(f"Certificado cargado. RUT titular: {cert.rut or '(no detectado)'}")

    doc = build_demo_doc()
    signed_dte = signer.sign_document(doc, cert)

    # ¿Se insertó el nodo <Signature>?
    sig = signed_dte.find(".//{http://www.w3.org/2000/09/xmldsig#}Signature")
    print("Nodo <Signature> insertado:", sig is not None)

    # Verificación criptográfica de la firma recién creada.
    print("Firma verifica correctamente:", signer.verify_signature(signed_dte))

    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)
    target = out / "dte_33_firmado.xml"
    target.write_bytes(etree.tostring(signed_dte, xml_declaration=True, encoding="ISO-8859-1"))
    print(f"XML firmado escrito en: {target}")


if __name__ == "__main__":
    main()
