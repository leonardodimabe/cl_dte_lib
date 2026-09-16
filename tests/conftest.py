"""Fixtures compartidos para los tests."""

import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from dte_chile.certificate import Certificate


@pytest.fixture(scope="session")
def cert() -> Certificate:
    """Certificado self-signed (solo para probar el flujo de firma)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Cert")])
    certificate = (
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
        cert_pem=certificate.public_bytes(serialization.Encoding.PEM),
        rut="77777777-7",
    )


@pytest.fixture(scope="session")
def caf_factory():
    """Genera CAF válidos (con llave RSA real) para firmar el TED en los tests.

    El bloque <CAF> no lleva firma real del SII —no la tenemos— pero sí una
    RSASK utilizable, que es lo que el TED necesita para timbrar.
    """
    from lxml import etree

    from dte_chile.caf import CAF, load_caf_bytes

    # Los CAF del SII usan 512 bits; 1024 es lo mínimo que genera cryptography.
    # Con 2048 la RSAPK y el FRMT ya no caben en el PDF417 del impreso.
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )

    def _make(
        doc_type: int, folio_from: int = 1, folio_to: int = 100, rut: str = "76158145-7"
    ) -> CAF:
        root = etree.Element("AUTORIZACION")
        caf = etree.SubElement(root, "CAF", version="1.0")
        da = etree.SubElement(caf, "DA")
        for tag, value in (("RE", rut), ("RS", "DEMO SPA"), ("TD", str(doc_type))):
            etree.SubElement(da, tag).text = value
        rng = etree.SubElement(da, "RNG")
        etree.SubElement(rng, "D").text = str(folio_from)
        etree.SubElement(rng, "H").text = str(folio_to)
        etree.SubElement(da, "FA").text = "2026-01-01"
        # La llave pública de verdad: el SII verifica el timbre con la RSAPK que
        # va dentro del DD, y los tests tienen que poder hacer lo mismo.
        numbers = key.public_key().public_numbers()
        pk = etree.SubElement(da, "RSAPK")
        etree.SubElement(pk, "M").text = _b64_int(numbers.n)
        etree.SubElement(pk, "E").text = _b64_int(numbers.e)
        etree.SubElement(da, "IDK").text = "100"
        etree.SubElement(caf, "FRMA", algoritmo="SHA1withRSA").text = "eA=="
        etree.SubElement(root, "RSASK").text = private_pem
        etree.SubElement(root, "RSAPUBK").text = public_pem
        return load_caf_bytes(etree.tostring(root))

    return _make


def _b64_int(value: int) -> str:
    import base64

    return base64.b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).decode()
