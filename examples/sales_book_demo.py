"""Demo: genera y firma un Libro de Ventas (IECV) del período.

Uso:
    $env:PFX_PASS = "********"
    python examples/sales_book_demo.py "<firma.pfx>"
"""

import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lxml import etree

from _common import validate_xsd_or_abort
from dte_chile import signer
from dte_chile.book import BookCover, BookLine, build_book, serialize
from dte_chile.certificate import Certificate


def main() -> None:
    cert = Certificate.from_pfx(sys.argv[1], os.environ.get("PFX_PASS", ""))

    cover = BookCover(
        issuer_rut="76158145-7",
        sender_rut=cert.rut,
        period="2026-06",
        operation_type="VENTA",
        resolution_number=0,  # certificación
        lines=[
            BookLine(
                33,
                3027,
                dt.date(2026, 6, 8),
                "17099910-K",
                "Cliente Ejemplo",
                net_amount=474000,
                vat_amount=90060,
                total_amount=564060,
            ),
            BookLine(
                33,
                3028,
                dt.date(2026, 6, 9),
                "60803000-K",
                "Otro Cliente",
                net_amount=100000,
                vat_amount=19000,
                total_amount=119000,
            ),
            BookLine(
                61,
                5,
                dt.date(2026, 6, 10),
                "17099910-K",
                "Cliente Ejemplo",
                net_amount=50000,
                vat_amount=9500,
                total_amount=59500,
                voided=True,
            ),
        ],
    )

    book = build_book(cover, cert, dt.datetime(2026, 7, 1, 9, 0, 0))
    xml = serialize(book)
    validate_xsd_or_abort(xml, f"Libro de Ventas {cover.period}")

    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)
    target = out / f"libro_ventas_{cover.period}.xml"
    target.write_bytes(xml)

    reparse = etree.fromstring(xml)
    print(f"Libro de Ventas período {cover.period}")
    for t in reparse.findall(".//{*}TotalesPeriodo"):
        print(
            f"  Tipo {t.findtext('{*}TpoDoc')}: "
            f"{t.findtext('{*}TotDoc')} doc, total ${int(t.findtext('{*}TotMntTotal')):,}"
        )
    print(f"Firma del libro: {signer.verify_signatures(reparse)}")
    print(f"Archivo: {target} ({len(xml)} bytes)")


if __name__ == "__main__":
    main()
