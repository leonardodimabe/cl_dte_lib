"""Cliente REST de boleta electrónica, con el SII simulado.

Las respuestas son las que devuelve el SII de verdad: se capturaron del ambiente
de certificación (apicert.sii.cl) al verificar el flujo de autenticación.
"""

import pytest
import requests

from dte_chile.errors import SiiAuthError, SiiError, SiiUploadError
from dte_chile.receipt_client import ReceiptClient, ReceiptEnvironment

SEED_OK = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<SII:RESPUESTA xmlns:SII="http://www.sii.cl/XMLSchema">'
    b"<SII:RESP_BODY><SEMILLA>167260909992</SEMILLA></SII:RESP_BODY>"
    b"<SII:RESP_HDR><ESTADO>00</ESTADO></SII:RESP_HDR></SII:RESPUESTA>"
)
TOKEN_OK = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<SII:RESPUESTA xmlns:SII="http://www.sii.cl/XMLSchema">'
    b"<SII:RESP_BODY><TOKEN>XYUFZXZX761DX</TOKEN></SII:RESP_BODY>"
    b"<SII:RESP_HDR><ESTADO>00</ESTADO><GLOSA>Token Creado</GLOSA>"
    b"</SII:RESP_HDR></SII:RESPUESTA>"
)
ACCESS_DENIED = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/"><env:Body>'
    b"<env:Fault><faultcode>env:Client</faultcode>"
    b"<faultstring>Acceso Denegado (from client)</faultstring>"
    b"</env:Fault></env:Body></env:Envelope>"
)
UPLOAD_OK = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<RECEPCIONDTE><TRACKID>123456789012345</TRACKID>"
    b"<ESTADO>0</ESTADO><GLOSA>Envio Recibido</GLOSA></RECEPCIONDTE>"
)


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _FakeSession:
    """Registra las llamadas y devuelve respuestas preparadas."""

    def __init__(self, get=None, post=None):
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self._get = get
        self._post = post if isinstance(post, list) else [post]
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._get

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._post.pop(0) if len(self._post) > 1 else self._post[0]


@pytest.fixture
def client(cert, monkeypatch):
    def _make(get=None, post=None):
        session = _FakeSession(get=get, post=post)
        monkeypatch.setattr("dte_chile.receipt_client.build_session", lambda: session)
        instance = ReceiptClient(cert, ReceiptEnvironment.CERTIFICATION)
        instance.session = session
        return instance

    return _make


# --------------------------------------------------------------------------- #
#  Hosts
# --------------------------------------------------------------------------- #
def test_environments_do_not_use_maullin_or_palena():
    """El SII dedica otros servidores a la boleta."""
    assert ReceiptEnvironment.CERTIFICATION.base_url == "https://apicert.sii.cl/recursos/v1"
    assert ReceiptEnvironment.PRODUCTION.base_url == "https://api.sii.cl/recursos/v1"


# --------------------------------------------------------------------------- #
#  Autenticación
# --------------------------------------------------------------------------- #
def test_seed_is_read_from_the_response(client):
    instance = client(get=_FakeResponse(SEED_OK))
    assert instance.get_seed() == "167260909992"
    method, url, _ = instance.session.calls[0]
    assert method == "GET"
    assert url.endswith("/boleta.electronica.semilla")


def test_token_is_obtained_from_the_signed_seed(client):
    instance = client(get=_FakeResponse(SEED_OK), post=_FakeResponse(TOKEN_OK))
    assert instance.authenticate() == "XYUFZXZX761DX"

    _, url, kw = instance.session.calls[1]
    assert url.endswith("/boleta.electronica.token")
    assert kw["headers"]["Content-Type"] == "application/xml"
    # Lo que se manda es la semilla firmada, no la semilla desnuda.
    assert b"<Signature" in kw["data"]
    assert b"167260909992" in kw["data"]


def test_missing_seed_is_an_auth_error(client):
    instance = client(get=_FakeResponse(b"<RESPUESTA><ESTADO>99</ESTADO></RESPUESTA>"))
    with pytest.raises(SiiAuthError, match="no devolvió SEMILLA"):
        instance.get_seed()


def test_rejected_seed_reports_the_sii_message(client):
    rejected = (
        b"<RESPUESTA><RESP_HDR><ESTADO>02</ESTADO>"
        b"<GLOSA>Firma Invalida</GLOSA></RESP_HDR></RESPUESTA>"
    )
    instance = client(get=_FakeResponse(SEED_OK), post=_FakeResponse(rejected))
    with pytest.raises(SiiAuthError, match="Firma Invalida"):
        instance.authenticate()


def test_network_failure_surfaces_as_sii_error(client, monkeypatch):
    instance = client(get=_FakeResponse(SEED_OK))

    def _boom(*a, **kw):
        raise requests.ConnectionError("sin red")

    monkeypatch.setattr(instance.session, "get", _boom)
    with pytest.raises(SiiError, match="Error de red"):
        instance.get_seed()


# --------------------------------------------------------------------------- #
#  Envío
# --------------------------------------------------------------------------- #
def test_upload_returns_a_fifteen_digit_track_id(client):
    instance = client(
        get=_FakeResponse(SEED_OK), post=[_FakeResponse(TOKEN_OK), _FakeResponse(UPLOAD_OK)]
    )
    result = instance.send_receipts(b"<EnvioBOLETA/>", "77262159-0", "12291733-9")

    assert result.track_id == "123456789012345"
    assert len(result.track_id) == 15  # el de factura tiene 10
    assert result.detail == "Envio Recibido"


def test_upload_splits_the_ruts_into_body_and_check_digit(client):
    instance = client(
        get=_FakeResponse(SEED_OK), post=[_FakeResponse(TOKEN_OK), _FakeResponse(UPLOAD_OK)]
    )
    instance.send_receipts(b"<EnvioBOLETA/>", "77262159-0", "12291733-9")

    _, url, kw = instance.session.calls[2]
    assert url.endswith("/boleta.electronica.envio")
    assert kw["data"] == {
        "rutCompany": "77262159",
        "dvCompany": "0",
        "rutSender": "12291733",
        "dvSender": "9",
    }
    assert kw["files"]["archivo"][1] == b"<EnvioBOLETA/>"


def test_token_travels_in_the_cookie(client):
    instance = client(
        get=_FakeResponse(SEED_OK), post=[_FakeResponse(TOKEN_OK), _FakeResponse(UPLOAD_OK)]
    )
    instance.send_receipts(b"<EnvioBOLETA/>", "77262159-0", "12291733-9")
    assert instance.session.cookies.get("TOKEN") == "XYUFZXZX761DX"


def test_access_denied_is_reported_with_the_sii_fault(client):
    """Es la respuesta real cuando el titular del certificado no está autorizado."""
    instance = client(
        get=_FakeResponse(SEED_OK),
        post=[_FakeResponse(TOKEN_OK), _FakeResponse(ACCESS_DENIED, status_code=500)],
    )
    with pytest.raises(SiiUploadError, match="Acceso Denegado"):
        instance.send_receipts(b"<EnvioBOLETA/>", "77262159-0", "12291733-9")


def test_authentication_happens_once(client):
    instance = client(
        get=_FakeResponse(SEED_OK),
        post=[_FakeResponse(TOKEN_OK), _FakeResponse(UPLOAD_OK), _FakeResponse(UPLOAD_OK)],
    )
    instance.send_receipts(b"<EnvioBOLETA/>", "77262159-0", "12291733-9")
    instance.send_receipts(b"<EnvioBOLETA/>", "77262159-0", "12291733-9")
    assert sum(1 for method, _, _ in instance.session.calls if method == "GET") == 1


def test_user_agent_follows_the_sii_format():
    """El SII filtra por User-Agent: con otro formato responde error genérico."""
    ua = ReceiptClient.user_agent("12291733-9")
    assert ua == "Mozilla/4.0 (compatible; PROG 1.0; Windows NT 5.0; 12291733-9)"


def test_dte_client_uses_the_same_user_agent_format():
    from dte_chile.sii_client import SIIClient

    assert SIIClient.user_agent("12291733-9") == (
        "Mozilla/4.0 (compatible; PROG 1.0; Windows NT 5.0; 12291733-9)"
    )
