"""Tests de los acuses de intercambio (RespuestaDTE y EnvioRecibos)."""

import datetime as dt

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from dte_chile import exchange as ix
from dte_chile import signer
from dte_chile.certificate import Certificate


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
    assert reparse.find(".//{*}EstadoDTEGlosa").text == "Monto no coincide"


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
