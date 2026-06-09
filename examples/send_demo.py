"""Demo end-to-end: timbrar → firmar → sobre EnvioDTE → subir a Maullín.

1. Carga CAF + certificado.
2. Construye un DTE 33, lo timbra (TED) y lo firma (XMLDSig).
3. Lo empaqueta en un sobre EnvioDTE (SetDTE + Carátula) y firma el SetDTE.
4. Verifica todas las firmas tras serializar→re-parsear (como el SII).
5. Valida contra el XSD y sube el sobre con DTEUpload → TrackID.
6. Consulta el estado del envío.

Uso:
    $env:PFX_PASS = "********"
    python examples/send_demo.py "<CAF.xml>" "<firma.pfx>"
"""

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lxml import etree

from _common import validate_xsd_or_abort
from dte_chile import signer
from dte_chile.caf import load_caf
from dte_chile.certificate import Certificate
from dte_chile.document_types import DTEType
from dte_chile.envelope import Cover, build_envelope, serialize
from dte_chile.models import DTE, Issuer, Item, Receiver
from dte_chile.sii_client import Environment, SIIClient
from dte_chile.xml_builder import build_document


def main() -> None:
    if len(sys.argv) < 3:
        print("Uso: python examples/send_demo.py <CAF.xml> <firma.pfx>")
        sys.exit(2)
    caf_path, pfx_path = sys.argv[1], sys.argv[2]
    password = os.environ.get("PFX_PASS", "")

    caf = load_caf(caf_path)
    cert = Certificate.from_pfx(pfx_path, password)
    print(f"CAF: tipo {caf.doc_type}, RUT {caf.issuer_rut}, folio {caf.folio_from}")
    print(f"Certificado (envía): RUT {cert.rut}")

    # 1) DTE timbrado + firmado
    dte = DTE(
        type=DTEType(caf.doc_type),
        folio=caf.folio_from,
        issue_date=dt.date(2026, 6, 8),
        issuer=Issuer(
            caf.issuer_rut,
            "MUNOZ Y MADARIAGA LIMITADA",
            "Venta al por menor",
            479100,
            "Av. Siempre Viva 742",
            "Santiago",
            "Santiago",
        ),
        receiver=Receiver(
            "17099910-K", "Cliente Ejemplo Ltda", "Servicios", "Calle Falsa 123", "Providencia"
        ),
        items=[Item("Notebook 14", 1, 450000), Item("Mouse", 2, 12000)],
    )
    ts = dt.datetime(2026, 6, 8, 10, 30, 0)
    document = build_document(dte, caf, ts)
    signed_dte = signer.sign_document(document, cert)
    print(f"DTE folio {dte.folio} timbrado y firmado. Total ${dte.total_amount:,}")

    # 2) Sobre EnvioDTE
    cover = Cover(
        issuer_rut=caf.issuer_rut,
        sender_rut=cert.rut,
        resolution_date=dt.date(2026, 6, 8),
        resolution_number=0,  # certificación
        subtotals=[(int(dte.type), 1)],
    )
    envelope = build_envelope([signed_dte], cover, cert, ts)
    xml_envelope = serialize(envelope)

    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)
    target = out / f"envio_{caf.issuer_rut}_folio{dte.folio}.xml"
    target.write_bytes(xml_envelope)
    print(f"Sobre EnvioDTE: {target} ({len(xml_envelope)} bytes)")

    # 3) Verificar firmas tras round-trip (serializar → re-parsear)
    reparse = etree.fromstring(xml_envelope)
    signatures = signer.verify_signatures(reparse)
    print(f"Firmas verificadas (SetDTE + DTE): {signatures}")

    # 4) Validar contra el XSD del SII ANTES de subir (aborta si no cumple).
    validate_xsd_or_abort(xml_envelope, "EnvioDTE")

    # 5) Subir a Maullín
    print("\n→ Autenticando y subiendo a Maullín (DTEUpload)...")
    client = SIIClient(cert, Environment.CERTIFICATION)
    res = client.send_dte(xml_envelope, caf.issuer_rut, cert.rut)
    print(f"  Estado upload: {res.status}")
    print(f"  TrackID:       {res.track_id}")
    if res.status == "ERROR_NO_XML":
        resp_file = out / "upload_response.html"
        resp_file.write_text(res.detail, encoding="utf-8")
        print(f"  Respuesta completa guardada en: {resp_file}")
    else:
        print(f"  Respuesta:     {res.detail}")

    # 6) Consultar estado (si hubo TrackID)
    if res.track_id:
        print("\n→ Consultando estado del envío...")
        status = client.query_status(res.track_id, caf.issuer_rut)
        print(f"  Estado: {status.status} | {status.detail}")


if __name__ == "__main__":
    main()
