"""Sobre de envío EnvioDTE (SetDTE) para el SII.

Estructura:

    <EnvioDTE version="1.0" xmlns="http://www.sii.cl/SiiDte">
      <SetDTE ID="SetDoc">
        <Caratula version="1.0">
          <RutEmisor/> <RutEnvia/> <RutReceptor/>
          <FchResol/> <NroResol/> <TmstFirmaEnv/>
          <SubTotDTE><TpoDTE/><NroDTE/></SubTotDTE>
        </Caratula>
        <DTE>...</DTE>           ← uno o más DTE ya firmados
      </SetDTE>
      <Signature>...</Signature> ← firma del SetDTE
    </EnvioDTE>

Para certificación el receptor es el SII (RUT 60803000-K) y NroResol = 0.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

from lxml import etree

from . import signer
from .certificate import Certificate
from .validation import build_root, serialize_document

NS = "http://www.sii.cl/SiiDte"

# RUT del SII como receptor de los envíos (igual en certificación y producción).
SII_RECEIVER_RUT = "60803000-K"


@dataclass
class Cover:
    issuer_rut: str
    sender_rut: str  # RUT del titular del certificado que envía
    resolution_date: _dt.date  # FchResol (en certificación: fecha de hoy)
    resolution_number: int = 0  # NroResol (0 en certificación)
    receiver_rut: str = SII_RECEIVER_RUT
    # Lista de (doc_type, count) para los SubTotDTE.
    subtotals: list[tuple[int, int]] = field(default_factory=list)


def build_envelope(
    signed_dtes: list[etree._Element],
    cover: Cover,
    cert: Certificate,
    timestamp: _dt.datetime,
) -> etree._Element:
    """Arma y firma el <EnvioDTE> con los DTE ya firmados."""
    envelope = build_root("EnvioDTE", version="1.0")

    set_dte = etree.SubElement(envelope, "{%s}SetDTE" % NS, ID="SetDoc")
    _cover(set_dte, cover, timestamp)
    for dte in signed_dtes:
        set_dte.append(dte)

    # Normalizar namespaces (serializar→reparsear) ANTES de firmar, para que el
    # árbol que se firma sea idéntico al que se transmite. Sin esto, lxml elimina
    # el xmlns redundante del DTE embebido al serializar y la firma del SetDTE
    # deja de validar tras el round-trip.
    envelope = etree.fromstring(etree.tostring(envelope))
    set_dte = envelope.find("{%s}SetDTE" % NS)

    return signer.sign_set_dte(envelope, set_dte, cert)


def _cover(set_dte: etree._Element, cover: Cover, ts: _dt.datetime) -> None:
    cover_node = etree.SubElement(set_dte, "{%s}Caratula" % NS, version="1.0")
    _t(cover_node, "RutEmisor", cover.issuer_rut)
    _t(cover_node, "RutEnvia", cover.sender_rut)
    _t(cover_node, "RutReceptor", cover.receiver_rut)
    _t(cover_node, "FchResol", cover.resolution_date.isoformat())
    _t(cover_node, "NroResol", str(cover.resolution_number))
    _t(cover_node, "TmstFirmaEnv", ts.replace(microsecond=0).isoformat())
    for doc_type, count in cover.subtotals:
        subtotal = etree.SubElement(cover_node, "{%s}SubTotDTE" % NS)
        _t(subtotal, "TpoDTE", str(doc_type))
        _t(subtotal, "NroDTE", str(count))


def _t(parent: etree._Element, tag: str, value: str) -> None:
    node = etree.SubElement(parent, "{%s}%s" % (NS, tag))
    node.text = value


#: ``<DTE ...>`` sin declaración de namespace propia. No casa con ``<EnvioDTE``
#: (ahí el ``<`` va seguido de ``E``) ni con el cierre ``</DTE>``.
_DTE_ABIERTO = re.compile(rb"<DTE(?![^>]*\sxmlns=)(\s[^>]*)?>")


def serialize(envelope: etree._Element) -> bytes:
    """Serializa el sobre en ISO-8859-1 con declaración (formato que espera el SII).

    Cada ``<DTE>`` sale con su propio ``xmlns``, aunque lo herede del sobre.

    lxml elimina las declaraciones redundantes al serializar: como ``<EnvioDTE>``
    ya declara el namespace por defecto, el ``<DTE>`` de dentro queda desnudo.
    El XML es idéntico para cualquier parser —el namespace se hereda— y xmlsec
    valida la firma sin problema, porque canonicaliza con el árbol completo.

    Pero el SII no valida así: toma cada ``<DTE>`` como fragmento suelto, y ahí
    el namespace heredado ya no existe. Recalcula otro digest y responde
    **(DTE-3-505) Firma DTE Incorrecta**, que es un mensaje engañoso —la firma
    está bien— y manda a buscar el problema donde no está.

    Reponer la declaración es seguro: la C14N inclusiva no emite una declaración
    idéntica a la que ya está en contexto (Canonical XML 1.0 §2.3), así que la
    forma canónica no cambia y las firmas del documento y del SetDTE siguen
    valiendo. Verificado con xmlsec sobre un sobre real antes y después.

    Es el mismo camino que toma el módulo chileno de Odoo, que arma el sobre
    concatenando texto y por eso nunca pierde el ``xmlns`` del ``<DTE>``.
    """
    xml = serialize_document(envelope)
    return _DTE_ABIERTO.sub(rb'<DTE xmlns="' + NS.encode() + rb'"\1>', xml)
