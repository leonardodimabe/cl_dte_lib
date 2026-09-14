"""Construcción del nodo <Documento> del DTE (sin firma XMLDSig todavía).

Cubre los tipos 33, 34, 46, 52, 56 y 61. La diferencia principal entre ellos:
  - 34 (exenta): usa <MntExe> y NO lleva <MntNeto>/<IVA>.
  - 46 (factura de compra): la emite el comprador y suele retener el IVA
    (<ImptoReten> código 15), que se descuenta del total.
  - 52 (guía): lleva IndTraslado y la sección <Transporte>.
  - 56/61 (notas): llevan bloque(s) <Referencia> obligatorios.

Soporta descuentos/recargos por línea (DescuentoPct/DescuentoMonto) y globales
(<DscRcgGlobal>), que el set de certificación del SII exige.
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
    _global_discounts(document, dte)
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
    if dte.dispatch_type is not None:
        _t(id_doc, "TipoDespacho", str(int(dte.dispatch_type)))
    if dte.transfer_type is not None:
        _t(id_doc, "IndTraslado", str(int(dte.transfer_type)))

    issuer = etree.SubElement(header, "Emisor")
    _t(issuer, "RUTEmisor", dte.issuer.rut.value)
    _t(issuer, "RznSoc", dte.issuer.business_name)
    _t(issuer, "GiroEmis", dte.issuer.activity[:80])
    _t(issuer, "Acteco", str(dte.issuer.economic_activity))
    # Orden del XSD: Acteco, GuiaExport?, Sucursal?, CdgSIISucur?, DirOrigen...
    if dte.issuer.branch_name:
        _t(issuer, "Sucursal", dte.issuer.branch_name[:20])
    if dte.issuer.branch_code is not None:
        _t(issuer, "CdgSIISucur", str(dte.issuer.branch_code))
    _t(issuer, "DirOrigen", dte.issuer.address[:70])
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

    _transport(header, dte)

    # Orden del XSD: MntNeto, MntExe, ..., TasaIVA, IVA, ..., MntTotal.
    totals = etree.SubElement(header, "Totales")
    if dte.type.is_exempt:
        _t(totals, "MntExe", str(dte.exempt_amount))
    else:
        # MntNeto, MntExe, TasaIVA e IVA son minOccurs=0: sólo MntTotal es
        # obligatorio. Una nota que corrige una factura exenta no tiene monto
        # afecto, así que declarar "tasa 19%, IVA 0" sería inventar un impuesto
        # que no existe en el documento; el SII pide justamente lo contrario.
        if dte.net_amount or not dte.exempt_amount:
            _t(totals, "MntNeto", str(dte.net_amount))
        if dte.exempt_amount:
            _t(totals, "MntExe", str(dte.exempt_amount))
        if dte.net_amount:
            _t(totals, "TasaIVA", str(VAT_RATE))
            _t(totals, "IVA", str(dte.vat))
    # ImptoReten va después del IVA y antes de MntTotal (orden del XSD).
    for retention in dte.retentions:
        # Una retención de cero no retiene nada: declararla sería anunciar un
        # impuesto que el documento no tiene. Pasa en la nota que anula, donde
        # no hay IVA sobre el cual retener.
        if not retention.amount_over(dte.vat):
            continue
        node = etree.SubElement(totals, "ImptoReten")
        _t(node, "TipoImp", str(retention.code))
        if retention.rate is not None:
            _t(node, "TasaImp", _num(retention.rate))
        _t(node, "MontoImp", str(retention.amount_over(dte.vat)))
    _t(totals, "MntTotal", str(dte.total_amount))


def _transport(header: etree._Element, dte: DTE) -> None:
    """Sección <Transporte>, en el orden del XSD.

    Los tres últimos campos (FchSalida/HraSalida/FchLlegada) los incorporó la
    Res. Ex. N°154 y van DESPUÉS del bloque Aduana, no junto al resto.
    """
    tr = dte.transport
    if tr is None:
        return

    node = etree.SubElement(header, "Transporte")
    if tr.plate:
        _t(node, "Patente", tr.plate[:8])
    if tr.trailer_plate:
        _t(node, "PatenteCarro", tr.trailer_plate[:8])
    if tr.carrier_rut is not None:
        _t(node, "RUTTrans", tr.carrier_rut.value)
    if tr.driver is not None:
        driver = etree.SubElement(node, "Chofer")
        _t(driver, "RUTChofer", tr.driver.rut.value)
        _t(driver, "NombreChofer", tr.driver.name[:30])
    if tr.dest_address:
        _t(node, "DirDest", tr.dest_address[:70])
    if tr.dest_commune:
        _t(node, "CmnaDest", tr.dest_commune[:20])
    if tr.dest_city:
        _t(node, "CiudadDest", tr.dest_city[:20])
    # Aduana (exportación) iría aquí; aún no se emite.
    if tr.departure_date is not None:
        _t(node, "FchSalida", tr.departure_date.isoformat())
    if tr.departure_time is not None:
        _t(node, "HraSalida", tr.departure_time.strftime("%H:%M:%S"))
    if tr.arrival_date is not None:
        _t(node, "FchLlegada", tr.arrival_date.isoformat())


def _items(doc: etree._Element, dte: DTE) -> None:
    for i, item in enumerate(dte.items, start=1):
        # Orden del XSD: NroLinDet, CdgItem, IndExe, Retenedor, NmbItem, DscItem,
        # ..., QtyItem, ..., UnmdItem, PrcItem, DescuentoPct/Monto, ..., MontoItem.
        detail = etree.SubElement(doc, "Detalle")
        _t(detail, "NroLinDet", str(i))
        if item.exempt and not dte.type.is_exempt:
            _t(detail, "IndExe", "1")
        _t(detail, "NmbItem", item.name[:80])
        if item.description:
            _t(detail, "DscItem", item.description[:1000])
        # QtyItem y PrcItem son Dec12_6Type: el XSD los acota a >= 0.000001, así
        # que una línea sin cantidad o sin precio (p.ej. un traslado interno que
        # no informa valores) los omite y sólo lleva MontoItem.
        if item.quantity:
            _t(detail, "QtyItem", _num(item.quantity))
        if item.unit:
            _t(detail, "UnmdItem", item.unit[:4])
        if item.unit_price:
            _t(detail, "PrcItem", str(item.unit_price))
        if item.discount_pct:
            _t(detail, "DescuentoPct", _num(item.discount_pct))
        if item.discount:
            _t(detail, "DescuentoMonto", str(item.discount))
        _t(detail, "MontoItem", str(item.amount))


def _global_discounts(doc: etree._Element, dte: DTE) -> None:
    """Emite un <DscRcgGlobal> por cada descuento/recargo global del documento."""
    for i, dr in enumerate(dte.global_discounts, start=1):
        node = etree.SubElement(doc, "DscRcgGlobal")
        _t(node, "NroLinDR", str(i))
        _t(node, "TpoMov", dr.kind)
        if dr.reason:
            _t(node, "GlosaDR", dr.reason[:45])
        _t(node, "TpoValor", dr.value_type)
        _t(node, "ValorDR", _num(dr.value))
        if dr.exempt_indicator is not None:
            _t(node, "IndExeDR", dr.exempt_indicator)


def _references(doc: etree._Element, dte: DTE) -> None:
    for i, ref in enumerate(dte.references, start=1):
        ref_node = etree.SubElement(doc, "Referencia")
        _t(ref_node, "NroLinRef", str(i))
        _t(ref_node, "TpoDocRef", str(ref.doc_type))
        _t(ref_node, "FolioRef", str(ref.folio))
        if ref.date is not None:
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
