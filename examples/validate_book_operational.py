"""Validación operativa del Libro de Compra y Venta con datos REALES del RCV.

Flujo completo end-to-end:
  RCV (SII) → to_book_lines → build_book → firmar → validar XSD

Ejercita COMPRA y VENTA del período contra el SII real, arma el LibroCompraVenta
de cada uno, lo firma y lo valida contra el XSD oficial. Si ambos validan, el
libro está operativo.

⚠️ PRODUCCIÓN. Uso:
    $env:PFX_PASS = "********"
    python examples/validate_book_operational.py "<firma.pfx>" 76158145-7 202505
"""

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lxml import etree

from _common import validate_xsd_or_abort
from dte_chile import signer
from dte_chile.book import BookCover, build_book, serialize
from dte_chile.certificate import Certificate
from dte_chile.rcv import RCVClient, to_book_lines


def main() -> None:
    pfx_path = sys.argv[1]
    rut = sys.argv[2] if len(sys.argv) > 2 else "76158145-7"
    period = sys.argv[3] if len(sys.argv) > 3 else "202505"  # AAAAMM
    book_period = f"{period[:4]}-{period[4:]}"  # AAAA-MM

    cert = Certificate.from_pfx(pfx_path, os.environ.get("PFX_PASS", ""))
    ts = dt.datetime(2026, 6, 9, 9, 0, 0)
    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)

    todo_ok = True
    with RCVClient(cert) as rcv:
        rcv.authenticate()
        print(f"Sesión SII establecida. RUT {rut}, período {period}\n")

        for operation in ("COMPRA", "VENTA"):
            by_state = rcv.detail(rut, period, operation)
            records = by_state.get("REGISTRO", [])
            lines = to_book_lines(records, operation)
            print(
                f"[{operation}] {len(lines)} documento(s) en REGISTRO "
                f"(total ${sum(ln.total_amount for ln in lines):,})"
            )

            cover = BookCover(
                issuer_rut=rut,
                sender_rut=cert.rut,
                period=book_period,
                operation_type=operation,
                resolution_number=0,
                lines=lines,
            )
            book = build_book(cover, cert, ts)
            xml = serialize(book)

            destino = out / f"libro_{operation.lower()}_{book_period}.xml"
            destino.write_bytes(xml)

            firmas = signer.verify_signatures(etree.fromstring(xml))
            print(f"  Firma: {firmas} | Archivo: {destino.name} ({len(xml)} bytes)")
            validate_xsd_or_abort(xml, f"Libro de {operation} {book_period}")
            todo_ok = todo_ok and firmas == [True]

    print(
        "\n"
        + (
            "✅ Libro de Compra y Venta OPERATIVO (ambos válidos y firmados)."
            if todo_ok
            else "❌ Revisar."
        )
    )


if __name__ == "__main__":
    main()
