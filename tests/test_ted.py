# ruff: noqa: E501 — CAF_ARCHIVO reproduce un archivo del SII línea a línea;
# partir su FRMA en dos cambiaría justo lo que el test quiere conservar.
"""El timbre (TED) contra el ejemplo de referencia del instructivo del SII.

El ejemplo del SII —RUT 97975000-5, factura 27, receptor JORGE GONZALEZ LTDA—
publica el DD exacto que se firma y su FRMT. Ese FRMT valida sólo sobre el DD
PLANO, con el CAF incrustado sin saltos de línea. Aquí se exige que el motor
produzca exactamente esos bytes, aunque el CAF le llegue con saltos de línea
como vienen los archivos del SII.

No es un detalle de formato: con el CAF incrustado tal cual, una certificación
entera volvió con sus 32 documentos rechazados, mientras los libros —que no
llevan timbre— salían aceptados.
"""

import base64
import datetime as dt

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from dte_chile.caf import CAF
from dte_chile.document_types import DTEType
from dte_chile.models import DTE, Issuer, Item, Receiver
from dte_chile.ted import build_ted, dd_bytes

# Literal del instructivo del SII, tal cual se firma.
DD_SII = (
    "<DD><RE>97975000-5</RE><TD>33</TD><F>27</F><FE>2003-09-08</FE>"
    "<RR>8414240-9</RR><RSR>JORGE GONZALEZ LTDA</RSR><MNT>502946</MNT>"
    '<IT1>Cajon AFECTO</IT1><CAF version="1.0"><DA><RE>97975000-5</RE>'
    "<RS>RUT DE PRUEBA</RS><TD>33</TD><RNG><D>1</D><H>200</H></RNG>"
    "<FA>2003-09-04</FA><RSAPK><M>0a4O6Kbx8Qj3K4iWSP4w7KneZYeJ+g/prihYtIEolKt3"
    "cykSxl1zO8vSXu397QhTmsX7SBEudTUx++2zDXBhZw==</M><E>Aw==</E></RSAPK>"
    '<IDK>100</IDK></DA><FRMA algoritmo="SHA1withRSA">g1AQX0sy8NJugX52k2hTJE'
    "ZAE9Cuul6pqYBdFxj1N17umW7zG/hAavCALKByHzdYAfZ3LhGTXCai5zNxOo4lDQ==</FRMA>"
    "</CAF><TSTED>2003-09-08T12:28:31</TSTED></DD>"
)
FRMT_SII = (
    "pqjXHHQLJmyFPMRvxScN7tYHvIsty0pqL2LLYaG43jMmnfiZfllLA0wb32lP+HBJ/tf8nziSeorvjlx410ZImw=="
)

# El mismo CAF como llega en un archivo del SII: con saltos de línea.
CAF_ARCHIVO = b"""<CAF version="1.0">
<DA>
<RE>97975000-5</RE>
<RS>RUT DE PRUEBA</RS>
<TD>33</TD>
<RNG><D>1</D><H>200</H></RNG>
<FA>2003-09-04</FA>
<RSAPK><M>0a4O6Kbx8Qj3K4iWSP4w7KneZYeJ+g/prihYtIEolKt3cykSxl1zO8vSXu397QhTmsX7SBEudTUx++2zDXBhZw==</M><E>Aw==</E></RSAPK>
<IDK>100</IDK>
</DA>
<FRMA algoritmo="SHA1withRSA">g1AQX0sy8NJugX52k2hTJEZAE9Cuul6pqYBdFxj1N17umW7zG/hAavCALKByHzdYAfZ3LhGTXCai5zNxOo4lDQ==</FRMA>
</CAF>"""


def _clave():
    llave = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pem = llave.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return llave, pem


def _dte(receptor="JORGE GONZALEZ LTDA", item="Cajon AFECTO"):
    return DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=27,
        issue_date=dt.date(2003, 9, 8),
        issuer=Issuer(
            rut="97975000-5",
            business_name="RUT DE PRUEBA",
            activity="Pruebas",
            economic_activity=479100,
            address="Calle 1",
            commune="Santiago",
            city="Santiago",
        ),
        receiver=Receiver(
            rut="8414240-9",
            business_name=receptor,
            activity="Comercio",
            address="Calle 2",
            commune="Santiago",
        ),
        # Neto 422.644 + IVA 80.302 = 502.946, el MNT del ejemplo.
        items=[Item(item, quantity=1, unit_price=422644)],
    )


def _caf(pem):
    return CAF(
        doc_type=33,
        folio_from=1,
        folio_to=200,
        issuer_rut="97975000-5",
        caf_element=etree.fromstring(CAF_ARCHIVO),
        rsa_private_key_pem=pem,
    )


def test_el_dd_es_byte_a_byte_el_del_ejemplo_del_sii():
    _llave, pem = _clave()
    ted = build_ted(_dte(), _caf(pem), dt.datetime(2003, 9, 8, 12, 28, 31))

    assert dd_bytes(ted.find("DD")) == DD_SII.encode("iso-8859-1")


def test_el_frmt_del_sii_valida_sobre_ese_mismo_dd():
    """Control del propio test: la referencia es la buena, no una suposición."""
    m = base64.b64decode(
        "0a4O6Kbx8Qj3K4iWSP4w7KneZYeJ+g/prihYtIEolKt3cykSxl1zO8vSXu397QhTmsX7SBEudTUx++2zDXBhZw=="
    )
    publica = rsa.RSAPublicNumbers(3, int.from_bytes(m, "big")).public_key()
    publica.verify(
        base64.b64decode(FRMT_SII), DD_SII.encode("iso-8859-1"), padding.PKCS1v15(), hashes.SHA1()
    )


def test_la_firma_cubre_exactamente_el_dd_que_viaja():
    llave, pem = _clave()
    ted = build_ted(_dte(), _caf(pem), dt.datetime(2003, 9, 8, 12, 28, 31))
    firma = base64.b64decode(ted.find("FRMT").text)

    llave.public_key().verify(firma, dd_bytes(ted.find("DD")), padding.PKCS1v15(), hashes.SHA1())
    # Y no sobre la forma con saltos de línea, que es la que el SII rechaza.
    con_saltos = dd_bytes(ted.find("DD")).replace(b"<DA>", b"\n<DA>\n")
    try:
        llave.public_key().verify(firma, con_saltos, padding.PKCS1v15(), hashes.SHA1())
    except InvalidSignature:
        pass
    else:
        raise AssertionError("la firma no debería validar sobre el DD con saltos")


def test_una_razon_social_con_enie_se_firma_en_iso_8859_1():
    """«Ñ» es el byte 0xD1 en el documento; firmar «&#209;» invalida el timbre."""
    _llave, pem = _clave()
    ted = build_ted(
        _dte(receptor="PEÑA Y CIA LTDA"), _caf(pem), dt.datetime(2003, 9, 8, 12, 28, 31)
    )
    datos = dd_bytes(ted.find("DD"))
    assert b"PE\xd1A Y CIA LTDA" in datos
    assert b"&#209;" not in datos
