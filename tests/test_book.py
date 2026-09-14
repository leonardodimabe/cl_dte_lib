"""Tests del Libro de Compras y Ventas (IECV)."""

import datetime as dt

from lxml import etree

from dte_chile import signer
from dte_chile.book import (
    BookCover,
    BookLine,
    build_book,
    sales_line,
    serialize,
)
from dte_chile.document_types import DTEType
from dte_chile.models import DTE, Issuer, Item, Receiver


def _cover_with_lines():
    return BookCover(
        issuer_rut="76158145-7",
        sender_rut="12291733-9",
        period="2026-06",
        operation_type="VENTA",
        lines=[
            BookLine(
                33,
                3027,
                dt.date(2026, 6, 8),
                "17099910-K",
                "Cliente A",
                net_amount=474000,
                vat_amount=90060,
                total_amount=564060,
            ),
            BookLine(
                33,
                3028,
                dt.date(2026, 6, 9),
                "17099910-K",
                "Cliente A",
                net_amount=100000,
                vat_amount=19000,
                total_amount=119000,
            ),
            BookLine(
                34,
                9,
                dt.date(2026, 6, 9),
                "17099910-K",
                "Cliente B",
                exempt_amount=50000,
                total_amount=50000,
            ),
        ],
    )


def test_book_signature_valid(cert):
    book = build_book(_cover_with_lines(), cert, dt.datetime(2026, 7, 1, 9, 0, 0))
    reparse = etree.fromstring(serialize(book))
    assert signer.verify_signatures(reparse) == [True]


def test_summary_groups_by_type(cert):
    book = build_book(_cover_with_lines(), cert, dt.datetime(2026, 7, 1, 9, 0, 0))
    reparse = etree.fromstring(serialize(book))

    totals = reparse.findall(".//{*}TotalesPeriodo")
    by_type = {t.findtext("{*}TpoDoc"): t for t in totals}
    assert set(by_type) == {"33", "34"}

    t33 = by_type["33"]
    assert t33.findtext("{*}TotDoc") == "2"
    assert t33.findtext("{*}TotMntNeto") == "574000"  # 474000 + 100000
    assert t33.findtext("{*}TotMntIVA") == "109060"  # 90060 + 19000
    assert t33.findtext("{*}TotMntTotal") == "683060"

    t34 = by_type["34"]
    assert t34.findtext("{*}TotDoc") == "1"
    assert t34.findtext("{*}TotMntExe") == "50000"


def test_detail_per_document(cert):
    book = build_book(_cover_with_lines(), cert, dt.datetime(2026, 7, 1, 9, 0, 0))
    reparse = etree.fromstring(serialize(book))
    details = reparse.findall(".//{*}Detalle")
    assert len(details) == 3
    assert reparse.findtext(".//{*}PeriodoTributario") == "2026-06"
    assert reparse.findtext(".//{*}TipoOperacion") == "VENTA"


def test_line_from_dte():
    dte = DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=3027,
        issue_date=dt.date(2026, 6, 8),
        issuer=Issuer("76158145-7", "Emisor", "g", 479100, "dir", "Santiago"),
        receiver=Receiver("17099910-K", "Cliente A", "g", "dir", "Providencia"),
        items=[Item("Item", 1, 474000)],
    )
    line = sales_line(dte)
    assert line.doc_type == 33 and line.folio == 3027
    assert line.rut == "17099910-K" and line.business_name == "Cliente A"
    assert line.net_amount == 474000 and line.vat_amount == 90060
    assert line.total_amount == 564060  # 474000 + 90060


def test_un_libro_grande_no_deja_lineas_demasiado_largas(cert):
    """«ENV - 3 - Error en Schema - CHR-00002: Line too long».

    El SII rechazó un libro de 26 detalles cuya única línea tenía 10.848
    caracteres. El límite no aparece en ningún XSD, así que el esquema lo daba
    por bueno y el rechazo llegaba después de gastar un TrackID.
    """
    import datetime as dt

    from dte_chile.book import BookCover, BookLine, build_book, serialize
    from dte_chile.validation import MAX_LINE_LENGTH

    lineas = [
        BookLine(
            doc_type=33,
            folio=n,
            date=dt.date(2026, 9, 14),
            rut="76008900-1",
            business_name="AGROINDUSTRIAL Y COMERCIAL SUPERFRUIT LIMITADA",
            net_amount=1000,
            vat_amount=190,
            total_amount=1190,
        )
        for n in range(1, 41)
    ]
    cover = BookCover(
        issuer_rut="77262159-0",
        sender_rut="12291733-9",
        period="2026-09",
        book_type="ESPECIAL",
        notification_folio=5038171,
        lines=lineas,
    )

    xml = serialize(build_book(cover, cert, dt.datetime(2026, 9, 14, 12, 0, 0)))

    assert max(len(line) for line in xml.splitlines()) <= MAX_LINE_LENGTH
    # Y las firmas siguen valiendo: los saltos se añaden ANTES de firmar.
    from lxml import etree

    from dte_chile import signer

    assert all(signer.verify_signatures(etree.fromstring(xml)))
