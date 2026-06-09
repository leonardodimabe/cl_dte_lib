"""Demo del control de folios multi-CAF integrado con el timbrado.

Muestra: cargar CAF → ver disponibilidad → asignar el siguiente folio (sin
repetir, con estado persistente) → timbrar y firmar el DTE con ese folio →
alerta de reposición → protección contra reutilización.

Uso:
    $env:PFX_PASS = "********"
    python examples/folio_management_demo.py "<CAF.xml>" "<firma.pfx>"
"""

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dte_chile import signer
from dte_chile.certificate import Certificate
from dte_chile.document_types import DTEType
from dte_chile.folios import FolioManager, FoliosExhausted
from dte_chile.models import DTE, Issuer, Item, Receiver
from dte_chile.xml_builder import build_document


def main() -> None:
    caf_path, pfx_path = sys.argv[1], sys.argv[2]
    password = os.environ.get("PFX_PASS", "")

    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)
    registry = out / "folios_registro.json"
    # DEMO: arrancamos de cero para que sea reproducible.
    # ⚠️ En uso real NUNCA se borra el registro (se perdería el control de folios).
    registry.unlink(missing_ok=True)

    fm = FolioManager(registry)
    fm.load_caf(caf_path)
    print("Registro de folios:", registry)
    print("Resumen inicial:", fm.summary())

    doc_type = DTEType.AFFECTED_INVOICE
    print(f"\nDisponibles tipo {int(doc_type)}: {fm.available_folios(int(doc_type))}")

    # Asignar el siguiente folio disponible
    folio, caf = fm.next_folio(int(doc_type))
    print(f"→ Folio asignado: {folio} (CAF RUT {caf.issuer_rut})")

    # Timbrar y firmar con ese folio
    cert = Certificate.from_pfx(pfx_path, password)
    dte = DTE(
        type=doc_type,
        folio=folio,
        issue_date=dt.date(2026, 6, 8),
        issuer=Issuer(
            caf.issuer_rut,
            "MUNOZ Y MADARIAGA LIMITADA",
            "Venta",
            479100,
            "Av. Siempre Viva 742",
            "Santiago",
            "Santiago",
        ),
        receiver=Receiver(
            "17099910-K", "Cliente Ejemplo Ltda", "Servicios", "Calle Falsa 123", "Providencia"
        ),
        items=[Item("Notebook 14", 1, 450000)],
    )
    document = build_document(dte, caf, dt.datetime(2026, 6, 8, 10, 30, 0))
    signed_dte = signer.sign_document(document, cert)
    print(
        f"  DTE folio {folio} timbrado y firmado. Firma OK: {signer.verify_signature(signed_dte)}"
    )

    # Estado tras asignar
    print(f"\nDisponibles ahora: {fm.available_folios(int(doc_type))}")
    print(f"Último asignado:   {fm.last_assigned(int(doc_type))}")
    print(f"¿Necesita reposición (umbral 10)?: {fm.needs_replenishment(int(doc_type), 10)}")

    # Protección anti-reutilización
    print("\nIntentando asignar otro folio (CAF de un solo folio)...")
    try:
        fm.next_folio(int(doc_type))
    except FoliosExhausted as ex:
        print(f"  ✋ Bloqueado correctamente: {ex}")


if __name__ == "__main__":
    main()
