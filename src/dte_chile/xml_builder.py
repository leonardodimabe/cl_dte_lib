"""Construcción del nodo <Documento> del DTE (sin firma XMLDSig todavía).

Cubre los tipos 33, 34, 56 y 61. La diferencia principal entre ellos:
  - 34 (exenta): usa <MntExe> y NO lleva <MntNeto>/<IVA>.
  - 56/61 (notas): llevan bloque(s) <Referencia> obligatorios.
"""

from __future__ import annotations

import datetime as _dt

from lxml import etree

from .caf import CAF
from .models import DTE, VAT_RATE
from .ted import build_ted


def build_document(dte: DTE, caf: CAF, timestamp: _dt.datetime) -> etree._Element:
    """Devuelve el nodo <Documento ID=...> listo para firmar con XMLDSig."""
    dte.validate()

    doc_id = f"F{dte.folio}T{int(dte.type)}"
    document = etree.Element("Documento", ID=doc_id)

    _header(document, dte)
    _items(document, dte)
    _references(document, dte)

    # Timbre + timestamp de firma
    document.append(build_ted(dte, caf, timestamp))
    ts = etree.SubElement(document, "TmstFirma")
    ts.text = timestamp.replace(microsecond=0).isoformat()

    return document


def _header(doc: etree._Element, dte: DTE) -> None:
    header = etree.SubElement(doc, "Encabezado")

    id_doc = etree.SubElement(header, "IdDoc")
    _t(id_doc, "TipoDTE", str(int(dte.type)))
    _t(id_doc, "Folio", str(dte.folio))
    _t(id_doc, "FchEmis", dte.issue_date.isoformat())

    issuer = etree.SubElement(header, "Emisor")
    _t(issuer, "RUTEmisor", dte.issuer.rut.value)
    _t(issuer, "RznSoc", dte.issuer.business_name)
    _t(issuer, "GiroEmis", dte.issuer.activity[:80])
    _t(issuer, "Acteco", str(dte.issuer.economic_activity))
    _t(issuer, "DirOrigen", dte.issuer.address)
    _t(issuer, "CmnaOrigen", dte.issuer.commune)
    if dte.issuer.city:
        _t(issuer, "CiudadOrigen", dte.issuer.city)

    receiver = etree.SubElement(header, "Receptor")
    _t(receiver, "RUTRecep", dte.receiver.rut.value)
    _t(receiver, "RznSocRecep", dte.receiver.business_name[:100])
    _t(receiver, "GiroRecep", dte.receiver.activity[:40])
    _t(receiver, "DirRecep", dte.receiver.address)
    _t(receiver, "CmnaRecep", dte.receiver.commune)
    if dte.receiver.city:
        _t(receiver, "CiudadRecep", dte.receiver.city)

    totals = etree.SubElement(header, "Totales")
    if dte.type.is_exempt:
        _t(totals, "MntExe", str(dte.exempt_amount))
    else:
        if dte.exempt_amount:
            _t(totals, "MntExe", str(dte.exempt_amount))
        _t(totals, "MntNeto", str(dte.net_amount))
        _t(totals, "TasaIVA", str(VAT_RATE))
        _t(totals, "IVA", str(dte.vat))
    _t(totals, "MntTotal", str(dte.total_amount))


def _items(doc: etree._Element, dte: DTE) -> None:
    for i, item in enumerate(dte.items, start=1):
        detail = etree.SubElement(doc, "Detalle")
        _t(detail, "NroLinDet", str(i))
        _t(detail, "NmbItem", item.name[:80])
        if item.description:
            _t(detail, "DscItem", item.description[:1000])
        if item.exempt and not dte.type.is_exempt:
            _t(detail, "IndExe", "1")
        _t(detail, "QtyItem", _num(item.quantity))
        if item.unit:
            _t(detail, "UnmdItem", item.unit[:4])
        _t(detail, "PrcItem", str(item.unit_price))
        _t(detail, "MontoItem", str(item.amount))


def _references(doc: etree._Element, dte: DTE) -> None:
    for i, ref in enumerate(dte.references, start=1):
        ref_node = etree.SubElement(doc, "Referencia")
        _t(ref_node, "NroLinRef", str(i))
        _t(ref_node, "TpoDocRef", str(ref.doc_type))
        _t(ref_node, "FolioRef", str(ref.folio))
        _t(ref_node, "FchRef", ref.date.isoformat())
        if ref.code is not None:
            _t(ref_node, "CodRef", str(int(ref.code)))
        if ref.reason:
            _t(ref_node, "RazonRef", ref.reason[:90])


def _t(parent: etree._Element, tag: str, value: str) -> None:
    node = etree.SubElement(parent, tag)
    node.text = value


def _num(value: float) -> str:
    """Formatea cantidad: entero si no tiene decimales, si no hasta 6 decimales."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")
