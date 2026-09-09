

def test_lee_el_desglose_por_tipo_de_documento():
    """Respuesta REAL de Maullín, copiada tal cual de una consulta.

    Importa que sea la de verdad y no la del esquema documentado: el SII
    describe un ``ESTADISTICA`` que envuelve cada grupo y **no lo envía**. Los
    campos van planos dentro de RESP_BODY, uno tras otro. Una versión anterior
    de este test usaba la forma documentada, pasaba, y el parser devolvía lista
    vacía contra el Servicio real.

    Es el set básico de una certificación: ESTADO EPR —envío procesado— con sus
    ocho documentos rechazados dentro.
    """
    from lxml import etree

    from dte_chile.sii_client import _parse_stats

    respuesta = b"""<?xml version="1.0" encoding="UTF-8"?>
<SII:RESPUESTA xmlns:SII="http://www.sii.cl/XMLSchema">
    <SII:RESP_BODY>
        <TIPO_DOCTO>33</TIPO_DOCTO>
        <INFORMADOS>4</INFORMADOS>
        <ACEPTADOS>0</ACEPTADOS>
        <RECHAZADOS>4</RECHAZADOS>
        <REPAROS>0</REPAROS>
        <TIPO_DOCTO>56</TIPO_DOCTO>
        <INFORMADOS>1</INFORMADOS>
        <ACEPTADOS>0</ACEPTADOS>
        <RECHAZADOS>1</RECHAZADOS>
        <REPAROS>0</REPAROS>
        <TIPO_DOCTO>61</TIPO_DOCTO>
        <INFORMADOS>3</INFORMADOS>
        <ACEPTADOS>0</ACEPTADOS>
        <RECHAZADOS>3</RECHAZADOS>
        <REPAROS>0</REPAROS>
    </SII:RESP_BODY>
    <SII:RESP_HDR>
        <TRACKID>0257259806</TRACKID>
        <ESTADO>EPR</ESTADO>
        <GLOSA>Envio Procesado</GLOSA>
    </SII:RESP_HDR>
</SII:RESPUESTA>"""

    stats = _parse_stats(etree.fromstring(respuesta))
    assert [s.doc_type for s in stats] == [33, 56, 61]
    assert [s.informed for s in stats] == [4, 1, 3]
    assert sum(s.accepted for s in stats) == 0
    assert sum(s.rejected for s in stats) == 8


def test_tambien_lee_la_forma_documentada_con_ESTADISTICA():
    """Por si el Servicio la usa en otro ambiente: no cuesta soportarla."""
    from lxml import etree

    from dte_chile.sii_client import _parse_stats

    respuesta = b"""<SII:RESPUESTA xmlns:SII="http://www.sii.cl/XMLSchema">
      <SII:RESP_BODY>
        <ESTADISTICA><TIPO_DOCTO>110</TIPO_DOCTO><INFORMADOS>3</INFORMADOS>
          <ACEPTADOS>3</ACEPTADOS><RECHAZADOS>0</RECHAZADOS><REPAROS>0</REPAROS></ESTADISTICA>
      </SII:RESP_BODY>
    </SII:RESPUESTA>"""
    stats = _parse_stats(etree.fromstring(respuesta))
    assert [(s.doc_type, s.accepted) for s in stats] == [(110, 3)]


def test_un_libro_no_trae_desglose():
    """El IECV no lleva ESTADISTICA: su veredicto es el ESTADO entero (LOK)."""
    from lxml import etree

    from dte_chile.sii_client import _parse_stats

    respuesta = b"""<SII:RESPUESTA xmlns:SII="http://www.sii.cl/XMLSchema">
      <SII:RESP_HDR><ESTADO>LOK</ESTADO>
      <GLOSA>Envio de Libro Aceptado - Cuadrado</GLOSA></SII:RESP_HDR>
    </SII:RESPUESTA>"""
    assert _parse_stats(etree.fromstring(respuesta)) == []
