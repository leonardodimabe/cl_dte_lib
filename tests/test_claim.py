"""Aceptar o reclamar un DTE recibido ante el SII."""

import pytest
from lxml import etree

from dte_chile.certificate import Certificate
from dte_chile.claim import (
    NS_CLAIM,
    ClaimAction,
    ClaimClient,
    _build_envelope,
    _parse_result,
    _split_rut,
)
from dte_chile.errors import SiiError
from dte_chile.sii_client import Environment

RESPUESTA_OK = b"""<?xml version="1.0"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
 <soapenv:Body>
  <ns:ingresarAceptacionReclamoDocResponse
      xmlns:ns="http://ws.registroreclamodte.diii.sdi.sii.cl">
   <return><codResp>0</codResp><descResp>EVENTO REGISTRADO</descResp></return>
  </ns:ingresarAceptacionReclamoDocResponse>
 </soapenv:Body>
</soapenv:Envelope>"""


def _cliente():
    cliente = ClaimClient(
        Certificate(private_key_pem=b"", cert_pem=b"", rut="12291733-9"),
        Environment.CERTIFICATION,
    )
    cliente._token = "TOKEN123"
    return cliente


def test_el_sobre_lleva_los_parametros_del_wsdl():
    """Los nombres salen del WSDL del SII, no de la imaginación."""
    xml = _build_envelope(
        "ingresarAceptacionReclamoDoc",
        {
            "rutEmisor": "88888888",
            "dvEmisor": "8",
            "tipoDoc": "33",
            "folio": "52298",
            "accionDoc": "RCD",
        },
    )
    raiz = etree.fromstring(xml)
    operacion = raiz.find(".//{%s}ingresarAceptacionReclamoDoc" % NS_CLAIM)
    assert operacion is not None
    assert [hijo.tag for hijo in operacion] == [
        "rutEmisor",
        "dvEmisor",
        "tipoDoc",
        "folio",
        "accionDoc",
    ]
    assert operacion.findtext("accionDoc") == "RCD"


def test_reclamar_manda_el_rut_del_emisor_partido(monkeypatch):
    """El documento se identifica por su EMISOR, tipo y folio.

    Mandar el RUT propio registraría el evento sobre un documento que no
    existe, y el reclamo se daría por hecho sin estarlo.
    """
    cliente = _cliente()
    visto = {}

    def _falso(operation, params):
        visto["operation"], visto["params"] = operation, params
        return _parse_result(RESPUESTA_OK, operation)

    monkeypatch.setattr(cliente, "_call", _falso)
    resultado = cliente.register("88888888-8", 33, 52298, ClaimAction.CLAIM_CONTENT)

    assert visto["operation"] == "ingresarAceptacionReclamoDoc"
    assert visto["params"]["rutEmisor"] == "88888888"
    assert visto["params"]["dvEmisor"] == "8"
    assert visto["params"]["accionDoc"] == "RCD"
    assert resultado.ok and resultado.detail == "EVENTO REGISTRADO"


def test_una_boleta_no_se_reclama():
    """Sólo los documentos con crédito fiscal admiten aceptación o reclamo."""
    with pytest.raises(ValueError, match="no admite"):
        _cliente().register("88888888-8", 39, 1, ClaimAction.CLAIM_CONTENT)


def test_un_fault_del_sii_no_pasa_por_exito():
    """Un Fault trae HTTP 200: leerlo como respuesta daría un reclamo fantasma."""
    fault = b"""<?xml version="1.0"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
     <soapenv:Body><soapenv:Fault>
      <faultcode>env:Server</faultcode><faultstring>Token invalido</faultstring>
     </soapenv:Fault></soapenv:Body></soapenv:Envelope>"""
    with pytest.raises(SiiError, match="Token invalido"):
        _parse_result(fault, "ingresarAceptacionReclamoDoc")


def test_el_historial_trae_los_eventos_del_documento():
    respuesta = b"""<?xml version="1.0"?>
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
     <soapenv:Body>
      <ns:listarEventosHistDocResponse xmlns:ns="http://ws.registroreclamodte.diii.sdi.sii.cl">
       <return><codResp>0</codResp><descResp>OK</descResp>
        <listaEventosDoc><codEvento>ERM</codEvento>
         <descEvento>Otorga Recibo de Mercaderias</descEvento>
         <fechaEvento>2026-09-20</fechaEvento><rutResponde>77262159-0</rutResponde>
        </listaEventosDoc>
       </return>
      </ns:listarEventosHistDocResponse>
     </soapenv:Body></soapenv:Envelope>"""
    resultado = _parse_result(respuesta, "listarEventosHistDoc")
    assert resultado.ok
    assert [(e.code, e.responder_rut) for e in resultado.events] == [("ERM", "77262159-0")]


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("77262159-0", ("77262159", "0")),
        ("77.262.159-0", ("77262159", "0")),
        ("122917339", ("12291733", "9")),
        ("6824772-K", ("6824772", "K")),
    ],
)
def test_el_rut_se_parte_como_lo_pide_el_sii(entrada, esperado):
    assert _split_rut(entrada) == esperado
