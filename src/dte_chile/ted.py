"""Timbre Electrónico (TED).

El TED es el "sello" que prueba que el folio fue autorizado. Estructura:

    <TED version="1.0">
      <DD>                      ← datos del documento
        <RE>..</RE> <TD>..</TD> <F>..</F> <FE>..</FE>
        <RR>..</RR> <RSR>..</RSR> <MNT>..</MNT> <IT1>..</IT1>
        <CAF>..</CAF>           ← incrustado tal cual desde el archivo CAF
        <TSTED>..</TSTED>       ← timestamp de generación del timbre
      </DD>
      <FRMT algoritmo="SHA1withRSA">..</FRMT>  ← firma RSA del <DD> con llave del CAF
    </TED>

La firma (FRMT) se hace con la **llave privada del CAF**, NO con el certificado.
"""

from __future__ import annotations

import base64
import datetime as _dt

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from .caf import CAF
from .models import DTE


def build_ted(dte: DTE, caf: CAF, timestamp: _dt.datetime) -> etree._Element:
    """Construye y firma el nodo <TED> de un DTE."""
    if not caf.contains(dte.folio):
        raise ValueError(
            f"Folio {dte.folio} fuera del rango CAF [{caf.folio_from}-{caf.folio_to}]."
        )

    ted = etree.Element("TED", version="1.0")
    dd = etree.SubElement(ted, "DD")

    _text(dd, "RE", dte.issuer.rut.value)
    _text(dd, "TD", str(int(dte.type)))
    _text(dd, "F", str(dte.folio))
    _text(dd, "FE", dte.issue_date.isoformat())
    _text(dd, "RR", dte.receiver.rut.value)
    _text(dd, "RSR", dte.receiver.business_name[:40])
    _text(dd, "MNT", str(dte.total_amount))
    _text(dd, "IT1", dte.items[0].name[:40])

    # El bloque CAF va incrustado literalmente dentro del DD.
    dd.append(_clone(caf.caf_element))

    _text(dd, "TSTED", timestamp.replace(microsecond=0).isoformat())

    # Firma del DD con la llave privada del CAF (RSA, SHA1).
    signature = _sign_dd(dd, caf.rsa_private_key_pem)
    frmt = etree.SubElement(ted, "FRMT", algoritmo="SHA1withRSA")
    frmt.text = signature

    return ted


def _sign_dd(dd: etree._Element, rsa_private_key_pem: str) -> str:
    """Firma el <DD> serializado con RSA-SHA1, devuelve la firma en base64."""
    dd_bytes = etree.tostring(dd)
    private_key = serialization.load_pem_private_key(
        rsa_private_key_pem.encode("latin-1"), password=None
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("La llave del CAF (RSASK) no es RSA.")
    signature = private_key.sign(dd_bytes, padding.PKCS1v15(), hashes.SHA1())
    return base64.b64encode(signature).decode("ascii")


def _text(parent: etree._Element, tag: str, value: str) -> etree._Element:
    node = etree.SubElement(parent, tag)
    node.text = value
    return node


def _clone(node: etree._Element) -> etree._Element:
    return etree.fromstring(etree.tostring(node))
