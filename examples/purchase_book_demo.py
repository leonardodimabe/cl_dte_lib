"""Demo del Libro de COMPRAS (IECV) con datos de ejemplo.

Mismo motor que el de ventas, con TipoOperacion=COMPRA y la contraparte = el
proveedor. Sustituye las líneas de ejemplo por las compras reales (de Odoo o del
RCV del SII) cuando tengas la fuente conectada.

Uso:
    $env:PFX_PASS = "********"
    python examples/purchase_book_demo.py "<firma.pfx>"
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

    # Compras de ejemplo del período (proveedores como contraparte).
    cover = BookCover(
        issuer_rut="76158145-7",
        sender_rut=cert.rut,
        period="2026-06",
        operation_type="COMPRA",
        resolution_number=0,
        lines=[
            BookLine(
                33,
                12045,
                dt.date(2026, 6, 3),
                "96874030-K",
                "Proveedor Insumos SA",
                net_amount=320000,
                vat_amount=60800,
                total_amount=380800,
            ),
            BookLine(
                33,
                8891,
                dt.date(2026, 6, 5),
                "77123456-7",
                "Distribuidora Sur Ltda",
                net_amount=150000,
                vat_amount=28500,
                total_amount=178500,
            ),
            BookLine(
                34,
                4521,
                dt.date(2026, 6, 7),
                "61703000-K",
                "Servicios Exentos EIRL",
                exempt_amount=90000,
                total_amount=90000,
            ),
        ],
    )

    book = build_book(cover, cert, dt.datetime(2026, 7, 1, 9, 0, 0))
    xml = serialize(book)
    validate_xsd_or_abort(xml, f"Libro de Compras {cover.period}")

    out = Path(__file__).resolve().parents[1] / "out"
    out.mkdir(exist_ok=True)
    target = out / f"libro_compras_{cover.period}.xml"
    target.write_bytes(xml)

    reparse = etree.fromstring(xml)
    print(f"\nLibro de COMPRAS período {cover.period}")
    for t in reparse.findall(".//{*}TotalesPeriodo"):
        print(
            f"  Tipo {t.findtext('{*}TpoDoc')}: {t.findtext('{*}TotDoc')} doc | "
            f"neto ${int(t.findtext('{*}TotMntNeto') or 0):,} | "
            f"IVA ${int(t.findtext('{*}TotMntIVA') or 0):,} | "
            f"total ${int(t.findtext('{*}TotMntTotal')):,}"
        )
    print(f"Firma del libro: {signer.verify_signatures(reparse)}")
    print(f"Archivo: {target}")


if __name__ == "__main__":
    main()
