"""Demo completo: CAF + TED (timbre) + firma XMLDSig de un DTE 33.

Carga un archivo CAF real (folios autorizados), timbra el documento con un folio
del rango y lo firma con el certificado. Genera el <DTE> firmado listo para
empaquetar en un sobre EnvioDTE.

Uso:
    $env:PFX_PASS = "********"
    python examples/stamp_demo.py "C:\\ruta\\CAF.xml" "C:\\ruta\\firma.pfx"
"""

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lxml import etree

from dte_chile import signer
from dte_chile.caf import load_caf
from dte_chile.certificate import Certificate
from dte_chile.document_types import DTEType
from dte_chile.models import DTE, Issuer, Item, Receiver
from dte_chile.xml_builder import build_document


def main() -> None:
    if len(sys.argv) < 3:
        print("Uso: python examples/stamp_demo.py <CAF.xml> <firma.pfx>")
        sys.exit(2)
    caf_path, pfx_path = sys.argv[1], sys.argv[2]
    password = os.environ.get("PFX_PASS", "")

    caf = load_caf(caf_path)
    print(
        f"CAF cargado: tipo {caf.doc_type}, RUT {caf.issuer_rut}, "
        f"folios {caf.folio_from}–{caf.folio_to}"
    )

    cert = Certificate.from_pfx(pfx_path, password)
    print(f"Certificado: RUT titular {cert.rut}")

    # El emisor DEBE ser el RUT del CAF (el timbre amarra folio↔RUT).
    dte = DTE(
        type=DTEType(caf.doc_type),
        folio=caf.folio_from,  # primer folio del rango
        issue_date=dt.date(2026, 6, 8),
        issuer=Issuer(
            rut=caf.issuer_rut,
            business_name="Empresa Demo SpA",
            activity="Venta al por menor",
            economic_activity=479100,
            address="Av. Siempre Viva 742",
            commune="Santiago",
            city="Santiago",
        ),
        receiver=Receiver(
            rut="17099910-K",
            business_name="Cliente Ejemplo Ltda",
            activity="Servicios",
            address="Calle Falsa 123",
            commune="Providencia",
        ),
        items=[
            Item("Notebook 14", quantity=1, unit_price=450000),
            Item("Mouse inalambrico", quantity=2, unit_price=12000),
        ],
    )

    timestamp = dt.datetime(2026, 6, 8, 10, 30, 0)
    document = build_document(dte, caf, timestamp)
    print(f"Documento timbrado: folio {dte.folio}, total ${dte.total_amount:,}")

    # Verifica que el TED quedó con su firma RSA.
    ted = document.find("TED")
    frmt = ted.find("FRMT") if ted is not None else None
    print(
        "TED presente:",
        ted is not None,
        "| FRMT (firma timbre):",
        bool(frmt is not None and frmt.text),
    )

    signed_dte = signer.sign_document(document, cert)
    print("Firma XMLDSig verifica:", signer.verify_signature(signed_dte))

    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)
    target = out / f"dte_{int(dte.type)}_folio{dte.folio}_firmado.xml"
    target.write_bytes(etree.tostring(signed_dte, xml_declaration=True, encoding="ISO-8859-1"))
    print(f"XML firmado: {target}")


if __name__ == "__main__":
    main()
