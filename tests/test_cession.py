"""Cesión electrónica de facturas: el AEC que se manda al RPETC."""

import datetime as dt

import pytest
from lxml import etree

from dte_chile.cession import (
    NS,
    Cession,
    Party,
    Signatory,
    build_aec,
    serialize,
)
from dte_chile.signer import verify_signatures

TS = dt.datetime(2026, 9, 23, 10, 30, 0)


def _dte_firmado(cert, doc_type=33, folio=108, total=119000):
    """Un <DTE> con la forma que tiene uno emitido: encabezado y firma propia."""
    dte = etree.Element("{%s}DTE" % NS, nsmap={None: NS}, version="1.0")
    documento = etree.SubElement(dte, "{%s}Documento" % NS, ID="F%sT%s" % (folio, doc_type))
    encabezado = etree.SubElement(documento, "{%s}Encabezado" % NS)
    id_doc = etree.SubElement(encabezado, "{%s}IdDoc" % NS)
    etree.SubElement(id_doc, "{%s}TipoDTE" % NS).text = str(doc_type)
    etree.SubElement(id_doc, "{%s}Folio" % NS).text = str(folio)
    etree.SubElement(id_doc, "{%s}FchEmis" % NS).text = "2026-09-01"
    emisor = etree.SubElement(encabezado, "{%s}Emisor" % NS)
    etree.SubElement(emisor, "{%s}RUTEmisor" % NS).text = "77262159-0"
    receptor = etree.SubElement(encabezado, "{%s}Receptor" % NS)
    etree.SubElement(receptor, "{%s}RUTRecep" % NS).text = "76192083-9"
    totales = etree.SubElement(encabezado, "{%s}Totales" % NS)
    etree.SubElement(totales, "{%s}MntTotal" % NS).text = str(total)

    from dte_chile.signer import sign_enveloped

    sign_enveloped(dte, documento, cert)
    return dte


def _cesion(cert, **kwargs):
    dte = kwargs.pop("dte", None)
    return Cession(
        dte=_dte_firmado(cert) if dte is None else dte,
        assignor=Party(
            rut="77262159-0",
            name="CONSTRUCTORA DIMABE SPA",
            address="Cordova 298",
            email="pagos@dimabe.cl",
        ),
        assignee=Party(
            rut="96667560-8",
            name="FACTORING DE PRUEBA S.A.",
            address="Apoquindo 1000",
            email="operaciones@factoring.cl",
        ),
        signatory=Signatory(rut="12291733-9", name="ARTURO MUNOZ VERGARA"),
        amount=119000,
        due_date=dt.date(2026, 11, 30),
        **kwargs,
    )


def test_el_aec_lleva_sus_tres_firmas(cert):
    """DTECedido, Cesion y AEC se firman cada uno por separado.

    Con una sola firma el SII rechaza el archivo: cada nivel prueba una cosa
    distinta —el documento cedido, el contrato y el envío—.
    """
    aec = build_aec(_cesion(cert), cert, TS)

    firmas = aec.findall(".//{http://www.w3.org/2000/09/xmldsig#}Signature")
    # Cuatro: la del DTE original, más las tres de la cesión.
    assert len(firmas) == 4
    assert all(verify_signatures(etree.fromstring(etree.tostring(aec))))


def test_la_factura_cedida_conserva_su_firma(cert):
    """La firma del emisor no se puede recalcular: tiene que sobrevivir al viaje.

    Es el riesgo real de meter un documento firmado dentro de otro: basta que
    cambie un espacio o un namespace alrededor para que el digest deje de
    cuadrar, y el SII rechaza la cesión diciendo que la firma está mala.
    """
    dte = _dte_firmado(cert)
    folio = dte.findtext(".//{%s}Folio" % NS)

    # Ida y vuelta completas: serializar como se manda y volver a leer.
    aec = etree.fromstring(serialize(build_aec(_cesion(cert, dte=dte), cert, TS)))

    cedido = aec.find(".//{%s}DocumentoDTECedido/{%s}DTE" % (NS, NS))
    assert cedido is not None
    assert cedido.findtext(".//{%s}Folio" % NS) == folio
    # La firma del emisor se verifica con el DTE aislado, como la lee el SII.
    assert verify_signatures(aec)[0]


def test_la_cesion_repite_la_identidad_del_documento(cert):
    """El SII cruza la cesión con la factura por emisor, tipo y folio."""
    aec = build_aec(_cesion(cert), cert, TS)
    id_dte = aec.find(".//{%s}DocumentoCesion/{%s}IdDTE" % (NS, NS))

    assert id_dte.findtext("{%s}TipoDTE" % NS) == "33"
    assert id_dte.findtext("{%s}Folio" % NS) == "108"
    assert id_dte.findtext("{%s}RUTEmisor" % NS) == "77262159-0"
    assert id_dte.findtext("{%s}RUTReceptor" % NS) == "76192083-9"
    assert id_dte.findtext("{%s}MntTotal" % NS) == "119000"


def test_la_declaracion_jurada_nombra_el_recibo_de_mercaderias(cert):
    """Es lo que hace oponible la cesión al deudor (Ley 19.983, art. 3)."""
    aec = build_aec(_cesion(cert), cert, TS)
    declaracion = aec.findtext(".//{%s}DeclaracionJurada" % NS)

    assert "recibos" in declaracion
    assert "19.983" in declaracion
    assert "FACTORING DE PRUEBA S.A." in declaracion
    assert "ARTURO MUNOZ VERGARA" in declaracion


def test_se_puede_poner_otra_declaracion(cert):
    aec = build_aec(_cesion(cert, declaration="Declaro lo que corresponda."), cert, TS)
    assert aec.findtext(".//{%s}DeclaracionJurada" % NS) == "Declaro lo que corresponda."


def test_una_boleta_no_se_cede(cert):
    """Sólo se cede lo que lleva copia cedible."""
    with pytest.raises(ValueError, match="no es cedible"):
        build_aec(_cesion(cert, dte=_dte_firmado(cert, doc_type=39)), cert, TS)


def test_la_caratula_dice_quien_cede_y_a_quien(cert):
    aec = build_aec(_cesion(cert), cert, TS)
    caratula = aec.find(".//{%s}Caratula" % NS)

    assert caratula.findtext("{%s}RutCedente" % NS) == "77262159-0"
    assert caratula.findtext("{%s}RutCesionario" % NS) == "96667560-8"
    assert caratula.findtext("{%s}TmstFirmaEnvio" % NS) == "2026-09-23T10:30:00"


def test_el_archivo_sale_en_el_formato_que_espera_el_sii(cert):
    xml = serialize(build_aec(_cesion(cert), cert, TS))

    assert xml.startswith(b'<?xml version="1.0" encoding="ISO-8859-1"?>')
    assert b"AEC_v10.xsd" in xml
    # El monto de la cesión y el vencimiento son lo que el factoring compra.
    assert b"<MontoCesion>119000</MontoCesion>" in xml
    assert b"<UltimoVencimiento>2026-11-30</UltimoVencimiento>" in xml
