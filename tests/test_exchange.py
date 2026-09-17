"""Tests de los acuses de intercambio (RespuestaDTE y EnvioRecibos)."""

import datetime as dt
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from dte_chile import exchange as ix
from dte_chile import signer
from dte_chile.certificate import Certificate
from dte_chile.validation import Validator

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _self_signed_cert() -> Certificate:
    """Certificado self-signed solo para probar el flujo de firma en tests."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Cert")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(dt.datetime(2020, 1, 1))
        .not_valid_after(dt.datetime(2035, 1, 1))
        .sign(key, hashes.SHA256())
    )
    return Certificate(
        private_key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        rut="77777777-7",
    )


CERT = _self_signed_cert()

_ENVELOPE_XML = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<EnvioDTE xmlns="http://www.sii.cl/SiiDte" version="1.0">
 <SetDTE ID="SetDoc">
  <Caratula version="1.0">
   <RutEmisor>76158145-7</RutEmisor>
   <RutEnvia>12291733-9</RutEnvia>
   <RutReceptor>77777777-7</RutReceptor>
   <FchResol>2026-06-08</FchResol><NroResol>0</NroResol>
   <TmstFirmaEnv>2026-06-08T10:30:00</TmstFirmaEnv>
   <SubTotDTE><TpoDTE>33</TpoDTE><NroDTE>1</NroDTE></SubTotDTE>
  </Caratula>
  <DTE version="1.0"><Documento ID="F3027T33"><Encabezado>
   <IdDoc><TipoDTE>33</TipoDTE><Folio>3027</Folio><FchEmis>2026-06-08</FchEmis></IdDoc>
   <Emisor><RUTEmisor>76158145-7</RUTEmisor></Emisor>
   <Receptor><RUTRecep>77777777-7</RUTRecep></Receptor>
   <Totales><MntTotal>535500</MntTotal></Totales>
  </Encabezado></Documento></DTE>
 </SetDTE>
 <Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignedInfo>
  <Reference URI="#SetDoc"><DigestValue>QUJDMTIz</DigestValue></Reference>
 </SignedInfo></Signature>
</EnvioDTE>"""


def test_parse_envelope():
    envelope = ix.parse_envelope(_ENVELOPE_XML, "DTE_76158145.xml")
    assert envelope.set_dte_id == "SetDoc"
    assert envelope.issuer_rut == "76158145-7"
    assert envelope.receiver_rut == "77777777-7"
    assert envelope.digest == "QUJDMTIz"
    assert len(envelope.documents) == 1
    d = envelope.documents[0]
    assert d.doc_type == 33 and d.folio == 3027 and d.total_amount == 535500


def _envelope():
    return ix.parse_envelope(_ENVELOPE_XML)


def test_receipt_acknowledgment():
    resp = ix.build_receipt_acknowledgment(_envelope(), CERT, dt.datetime(2026, 6, 8, 11, 0, 0))
    reparse = etree.fromstring(ix.serialize(resp))

    assert signer.verify_signatures(reparse) == [True]
    assert reparse.find(".//{*}EstadoRecepEnv").text == "0"
    assert reparse.find(".//{*}RecepEnvGlosa").text == "Envio Recibido Conforme"
    # No mezcla resultado comercial en el acuse (es un choice en el XSD).
    assert reparse.find(".//{*}ResultadoDTE") is None
    # Carátula: responde el receptor, recibe el emisor original.
    assert reparse.find(".//{*}RutResponde").text == "77777777-7"
    assert reparse.find(".//{*}RutRecibe").text == "76158145-7"


def test_result_acceptance():
    resp = ix.build_result_response(_envelope(), CERT, dt.datetime(2026, 6, 8, 11, 0, 0))
    reparse = etree.fromstring(ix.serialize(resp))
    assert signer.verify_signatures(reparse) == [True]
    assert reparse.find(".//{*}EstadoDTE").text == "0"
    assert reparse.find(".//{*}RecepcionEnvio") is None


def test_result_rejection():
    resp = ix.build_result_response(
        _envelope(),
        CERT,
        dt.datetime(2026, 6, 8, 11, 0, 0),
        accept=False,
        rejection_label="Monto no coincide",
    )
    reparse = etree.fromstring(ix.serialize(resp))
    assert reparse.find(".//{*}EstadoDTE").text == "2"
    # Formato del SII: «RECHAZADO» con el motivo, citando la Ley 19.983.
    assert reparse.find(".//{*}EstadoDTEGlosa").text == (
        "RECHAZADO segun Ley 19.983: Monto no coincide"
    )


def test_receipts_envelope_nested_signatures():
    receipts = ix.build_receipts_envelope(
        _envelope(), CERT, dt.datetime(2026, 6, 8, 11, 0, 0), location="Bodega Central"
    )
    reparse = etree.fromstring(ix.serialize(receipts))

    # 2 firmas: la del SetRecibos + la del único Recibo.
    signatures = signer.verify_signatures(reparse)
    assert signatures == [True, True]
    assert reparse.find(".//{*}Recinto").text == "Bodega Central"
    assert "Ley 19.983" in reparse.find(".//{*}Declaracion").text
    assert reparse.find(".//{*}RutFirma").text == "77777777-7"


# --------------------------------------------------------------------------- #
#  El set de intercambio de la certificación, tal como lo entrega el SII
# --------------------------------------------------------------------------- #
#: Dos facturas de 88888888-8 para CONSTRUCTORA DIMABE SPA (77262159-0). La
#: segunda (folio 52299) va a otro receptor, 69507000-4: está puesta para ver si
#: el postulante la distingue.
DIMABE = "77262159-0"
TS = dt.datetime(2026, 9, 17, 10, 0, 0)

pytestmark_xsd = pytest.mark.skipif(
    not (SCHEMAS / "response" / "RespuestaEnvioDTE_v10.xsd").exists(),
    reason="XSD de respuesta no presentes",
)


def _set_sii():
    xml = (FIXTURES / "set_intercambio_sii.xml").read_bytes()
    return ix.parse_envelope(xml, "set_intercambio_sii.xml")


def test_el_set_del_sii_trae_una_factura_para_otro_receptor():
    envelope = _set_sii()
    assert envelope.receiver_rut == DIMABE
    assert [(d.folio, d.receiver_rut) for d in envelope.documents] == [
        (52298, DIMABE),
        (52299, "69507000-4"),
    ]


@pytestmark_xsd
def test_acuse_de_recibo_por_documento():
    """Formato del SII: 0 «DTE Recibido OK», 3 «DTE No Recibido - Error en RUT Receptor»."""
    xml = ix.serialize(ix.build_receipt_acknowledgment(_set_sii(), CERT, TS, responder_rut=DIMABE))
    Validator(SCHEMAS).validate(xml)
    raiz = etree.fromstring(xml)
    assert signer.verify_signatures(raiz) == [True]
    assert raiz.findtext(".//{*}EstadoRecepEnv") == "0"
    estados = {
        n.findtext("{*}Folio"): (n.findtext("{*}EstadoRecepDTE"), n.findtext("{*}RecepDTEGlosa"))
        for n in raiz.iter("{*}RecepcionDTE")
    }
    assert estados == {
        "52298": ("0", "DTE Recibido OK"),
        "52299": ("3", "DTE No Recibido - Error en RUT Receptor"),
    }


@pytestmark_xsd
def test_resultado_comercial_rechaza_la_factura_ajena():
    xml = ix.serialize(ix.build_result_response(_set_sii(), CERT, TS, responder_rut=DIMABE))
    Validator(SCHEMAS).validate(xml)
    raiz = etree.fromstring(xml)
    assert signer.verify_signatures(raiz) == [True]
    estados = {
        n.findtext("{*}Folio"): (n.findtext("{*}EstadoDTE"), n.findtext("{*}EstadoDTEGlosa"))
        for n in raiz.iter("{*}ResultadoDTE")
    }
    assert estados["52298"] == ("0", "ACEPTADO OK")
    assert estados["52299"][0] == "2"
    assert estados["52299"][1].startswith("RECHAZADO segun Ley 19.983")
    assert "69507000-4" in estados["52299"][1]


def test_recibo_de_mercaderias_solo_de_lo_recibido():
    xml = ix.serialize(
        ix.build_receipts_envelope(_set_sii(), CERT, TS, location="BODEGA", responder_rut=DIMABE)
    )
    if (SCHEMAS / "receipts" / "EnvioRecibos_v10.xsd").exists():
        Validator(SCHEMAS).validate(xml)
    raiz = etree.fromstring(xml)
    assert [n.findtext("{*}Folio") for n in raiz.iter("{*}DocumentoRecibo")] == ["52298"]
    assert signer.verify_signatures(raiz) == [True, True]


def test_sin_documentos_propios_no_hay_recibo():
    with pytest.raises(ValueError, match="ningún documento"):
        ix.build_receipts_envelope(_set_sii(), CERT, TS, location="BODEGA", responder_rut="1-9")
