"""Tests del cliente BHE (parser puro + paginación con sesión falsa, sin red)."""

import datetime as dt

import pytest

from dte_chile.bhe import (
    BheClient,
    BheDocument,
    BteDocument,
    _extract_bte_total,
    _extract_bte_total_pages,
    _extract_total,
    _normalize_bte_row,
    _normalize_row,
    _parse_bte_report,
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
    "rutemisor": "12345678",
    "dvemisor": "5",
    "nombre_emisor": "JUAN PEREZ CONSULTOR",
    "nroboleta": "1001",
    "fecha_boleta": "05/05/2026",
    "fechaanulacion": "",
    "totalhonorarios": "1.000.000",
    "retencion_receptor": "152.500",
    "honorariosliquidos": "847.500",
    "estado": "V",
}
_ROW_ANULADA = {
    "rutemisor": "7654321",
    "dvemisor": "K",
    "nombre_emisor": "MARIA GONZALEZ",
    "nroboleta": "2002",
    "fecha_boleta": "15/05/2026",
    "fechaanulacion": "20/05/2026",
    "totalhonorarios": "500.000",
    "retencion_receptor": "76.250",
    "honorariosliquidos": "423.750",
    "estado": "A",
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
    encoding = None

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


# --------------------------------------------------------------------------- #
#  BTE emitidas (sistema zeus.sii.cl/cvc_cgi/bte)
# --------------------------------------------------------------------------- #
def _bte_row(folio, status, date, provider_rut, provider_name, gross, retained, paid) -> str:
    """Una fila como la del informe del SII: ícono con enlace + 11 columnas."""
    cells = [
        folio,
        f'<a href="javascript:void(0);">{status}</a>',
        date,
        "76086428-5",
        "EMPRESA RECEPTORA SPA",
        date,
        provider_rut,
        provider_name,
        gross,
        retained,
        paid,
    ]
    tds = "".join(f'<td><font class="reporte">{c}\n</font></td>' for c in cells)
    link = f'<td><a href="/cvc_cgi/bte/bte_indiv_cons3?C{folio}"><img src="/x.gif"></a></td>'
    return f"<tr>{link}{tds}</tr>"


def _bte_html(rows: list[str], total: int, page: int = 1, pages: int = 1) -> str:
    """Informe de BTE emitidas: marcador, totales, paginación, filas y fila de totales."""
    return (
        "<html><body><font><b>INFORME BTE's EMITIDAS</b></font>"
        f"<table><tr><td>Total Boletas : {total}</td>"
        f"<td>P&aacute;gina {page} de {pages}</td></tr></table>"
        '<table border=1><tr><td colspan="4"><b>Boleta</b></td><td><b>Emisi&oacute;n</b></td></tr>'
        + "".join(rows)
        + "<tr><td>Totales</td><td>122.714</td></tr></table></body></html>"
    )


_BTE_VIGENTE = _bte_row(
    "130", "VIG", "06-05-2026", "12345678-5", "JUAN PRESTADOR", "94.395", "14.395", "80.000"
)
_BTE_ANULADA = _bte_row(
    "131", "ANUL", "07-05-2026", "7654321-k", "MARIA PRESTADORA", "28.319", "4.319", "24.000"
)


class _FakeBteSession:
    """Una página por llamada según ``PAGINA`` (el sistema de BTE numera desde 1)."""

    def __init__(self, pages: list[str]):
        self.pages = pages
        self.requests: list[dict] = []
        self.gets: list[str] = []
        self.headers: dict = {}

    def get(self, url, timeout=None):
        self.gets.append(url)
        return _FakeResponse("<html>menu</html>")

    def post(self, url, data=None, headers=None, timeout=None):
        self.requests.append(data)
        return _FakeResponse(self.pages[int(data["PAGINA"]) - 1])

    def close(self) -> None:
        pass


def _bte_client(pages: list[str]) -> BheClient:
    client = BheClient("76086428-5", "clave-no-usada")
    client.session = _FakeBteSession(pages)
    client._authenticated = True  # evita el login real
    return client


def test_parse_bte_report_skips_header_and_totals():
    rows = _parse_bte_report(_bte_html([_BTE_VIGENTE, _BTE_ANULADA], total=2))
    assert [r["folio"] for r in rows] == ["130", "131"]


def test_normalize_bte_row_vigente_and_anulada():
    vig, anul = (
        _normalize_bte_row(r, "2026-05")
        for r in _parse_bte_report(_bte_html([_BTE_VIGENTE, _BTE_ANULADA], 2))
    )
    assert isinstance(vig, BteDocument)
    assert vig.provider_rut == "12345678-5"
    assert vig.provider_name == "JUAN PRESTADOR"
    assert vig.folio == 130
    assert vig.issue_date == dt.date(2026, 5, 6)
    assert (vig.gross_amount, vig.retention_amount, vig.net_amount) == (94395, 14395, 80000)
    assert vig.status == "vigente"
    assert anul.status == "anulada"
    assert anul.provider_rut == "7654321-K"


def test_extract_bte_pages_and_total():
    html = _bte_html([_BTE_VIGENTE], total=7, page=2, pages=3)
    assert _extract_bte_total_pages(html) == 3
    assert _extract_bte_total(html) == 7
    assert _extract_bte_total_pages("<html>sin paginación</html>") == 1


def test_fetch_issued_bte_single_page_and_params():
    with _bte_client([_bte_html([_BTE_VIGENTE, _BTE_ANULADA], total=2)]) as client:
        docs = client.fetch_issued_bte(2026, 3)
    assert [d.folio for d in docs] == [130, 131]
    assert all(d.period == "2026-03" for d in docs)
    req = client.session.requests[0]
    assert req == {
        "CNTR": "1",
        "PAGINA": "1",
        "AUTEN": "RUTCLAVE",
        "TIPO": "mensual",
        "MESM": "03",
        "ANOM": "2026",
    }
    assert client.session.gets == ["https://zeus.sii.cl/cvc_cgi/bte/bte_indiv_cons?1"]


def test_fetch_issued_bte_paginates():
    third = _bte_row("132", "VIG", "20-05-2026", "11111111-1", "PEDRO", "10.000", "1.525", "8.475")
    page1 = _bte_html([_BTE_VIGENTE, _BTE_ANULADA], total=3, page=1, pages=2)
    page2 = _bte_html([third], total=3, page=2, pages=2)
    with _bte_client([page1, page2]) as client:
        docs = client.fetch_issued_bte(2026, 5)
    assert [d.folio for d in docs] == [130, 131, 132]
    assert [r["PAGINA"] for r in client.session.requests] == ["1", "2"]


def test_fetch_issued_bte_total_mismatch_raises():
    # El SII dice 3 pero la única página trae 2: error explícito, no silencio.
    with (
        _bte_client([_bte_html([_BTE_VIGENTE, _BTE_ANULADA], total=3)]) as client,
        pytest.raises(BheError, match="3 BTE"),
    ):
        client.fetch_issued_bte(2026, 5)


def test_fetch_issued_bte_empty_month():
    with _bte_client([_bte_html([], total=0)]) as client:
        assert client.fetch_issued_bte(2026, 1) == []


def test_fetch_issued_bte_expired_session_raises_auth_error():
    with (
        _bte_client(["<html>Redireccionando a IngresoRutClave.html ...</html>"]) as client,
        pytest.raises(SiiAuthError),
    ):
        client.fetch_issued_bte(2026, 5)


def test_fetch_issued_bte_unrecognized_html_raises_bhe_error():
    with (
        _bte_client(["<html><body>Pagina desconocida</body></html>"]) as client,
        pytest.raises(BheError, match="Extracto"),
    ):
        client.fetch_issued_bte(2026, 5)
