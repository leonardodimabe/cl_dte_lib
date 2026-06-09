"""Tests del validador XSD.

Usa un XSD sintético en una carpeta temporal para probar la mecánica del
validador sin depender de los esquemas oficiales del SII (que no se versionan).
"""

import pytest

from dte_chile.validation import ValidationError, Validator, XSDNotAvailable

# XSD mínimo que valida <DTE><Documento>texto</Documento></DTE> en el ns SiiDte.
_XSD_DTE = """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           xmlns="http://www.sii.cl/SiiDte"
           targetNamespace="http://www.sii.cl/SiiDte"
           elementFormDefault="qualified">
  <xs:element name="DTE">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="Documento" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

NS = "http://www.sii.cl/SiiDte"


@pytest.fixture
def schemas_dir(tmp_path):
    # El validador mapea "DTE" → "dte/DTE_v10.xsd" (subcarpeta por familia).
    sub = tmp_path / "dte"
    sub.mkdir()
    (sub / "DTE_v10.xsd").write_text(_XSD_DTE, encoding="utf-8")
    return tmp_path


def test_document_valid(schemas_dir):
    v = Validator(schemas_dir)
    xml = f'<DTE xmlns="{NS}"><Documento>ok</Documento></DTE>'.encode()
    v.validate(xml)  # no lanza
    assert v.is_valid(xml) is True


def test_document_invalid(schemas_dir):
    v = Validator(schemas_dir)
    # Falta <Documento> → inválido según el XSD.
    xml = f'<DTE xmlns="{NS}"><Otro>x</Otro></DTE>'.encode()
    assert v.is_valid(xml) is False
    with pytest.raises(ValidationError) as exc:
        v.validate(xml)
    assert exc.value.errors  # trae el detalle del error


def test_xsd_missing(tmp_path):
    v = Validator(tmp_path)  # carpeta vacía
    xml = f'<DTE xmlns="{NS}"><Documento>ok</Documento></DTE>'.encode()
    with pytest.raises(XSDNotAvailable):
        v.validate(xml)


def test_root_without_mapping(schemas_dir):
    v = Validator(schemas_dir)
    xml = f'<Desconocido xmlns="{NS}"/>'.encode()
    with pytest.raises(ValueError, match="elemento raíz"):
        v.validate(xml)


def test_available(schemas_dir):
    v = Validator(schemas_dir)
    assert v.available("DTE") is True
    assert v.available("LibroCompraVenta") is False
