"""Demo: genera la representación impresa (HTML + PDF417) de un DTE timbrado.

Uso:
    $env:PFX_PASS = "********"
    python examples/printout_demo.py "<CAF.xml>" "<firma.pfx>"
"""

import datetime as dt
import os
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dte_chile import representation as rep
from dte_chile import signer
from dte_chile.caf import load_caf
from dte_chile.certificate import Certificate
from dte_chile.document_types import DTEType
from dte_chile.models import DTE, Issuer, Item, Receiver
from dte_chile.xml_builder import build_document


def main() -> None:
    caf_path, pfx_path = sys.argv[1], sys.argv[2]
    password = os.environ.get("PFX_PASS", "")

    caf = load_caf(caf_path)
    cert = Certificate.from_pfx(pfx_path, password)

    dte = DTE(
        type=DTEType(caf.doc_type),
        folio=caf.folio_from,
        issue_date=dt.date(2026, 6, 8),
        issuer=Issuer(
            caf.issuer_rut,
            "MUNOZ Y MADARIAGA LIMITADA",
            "Venta al por menor de artículos",
            479100,
            "Av. Siempre Viva 742",
            "Santiago",
            "Santiago",
        ),
        receiver=Receiver(
            "17099910-K",
            "Cliente Ejemplo Ltda",
            "Servicios de consultoría",
            "Calle Falsa 123",
            "Providencia",
        ),
        items=[Item('Notebook 14"', 1, 450000), Item("Mouse inalámbrico", 2, 12000)],
    )

    document = build_document(dte, caf, dt.datetime(2026, 6, 8, 10, 30, 0))
    signed_dte = signer.sign_document(document, cert)  # timbra + firma

    # Representación impresa (en certificación: resolución N° 0).
    # Se pasa el <DTE> firmado para extraer el TED literal tal como se transmite.
    resolution = rep.ResolutionInfo(number=0, date=dt.date(2026, 6, 8), sii_office="SANTIAGO")
    html = rep.generate_html(dte, signed_dte, resolution)

    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)

    html_target = out / f"representacion_folio{dte.folio}.html"
    rep.save_html(html, html_target)

    # También guardamos el PDF417 suelto, por si se requiere aparte.
    png = rep.generate_pdf417_png(rep.ted_bytes(signed_dte))
    png_target = out / f"timbre_folio{dte.folio}.png"
    png_target.write_bytes(png)

    print(f"Representación impresa: {html_target}")
    print(f"Timbre PDF417 (PNG):    {png_target} ({len(png)} bytes)")
    print("Abriendo en el navegador (Ctrl+P → Guardar como PDF para la muestra impresa)...")
    webbrowser.open(html_target.as_uri())


if __name__ == "__main__":
    main()
