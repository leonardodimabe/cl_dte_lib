"""Vigencia, ambiente y llaves del CAF."""

import datetime as dt

import pytest
from lxml import etree

from dte_chile.caf import load_caf_bytes


def _xml(caf, **changes) -> bytes:
    """Reserializa un CAF del factory cambiando campos de <DA>."""
    root = etree.Element("AUTORIZACION")
    node = etree.fromstring(etree.tostring(caf.caf_element))
    root.append(node)
    da = node.find("DA")
    for tag, value in changes.items():
        target = da.find(tag)
        if value is None:
            da.remove(target)
        else:
            target.text = value
    etree.SubElement(root, "RSASK").text = caf.rsa_private_key_pem
    return etree.tostring(root)


def test_reads_authorization_date_and_key_id(caf_factory):
    caf = caf_factory(33)
    assert caf.authorized_on == dt.date(2026, 1, 1)
    assert caf.key_id == 100
    assert caf.is_certification


@pytest.mark.parametrize(
    ("authorized", "last_day"),
    [
        ("2026-03-15", dt.date(2026, 9, 14)),
        ("2026-08-31", dt.date(2027, 2, 27)),
        ("2027-08-31", dt.date(2028, 2, 28)),  # bisiesto
    ],
)
def test_credit_documents_expire_after_six_months(caf_factory, authorized, last_day):
    caf = load_caf_bytes(_xml(caf_factory(33), FA=authorized))
    assert caf.expires_on == last_day
    assert not caf.is_expired(last_day)
    assert caf.is_expired(last_day + dt.timedelta(days=1))


@pytest.mark.parametrize("doc_type", [34, 39, 41, 52, 110, 111, 112])
def test_documents_without_fiscal_credit_do_not_expire(caf_factory, doc_type):
    caf = caf_factory(doc_type)
    assert caf.expires_on is None
    assert not caf.is_expired(dt.date(2099, 1, 1))


def test_matching_keys(caf_factory):
    assert caf_factory(33).keys_match()


def test_private_key_from_another_caf_does_not_match(caf_factory):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    caf = caf_factory(33)
    other = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    caf.rsa_private_key_pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    assert not caf.keys_match()


def test_garbage_private_key_does_not_match(caf_factory):
    caf = caf_factory(33)
    caf.rsa_private_key_pem = (
        "-----BEGIN RSA PRIVATE KEY-----\nZHVtbXk=\n-----END RSA PRIVATE KEY-----"
    )
    assert not caf.keys_match()


@pytest.mark.parametrize(
    "data",
    [
        b"no es xml",
        b"<AUTORIZACION/>",
        b"<AUTORIZACION><CAF/></AUTORIZACION>",
        b"<AUTORIZACION><CAF><DA><TD>33</TD></DA></CAF><RSASK>x</RSASK></AUTORIZACION>",
    ],
)
def test_malformed_caf_is_a_value_error(data):
    with pytest.raises(ValueError, match="CAF inválido"):
        load_caf_bytes(data)


def test_inverted_range_is_rejected(caf_factory):
    with pytest.raises(ValueError, match="rango"):
        load_caf_bytes(_xml(caf_factory(33, 10, 20), **{"RNG/D": "30"}))
