"""Tests de las partes puras del cliente RCV (sin red)."""

import datetime as dt

from dte_chile.rcv import RcvDocument, _normalize, _parse_csv, to_book_lines

_CSV_PURCHASE = [
    "Nro;Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Monto Exento;"
    "Monto Neto;Monto IVA Recuperable;Monto Total",
    "1;33;77073851-2;STARLINK CHILE SPA;1356612;15/05/2026;0;197479;37521;235000",
    "2;34;76058647-1;VERISURE CHILE SPA;10564545;20/05/2026;74877;0;0;74877",
]


def test_parse_csv():
    records = _parse_csv(_CSV_PURCHASE)
    assert len(records) == 2
    assert records[0]["Tipo Doc"] == "33"
    assert records[0]["Razon Social"] == "STARLINK CHILE SPA"
    assert records[1]["Folio"] == "10564545"


def test_parse_csv_empty():
    assert _parse_csv([]) == []
    assert _parse_csv(["solo headers"]) == []


def test_to_book_lines_purchase():
    lines = to_book_lines(_parse_csv(_CSV_PURCHASE), operation="COMPRA")
    assert len(lines) == 2

    line0 = lines[0]
    assert line0.doc_type == 33 and line0.folio == 1356612
    assert line0.date == dt.date(2026, 5, 15)
    assert line0.rut == "77073851-2" and line0.business_name == "STARLINK CHILE SPA"
    assert line0.net_amount == 197479 and line0.vat_amount == 37521
    assert line0.total_amount == 235000

    line1 = lines[1]  # exenta
    assert line1.doc_type == 34 and line1.exempt_amount == 74877 and line1.vat_amount == 0


# En ventas el SII usa "Monto total" (t minúscula) y "Monto IVA" (sin "Recuperable").
_CSV_SALES = [
    "Nro;Tipo Doc;Rut cliente;Razon Social;Folio;Fecha Docto;Monto Exento;"
    "Monto Neto;Monto IVA;Monto total",
    "1;33;99540010-3;VINA SANTA CRUZ S.A.;4584;01/05/2025;0;468983;89107;558090",
]


def test_to_book_lines_sales_total_column():
    lines = to_book_lines(_parse_csv(_CSV_SALES), operation="VENTA")
    assert len(lines) == 1
    assert lines[0].rut == "99540010-3"
    assert lines[0].net_amount == 468983 and lines[0].vat_amount == 89107
    assert lines[0].total_amount == 558090  # antes salía 0 por el casing del header


# --- documents() / RcvDocument ---
_CSV_PURCHASE_DUP = [
    "Nro;Tipo Doc;RUT Proveedor;Razon Social;Folio;Fecha Docto;Fecha Recepcion;"
    "Monto Exento;Monto Neto;Monto IVA Recuperable;Monto Total",
    "1;33;99520000-7;COPEC S.A.;31976502;02/06/2026;03/06/2026 10:15:00;0;582042;110588;692630",
    "2;33;99520000-7;COPEC S.A.;31976502;02/06/2026;03/06/2026 10:15:00;0;0;0;0",  # línea extra
]


def test_normalize_purchase_document():
    docs = _normalize({"REGISTRO": _parse_csv(_CSV_PURCHASE)}, "COMPRA")
    assert len(docs) == 2
    d0 = docs[0]
    assert isinstance(d0, RcvDocument)
    assert d0.operation == "COMPRA" and d0.state == "REGISTRO"
    assert d0.doc_type == 33 and d0.folio == 1356612
    assert d0.counterpart_rut == "77073851-2"
    assert d0.counterpart_name == "STARLINK CHILE SPA"
    assert d0.net_amount == 197479 and d0.vat_amount == 37521 and d0.total_amount == 235000


def test_normalize_dedups_extra_tax_lines():
    docs = _normalize({"REGISTRO": _parse_csv(_CSV_PURCHASE_DUP)}, "COMPRA")
    assert len(docs) == 1  # dos filas, mismo (tipo, rut, folio) → un doc
    assert docs[0].total_amount == 692630  # se queda con la fila con montos
    assert docs[0].reception_date == dt.datetime(2026, 6, 3, 10, 15, 0)


def test_normalize_sales_uses_sale_columns():
    docs = _normalize({"REGISTRO": _parse_csv(_CSV_SALES)}, "VENTA")
    assert docs[0].counterpart_rut == "99540010-3" and docs[0].total_amount == 558090


def test_rcv_document_to_book_line():
    doc = _normalize({"REGISTRO": _parse_csv(_CSV_PURCHASE)}, "COMPRA")[0]
    line = doc.to_book_line()
    assert line.doc_type == 33 and line.folio == 1356612
    assert line.rut == "77073851-2" and line.total_amount == 235000
