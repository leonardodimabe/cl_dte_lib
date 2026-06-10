"""Tests del cliente BHE (parser puro + paginación con sesión falsa, sin red)."""

import datetime as dt

import pytest

from dte_chile.bhe import (
    BheClient,
    BheDocument,
    _extract_total,
    _normalize_row,
    _parse_date,
    _parse_report,
    _to_int,
)
from dte_chile.errors import BheError, SiiAuthError


def _report_html(rows: list[dict], total: int) -> str:
    """Arma un HTML como el del SII: array JS por campo_índice + total."""
    lines = ["<html><body><!-- INFORME MENSUAL DE BOLETAS RECIBIDAS -->", "<script>"]
    for idx, row in enumerate(rows):
        for fld, value in row.items():
            if fld in ("totalhonorarios", "retencion_receptor", "honorariosliquidos"):
                lines.append(f"arr_informe_mensual['{fld}_{idx}'] = formatMiles(\"{value}\");")
            else:
                lines.append(f"arr_informe_mensual['{fld}_{idx}'] = \"{value}\";")
    lines.append(f"xml_values['total_boletas'] = \"{total}\";")
    lines.append("</script></body></html>")
    return "\n".join(lines)


_ROW_VIGENTE = {
    "rutemisor": "12345678", "dvemisor": "5", "nombre_emisor": "JUAN PEREZ CONSULTOR",
    "nroboleta": "1001", "fecha_boleta": "05/05/2026", "fechaanulacion": "",
    "totalhonorarios": "1.000.000", "retencion_receptor": "152.500",
    "honorariosliquidos": "847.500", "estado": "V",
}
_ROW_ANULADA = {
    "rutemisor": "7654321", "dvemisor": "K", "nombre_emisor": "MARIA GONZALEZ",
    "nroboleta": "2002", "fecha_boleta": "15/05/2026", "fechaanulacion": "20/05/2026",
    "totalhonorarios": "500.000", "retencion_receptor": "76.250",
    "honorariosliquidos": "423.750", "estado": "A",
}


# --- parser puro ---
def test_parse_report_extracts_rows_in_order():
    html = _report_html([_ROW_VIGENTE, _ROW_ANULADA], total=2)
    rows = _parse_report(html)
    assert len(rows) == 2
    assert rows[0]["nroboleta"] == "1001"
    assert rows[0]["totalhonorarios"] == "1.000.000"  # capturado dentro de formatMiles(...)
    assert rows[1]["fechaanulacion"] == "20/05/2026"


def test_parse_report_no_rows():
    assert _parse_report("<html>INFORME MENSUAL sin boletas</html>") == []


def test_extract_total():
    assert _extract_total(_report_html([_ROW_VIGENTE], total=137)) == 137
    assert _extract_total("<html>sin total</html>") is None


def test_normalize_row_vigente():
    doc = _normalize_row(_ROW_VIGENTE, period="2026-05")
    assert isinstance(doc, BheDocument)
    assert doc.issuer_rut == "12345678-5"
    assert doc.issuer_name == "JUAN PEREZ CONSULTOR"
    assert doc.folio == 1001
    assert doc.issue_date == dt.date(2026, 5, 5)
    assert doc.gross_amount == 1000000
    assert doc.retention_amount == 152500
    assert doc.net_amount == 847500
    assert doc.status == "vigente"
    assert doc.period == "2026-05"
    assert doc.cancel_date is None
    assert doc.raw["nroboleta"] == "1001"


def test_normalize_row_anulada():
    doc = _normalize_row(_ROW_ANULADA, period="2026-05")
    assert doc.issuer_rut == "7654321-K"
    assert doc.status == "anulada"
    assert doc.cancel_date == dt.date(2026, 5, 20)


def test_parse_date():
    assert _parse_date("05/05/2026") == dt.date(2026, 5, 5)
    assert _parse_date("") is None
    assert _parse_date(None) is None
    assert _parse_date("31/02/2026") is None  # fecha imposible
    assert _parse_date("2026-05-05") is None  # formato inesperado


def test_to_int_chilean_format():
    assert _to_int("1.000.000") == 1000000
    assert _to_int("152.500") == 152500
    assert _to_int("0") == 0
    assert _to_int("") == 0
    assert _to_int(None) == 0
    assert _to_int(847500) == 847500
    assert _to_int("texto") == 0


# --- fetch_received con sesión falsa (paginación / mes vacío / sesión expirada) ---
class _FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass


class _FakeSession:
    """Devuelve una página HTML por llamada según ``pagina_solicitada``."""

    def __init__(self, pages: list[str]):
        self.pages = pages
        self.requests: list[dict] = []
        self.headers: dict = {}

    def post(self, url, data=None, headers=None, timeout=None):
        self.requests.append(data)
        return _FakeResponse(self.pages[int(data["pagina_solicitada"])])

    def close(self) -> None:
        pass


def _client_with_pages(pages: list[str]) -> BheClient:
    client = BheClient("76086428-5", "clave-no-usada")
    client.session = _FakeSession(pages)
    client._authenticated = True  # evita el login real
    return client


def test_fetch_received_single_page():
    html = _report_html([_ROW_VIGENTE, _ROW_ANULADA], total=2)
    with _client_with_pages([html]) as client:
        docs = client.fetch_received(2026, 5)
    assert [d.folio for d in docs] == [1001, 2002]
    assert all(d.period == "2026-05" for d in docs)
    # Con el total cubierto en la página 0, no se piden más páginas.
    assert len(client.session.requests) == 1


def test_fetch_received_paginates_until_total():
    # 3 boletas con total=3 repartidas en 2 páginas (simula el corte de a 100).
    row3 = dict(_ROW_VIGENTE, nroboleta="3003", fecha_boleta="28/05/2026")
    page0 = _report_html([_ROW_VIGENTE, _ROW_ANULADA], total=3)
    page1 = _report_html([row3], total=3)
    with _client_with_pages([page0, page1]) as client:
        docs = client.fetch_received(2026, 5)
    assert [d.folio for d in docs] == [1001, 2002, 3003]
    assert [r["pagina_solicitada"] for r in client.session.requests] == ["0", "1"]


def test_fetch_received_incomplete_pagination_raises():
    # El SII reporta 5 pero la página 1 ya no trae filas: error explícito, no silencio.
    page0 = _report_html([_ROW_VIGENTE], total=5)
    page1 = "<html>INFORME MENSUAL</html>"  # sin filas
    with _client_with_pages([page0, page1]) as client, pytest.raises(BheError, match="5 boletas"):
        client.fetch_received(2026, 5)


def test_fetch_received_empty_month_returns_empty_list():
    html = "<html><body>INFORME MENSUAL DE BOLETAS RECIBIDAS - sin movimientos</body></html>"
    with _client_with_pages([html]) as client:
        assert client.fetch_received(2026, 1) == []


def test_fetch_received_expired_session_raises_auth_error():
    html = "<html>Redireccionando a IngresoRutClave.html ...</html>"
    with _client_with_pages([html]) as client, pytest.raises(SiiAuthError):
        client.fetch_received(2026, 5)


def test_fetch_received_unrecognized_html_raises_bhe_error():
    html = "<html><body>Pagina desconocida del SII</body></html>"
    with _client_with_pages([html]) as client, pytest.raises(BheError, match="Extracto"):
        client.fetch_received(2026, 5)


def test_fetch_received_sends_period_params():
    html = _report_html([_ROW_VIGENTE], total=1)
    with _client_with_pages([html]) as client:
        client.fetch_received(2026, 3)
    req = client.session.requests[0]
    assert req["cbmesinformemensual"] == "03"
    assert req["cbanoinformemensual"] == "2026"
    assert req["rut_arrastre"] == "76086428"
    assert req["dv_arrastre"] == "5"
