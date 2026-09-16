"""Cada <DTE> del sobre lleva su propio xmlns, y las firmas siguen valiendo.

Nace de un rechazo real: el SII devolvió «(DTE-3-505) Firma DTE Incorrecta» en
los 8 documentos de un sobre cuyas firmas eran correctas —xmlsec las validaba
todas—. El motivo era que lxml elimina la declaración de namespace redundante
del <DTE> al serializar, y el SII valida cada <DTE> como fragmento suelto.
"""

import datetime as dt
import re
from collections import Counter

import pytest
import xmlsec
from lxml import etree

from dte_chile.envelope import Cover, build_envelope
from dte_chile.envelope import serialize as serialize_envelope
from dte_chile.models import DTE, DTEType, Issuer, Item, Receiver
from dte_chile.signer import sign_document, verify_signatures
from dte_chile.xml_builder import build_document

TS = dt.datetime(2026, 9, 14, 11, 36, 0)
ISSUE_DATE = dt.date(2026, 9, 14)
NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"
NS_DTE = "http://www.sii.cl/SiiDte"


def _factura(folio: int, receptor: str) -> DTE:
    return DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=folio,
        issue_date=ISSUE_DATE,
        issuer=Issuer(
            rut="76158145-7",
            business_name="CONSTRUCTORA DE PRUEBA SPA",
            activity="Obras de construccion",
            economic_activity=439000,
            address="Camino Melipilla 1234",
            commune="RANCAGUA",
            city="RANCAGUA",
        ),
        # Con eñe a propósito: el sobre va en ISO-8859-1 y así también se
        # comprueba que la codificación no rompe el digest.
        receiver=Receiver(
            rut="17099910-K",
            business_name=receptor,
            activity="Comercio al por mayor",
            address="Av. Cliente 100",
            commune="Providencia",
            city="Santiago",
        ),
        items=[Item(name="Cajón AFECTO", quantity=2, unit_price=1000)],
    )


@pytest.fixture
def sobre(cert, caf_factory) -> bytes:
    documentos = [
        _factura(1, "INVERSIONES VIÑEDOS Y FRUTALES LIMITADA"),
        _factura(2, "CLIENTE DOS SPA"),
    ]
    firmados = [
        sign_document(build_document(d, caf_factory(int(d.type)), TS), cert) for d in documentos
    ]
    cover = Cover(
        issuer_rut="76158145-7",
        sender_rut="77777777-7",
        resolution_date=dt.date(2026, 8, 26),
        subtotals=sorted(Counter(int(d.type) for d in documentos).items()),
    )
    return serialize_envelope(build_envelope(firmados, cover, cert, TS))


def test_cada_dte_declara_su_namespace(sobre):
    """Sin esto el SII responde DTE-3-505 aunque la firma sea correcta."""
    aperturas = re.findall(rb"<DTE[^>]*>", sobre)
    assert len(aperturas) == 2
    for tag in aperturas:
        assert b'xmlns="http://www.sii.cl/SiiDte"' in tag, tag


def test_no_se_duplica_la_declaracion(sobre):
    """Reponerla dos veces daría un XML mal formado."""
    for tag in re.findall(rb"<DTE[^>]*>", sobre):
        assert tag.count(b"xmlns=") == 1, tag
    # Y el sobre sigue siendo parseable, que es la comprobación de verdad.
    assert etree.fromstring(sobre).tag == "{%s}EnvioDTE" % NS_DTE


def _verifica(arbol) -> bool:
    """¿Valida la primera firma de este árbol, con este árbol como contexto?"""
    firma = arbol.find(".//{%s}Signature" % NS_DSIG)
    uri = (firma.find(".//{%s}Reference" % NS_DSIG).get("URI") or "").lstrip("#")
    objetivo = next(n for n in arbol.iter() if n.get("ID") == uri)
    ctx = xmlsec.SignatureContext()
    ctx.register_id(objetivo, id_attr="ID")
    x509 = firma.find(".//{%s}X509Certificate" % NS_DSIG)
    pem = (
        b"-----BEGIN CERTIFICATE-----\n"
        + "".join((x509.text or "").split()).encode()
        + b"\n-----END CERTIFICATE-----\n"
    )
    ctx.key = xmlsec.Key.from_memory(pem, xmlsec.constants.KeyDataFormatCertPem, None)
    try:
        ctx.verify(firma)
        return True
    except xmlsec.VerificationError:
        return False


def _fragmentos(sobre: bytes) -> list[bytes]:
    """Cada <DTE> como lo extrae el SII: suelto, con su propia declaración."""
    decl = b'<?xml version="1.0" encoding="ISO-8859-1"?>'
    return [decl + f for f in re.findall(rb"<DTE[^>]*>.*?</DTE>", sobre, re.S)]


def test_la_firma_del_documento_valida_como_fragmento(sobre):
    """Así es como el SII valida cada DTE, y por tanto la única que importa.

    Con un ``xmlns:xsi`` de más en el <DTE> al firmar, esto daba inválida y el
    SII devolvía «(DTE-3-505) Firma DTE Incorrecta» pese a que la firma era
    correcta dentro del sobre. Costó dos envíos rechazados descubrirlo.
    """
    trozos = _fragmentos(sobre)
    assert len(trozos) == 2
    for i, frag in enumerate(trozos, start=1):
        assert _verifica(etree.fromstring(frag)), f"el DTE {i} no valida como fragmento"


def test_el_dte_no_declara_xsi(sobre):
    """La causa raíz, fijada aparte para que el motivo quede a la vista."""
    for tag in re.findall(rb"<DTE[^>]*>", sobre):
        assert b"xmlns:xsi" not in tag, tag


def test_la_firma_del_sobre_valida_con_el_sobre_entero(sobre):
    """El SetDTE sí se valida en el documento completo: es la raíz, no un trozo."""
    arbol = etree.fromstring(sobre)
    firma = next(
        f
        for f in arbol.findall(".//{%s}Signature" % NS_DSIG)
        if f.getparent().tag == "{%s}EnvioDTE" % NS_DTE
    )
    uri = (firma.find(".//{%s}Reference" % NS_DSIG).get("URI") or "").lstrip("#")
    assert uri == "SetDoc"
    ctx = xmlsec.SignatureContext()
    ctx.register_id(next(n for n in arbol.iter() if n.get("ID") == uri), id_attr="ID")
    x509 = firma.find(".//{%s}X509Certificate" % NS_DSIG)
    pem = (
        b"-----BEGIN CERTIFICATE-----\n"
        + "".join((x509.text or "").split()).encode()
        + b"\n-----END CERTIFICATE-----\n"
    )
    ctx.key = xmlsec.Key.from_memory(pem, xmlsec.constants.KeyDataFormatCertPem, None)
    ctx.verify(firma)


def test_los_timbres_validan_tal_como_viajan(sobre):
    """El timbre de una factura con «Ñ» en la razón social, sobre los bytes."""
    from dte_chile.ted import verify_stamps

    assert verify_stamps(sobre) == [True, True]


def test_el_sobre_declara_iso_8859_1(sobre):
    """La reposición se hace sobre bytes: no puede alterar la cabecera."""
    assert sobre.startswith(b'<?xml version="1.0" encoding="ISO-8859-1"?>')
    assert "VIÑEDOS".encode("ISO-8859-1") in sobre


# --------------------------------------------------------------------------- #
#  La firma se comprueba antes de devolverla
# --------------------------------------------------------------------------- #
def test_una_firma_que_no_verifica_se_repite(monkeypatch, cert, caf_factory):
    """Firmar y no mirar el resultado costó un rechazo del SII.

    `(DTE-3-505) Firma DTE Incorrecta` en UNO de los tres documentos de un sobre
    de exportación, con el mismo contenido que había firmado bien el día
    anterior y que vuelve a firmar bien al reintentarlo. El documento ya trae su
    folio y su timbre, así que volver a firmarlo no gasta nada.
    """
    from dte_chile import signer

    real = signer.verify_signatures
    llamadas = []

    def falla_la_primera(nodo):
        llamadas.append(1)
        return [False] if len(llamadas) == 1 else real(nodo)

    monkeypatch.setattr(signer, "verify_signatures", falla_la_primera)
    dte = signer.sign_document(build_document(_factura(9, "CLIENTE"), caf_factory(33), TS), cert)

    assert len(llamadas) == 2, "debió volver a firmar tras la primera comprobación fallida"
    # Y lo que devuelve lleva UNA sola firma, no la mala más la buena.
    assert len(dte.findall("{%s}Signature" % NS_DSIG)) == 1
    assert real(dte)[0] is True


def test_si_la_firma_nunca_verifica_se_aborta(monkeypatch, cert, caf_factory):
    """Mejor fallar al emitir que mandar al SII un sobre que va a rechazar:
    eso cuesta además el intento y un día de espera hasta su respuesta."""
    from dte_chile import signer

    monkeypatch.setattr(signer, "verify_signatures", lambda nodo: [False])
    with pytest.raises(ValueError, match="DTE-3-505"):
        signer.sign_document(build_document(_factura(9, "CLIENTE"), caf_factory(33), TS), cert)


# --------------------------------------------------------------------------- #
#  El corte en líneas no entra en lo ya firmado
# --------------------------------------------------------------------------- #
def test_el_corte_en_lineas_no_toca_un_dte_ya_firmado(cert, caf_factory):
    """`wrap_long_lines` debe dejar intacto lo que ya lleva firma.

    El corte reparte en líneas lo que pasa de 4.000 caracteres, y tiene que
    ocurrir ANTES de firmar: el espacio en blanco es contenido para la
    canonicalización. Pero al firmar el <SetDTE> la misma función recorre un
    sobre lleno de <DTE> YA firmados, y descender dentro de uno le mueve el
    digest.

    Costó tres emisiones. El SII devolvió «(DTE-3-505) Firma DTE Incorrecta» en
    uno de los tres documentos de un sobre de exportación —el único cuyo
    <Documento> quedaba cerca del límite— y aceptó los otros dos. El contenido
    era correcto y el mismo documento firmaba bien por separado.
    """
    from dte_chile.validation import wrap_long_lines

    dte = sign_document(build_document(_factura(1, "CLIENTE"), caf_factory(33), TS), cert)
    antes = etree.tostring(dte, with_tail=False)
    assert verify_signatures(dte)[0], "el DTE debe firmar bien antes de la prueba"

    # Un padre grande, como el <SetDTE> de un sobre: fuerza el reparto en líneas.
    padre = etree.SubElement(etree.Element("SetDTE"), "x")  # placeholder
    padre = padre.getparent()
    padre.remove(padre[0])
    padre.append(dte)
    etree.SubElement(padre, "Relleno").text = "x" * 5000

    wrap_long_lines(padre)

    assert etree.tostring(dte, with_tail=False) == antes, (
        "el corte en líneas entró dentro de un DTE ya firmado y le movió el digest"
    )
    assert verify_signatures(dte)[0], "la firma dejó de valer tras armar el sobre"
