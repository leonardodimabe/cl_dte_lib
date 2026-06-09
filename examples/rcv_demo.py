"""Descarga las compras (o ventas) del RCV del SII para un período.

⚠️ PRODUCCIÓN, datos reales. El certificado debe estar autorizado para la empresa.

Uso:
    $env:PFX_PASS = "********"
    python examples/rcv_demo.py "<firma.pfx>" 76158145-7 202505 COMPRA
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dte_chile.certificate import Certificate
from dte_chile.rcv import RCVClient


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def main() -> None:
    pfx_path = sys.argv[1]
    rut = sys.argv[2] if len(sys.argv) > 2 else "76158145-7"
    period = sys.argv[3] if len(sys.argv) > 3 else "202505"
    operation = sys.argv[4].upper() if len(sys.argv) > 4 else "COMPRA"

    cert = Certificate.from_pfx(pfx_path, os.environ.get("PFX_PASS", ""))
    print(f"RCV {operation} | RUT {rut} | período {period}")

    with RCVClient(cert) as rcv:
        print("→ Autenticando (TLS mutuo) contra el SII...")
        rcv.authenticate()
        print("  Sesión establecida.")

        print("→ Descargando detalle...")
        by_state = rcv.detail(rut, period, operation)

    rut_col = "RUT Proveedor" if operation == "COMPRA" else "Rut cliente"
    total_docs = 0
    for state, records in by_state.items():
        total = sum(_to_int(rec.get("Monto Total")) for rec in records)
        total_docs += len(records)
        print(f"\n[{state}] {len(records)} documento(s) — total ${total:,}")
        for rec in records[:10]:
            print(
                f"  {rec.get('Tipo Doc'):>3} folio {rec.get('Folio'):<8} "
                f"{rec.get(rut_col, ''):<12} {rec.get('Razon Social', '')[:30]:<30} "
                f"${_to_int(rec.get('Monto Total')):>12,}"
            )
        if len(records) > 10:
            print(f"  ... y {len(records) - 10} más")

    print(f"\nTotal documentos: {total_docs}")


if __name__ == "__main__":
    main()
