"""Demo de intercambio: recibir un EnvioDTE y responder los acuses firmados.

Simula el rol de RECEPTOR: lee un EnvioDTE, genera y firma:
  1. RespuestaDTE  (acuse de recibo del envío y aceptación comercial)
  2. EnvioRecibos  (recibo de mercaderías, Ley 19.983)

Uso:
    $env:PFX_PASS = "********"
    python examples/exchange_demo.py "<EnvioDTE.xml>" "<firma.pfx>"
"""

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _common import validate_xsd_or_abort
from dte_chile import exchange as ix
from dte_chile.certificate import Certificate


def main() -> None:
    envelope_path, pfx_path = sys.argv[1], sys.argv[2]
    cert = Certificate.from_pfx(pfx_path, os.environ.get("PFX_PASS", ""))

    raw = Path(envelope_path).read_bytes()
    envelope = ix.parse_envelope(raw, Path(envelope_path).name)
    print(f"EnvioDTE recibido: emisor {envelope.issuer_rut} → receptor {envelope.receiver_rut}")
    print(f"  SetDTE ID: {envelope.set_dte_id} | Digest: {envelope.digest}")
    for d in envelope.documents:
        print(f"  DTE tipo {d.doc_type} folio {d.folio} por ${d.total_amount:,}")

    ts = dt.datetime(2026, 6, 8, 11, 0, 0)
    contact = ix.Contact(name="Recepción", email="recepcion@empresa.cl")

    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)

    # 1a) Acuse de recibo del envío (RecepcionEnvio)
    xml_ack = ix.serialize(ix.build_receipt_acknowledgment(envelope, cert, ts, contact=contact))
    validate_xsd_or_abort(xml_ack, "Acuse de recibo (RespuestaDTE)")
    (out / "respuesta_dte.xml").write_bytes(xml_ack)

    # 1b) Resultado comercial (aceptación)
    xml_result = ix.serialize(
        ix.build_result_response(envelope, cert, ts, accept=True, contact=contact)
    )
    validate_xsd_or_abort(xml_result, "Resultado (RespuestaDTE)")
    (out / "respuesta_resultado.xml").write_bytes(xml_result)

    # 2) EnvioRecibos (recibo de mercaderías)
    xml_receipts = ix.serialize(
        ix.build_receipts_envelope(envelope, cert, ts, location="Bodega Central", contact=contact)
    )
    validate_xsd_or_abort(xml_receipts, "EnvioRecibos")
    (out / "envio_recibos.xml").write_bytes(xml_receipts)

    print("\n✅ Acuses generados, validados y firmados.")


if __name__ == "__main__":
    main()
