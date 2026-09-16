"""Representación impresa del DTE con timbre electrónico PDF417.

Genera una página HTML lista para imprimir o exportar a PDF (desde el navegador),
con el código de barras **PDF417** que codifica el TED — el "timbre" que el SII
exige en la representación impresa.

El PDF417 codifica los bytes del <TED> tal cual van en el XML, de modo que un
lector pueda reconstruir el DD y verificar el timbre contra la RSAPK del CAF.

Se emiten dos ejemplares: el **tributario** y el **cedible** (Ley 19.983), que el
SII exige adjuntar para factura, factura exenta, guía de despacho y factura de
compra. Los descuentos —por línea y globales— se imprimen explícitamente y las
cifras llevan separador de miles con punto, como pide el set de certificación.

Se evita depender de librerías PDF nativas (reportlab/weasyprint): el HTML es
auto-contenido (el PDF417 va embebido como PNG base64) y se imprime a PDF desde
el navegador. Si más adelante se requiere PDF directo, se puede enchufar un
backend (weasyprint/reportlab) sin tocar este layout.
"""

from __future__ import annotations

import base64
import datetime as _dt
import io
from dataclasses import dataclass

from lxml import etree
from pdf417gen import encode, render_image

from .document_types import DTEType, TransferType
from .models import DTE, VAT_RETENTION_TOTAL, Item
from .rut import format_rut


@dataclass
class ResolutionInfo:
    """Resolución que autoriza al emisor. En certificación: number=0."""

    number: int
    date: _dt.date
    sii_office: str = "SANTIAGO"


# --------------------------------------------------------------------------- #
#  PDF417
# --------------------------------------------------------------------------- #
def ted_bytes(element: etree._Element) -> bytes:
    """Extrae el <TED> LITERAL tal como aparece en el XML (mismos bytes).

    Sirve tanto si se pasa el <Documento> como el <DTE> completo. Se extrae por
    substring de la serialización para evitar que lxml inyecte el namespace
    heredado al serializar el sub-elemento aislado (lo que dejaría el timbre del
    barcode distinto al del XML y rompería la verificación del SII).
    """
    raw = etree.tostring(element, encoding="ISO-8859-1", xml_declaration=False)
    start = raw.find(b"<TED")
    end = raw.find(b"</TED>")
    if start == -1 or end == -1:
        raise ValueError("El documento no contiene <TED>; ¿está timbrado?")
    return raw[start : end + len(b"</TED>")]


def generate_pdf417_png(
    ted_xml: bytes, columns: int = 18, security_level: int = 5, scale: int = 2
) -> bytes:
    """Genera el PNG del PDF417 que codifica el TED.

    Parámetros acordes a la norma SII (nivel de corrección de errores 5).
    """
    codes = encode(ted_xml, columns=columns, security_level=security_level)
    image = render_image(codes, scale=scale, ratio=3, padding=4)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
#  HTML
# --------------------------------------------------------------------------- #
TAX_COPY = "TRIBUTARIO"
TRANSFERABLE_COPY = "CEDIBLE"

# Declaración del acuse de recibo (Ley 19.983) que va en el ejemplar cedible.
CESSION_DECLARATION = (
    "El acuse de recibo que se declara en este acto, de acuerdo a lo dispuesto en la "
    "letra b) del Art. 4°, y la letra c) del Art. 5° de la Ley 19.983, acredita que la "
    "entrega de mercaderías o servicio(s) prestado(s) ha(n) sido recibido(s)."
)


def is_cedible(dte: DTE) -> bool:
    """True si al documento le corresponde ejemplar cedible.

    En una guía de despacho que no constituye venta (traslado interno, entrega
    gratuita, etc.) el cedible es inoficioso: no hay crédito que ceder.
    """
    if dte.type.is_dispatch_note:
        return dte.transfer_type is TransferType.SALE
    return dte.type in (
        DTEType.AFFECTED_INVOICE,
        DTEType.EXEMPT_INVOICE,
        DTEType.PURCHASE_INVOICE,
    )


def generate_html(
    dte: DTE,
    document: etree._Element,
    resolution: ResolutionInfo,
    *,
    copy: str = TAX_COPY,
    verification_url: str = "",
) -> str:
    """Construye un ejemplar de la representación impresa (HTML auto-contenido).

    ``verification_url`` es el sitio donde el consumidor puede consultar su
    boleta. El SII lo **exige** en la representación impresa de la boleta
    electrónica, y debe estar publicado antes de aprobar la certificación.
    """
    barcode = _barcode(document)
    return _TEMPLATE.format(
        style=_STYLE, body=_body(dte, resolution, barcode, copy, verification_url)
    )


def generate_copies(
    dte: DTE,
    document: etree._Element,
    resolution: ResolutionInfo,
    *,
    verification_url: str = "",
) -> str:
    """Ejemplar tributario y, si corresponde, cedible — en un solo HTML.

    Van en páginas separadas para que salgan como dos hojas al imprimir.
    """
    barcode = _barcode(document)
    copies = [TAX_COPY] + ([TRANSFERABLE_COPY] if is_cedible(dte) else [])
    return _TEMPLATE.format(
        style=_STYLE,
        body="".join(_body(dte, resolution, barcode, copy, verification_url) for copy in copies),
    )


def save_html(html: str, path) -> None:
    from pathlib import Path

    Path(path).write_text(html, encoding="utf-8")


def _barcode(document: etree._Element) -> str:
    return base64.b64encode(generate_pdf417_png(ted_bytes(document))).decode("ascii")


# --------------------------------------------------------------------------- #
#  Secciones
# --------------------------------------------------------------------------- #
def _body(
    dte: DTE,
    resolution: ResolutionInfo,
    barcode: str,
    copy: str,
    verification_url: str = "",
) -> str:
    issuer_address = _esc(f"{dte.issuer.address}, {dte.issuer.commune}")
    receiver_address = _esc(f"{dte.receiver.address}, {dte.receiver.commune}")
    return f"""<div class="doc">
  <div class="top">
    <div class="emisor">
      <h1>{_esc(dte.issuer.business_name)}</h1>
      <div>{_esc(dte.issuer.activity)}</div>
      <div>{issuer_address}</div>
    </div>
    <div class="recuadro">
      <div class="rut">R.U.T. {_rut_display(dte.issuer.rut.value)}</div>
      <div class="tipo">{dte.type.label.upper()}</div>
      <div class="folio">N° {dte.folio}</div>
      <div class="sii">S.I.I. — {_esc(resolution.sii_office)}</div>
    </div>
  </div>
  <div class="cabecera">
    <span class="ejemplar">{_esc(copy)}</span>
    <span>Fecha emisión: {dte.issue_date.strftime("%d-%m-%Y")}</span>
  </div>
  {_receiver_block(dte, receiver_address)}
  {_transfer_block(dte)}
  {_items_table(dte)}
  {_references(dte)}
  <div class="totales"><table>{_totals(dte)}</table></div>
  <div class="timbre">
    <img src="data:image/png;base64,{barcode}" alt="Timbre Electrónico SII">
    <div class="ley">Timbre Electrónico SII</div>
    <div class="ley">{_resolution_legend(resolution)}</div>
    {_verification_note(dte, verification_url)}
  </div>
  {_cession_block(copy)}
</div>"""


def _transfer_block(dte: DTE) -> str:
    """Traslado y transporte. La Res. Ex. N°154 los exige también en el impreso."""
    if not dte.accompanies_goods:
        return ""

    rows: list[tuple[str, str]] = []
    if dte.transfer_type is not None:
        rows.append(("Tipo de traslado", dte.transfer_type.label))
    if dte.dispatch_type is not None:
        rows.append(("Tipo de despacho", dte.dispatch_type.label))

    transport = dte.transport
    if transport is not None:
        if transport.plate:
            rows.append(("Patente", transport.plate))
        if transport.trailer_plate:
            rows.append(("Patente carro/remolque", transport.trailer_plate))
        if transport.carrier_rut is not None:
            rows.append(("Transportista", _rut_display(transport.carrier_rut.value)))
        if transport.driver is not None:
            driver_rut = _rut_display(transport.driver.rut.value)
            rows.append(("Chofer", f"{transport.driver.name} ({driver_rut})"))
        destination = ", ".join(
            part for part in (transport.dest_address, transport.dest_commune) if part
        )
        if destination:
            rows.append(("Destino", destination))
        if transport.departure_date is not None:
            departure = transport.departure_date.strftime("%d-%m-%Y")
            if transport.departure_time is not None:
                departure += " " + transport.departure_time.strftime("%H:%M:%S")
            rows.append(("Salida", departure))
        if transport.arrival_date is not None:
            rows.append(("Llegada", transport.arrival_date.strftime("%d-%m-%Y")))

    if not rows:
        return ""
    cells = "".join(f"<div><b>{label}:</b> {_esc(value)}</div>" for label, value in rows)
    return f'<div class="traslado"><div class="titulo">Traslado</div>{cells}</div>'


def _items_table(dte: DTE) -> str:
    # La columna de descuento aparece sólo si alguna línea lo trae: el set exige
    # mostrarlo, pero no hay por qué ensuciar los documentos que no lo tienen.
    has_discount = any(item.discount for item in dte.items)
    has_unit = any(item.unit for item in dte.items)

    columns = ["#", "Detalle"]
    if has_unit:
        columns.append("Un.")
    columns += ["Cant.", "Precio"]
    if has_discount:
        columns.append("Desc.")
    columns.append("Monto")
    left_aligned = ("#", "Detalle", "Un.")
    head = "".join(
        f"<th>{name}</th>" if name in left_aligned else f'<th class="r">{name}</th>'
        for name in columns
    )

    rows = []
    for position, item in enumerate(dte.items, start=1):
        exempt_tag = ' <span class="exe">EXENTO</span>' if item.exempt else ""
        cells = [f"<td>{position}</td>", f"<td>{_esc(item.name)}{exempt_tag}</td>"]
        if has_unit:
            cells.append(f"<td>{_esc(item.unit)}</td>")
        cells.append(f'<td class="r">{_qty(item.quantity)}</td>')
        cells.append(f'<td class="r">{_money_or_dash(item.unit_price)}</td>')
        if has_discount:
            cells.append(f'<td class="r">{_line_discount(item)}</td>')
        cells.append(f'<td class="r">{_money(item.amount)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    body = "".join(rows)
    return f'<table class="det"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _line_discount(item: Item) -> str:
    if not item.discount:
        return ""
    if item.discount_pct:
        return f"{_pct(item.discount_pct)} ({_money(item.discount)})"
    return _money(item.discount)


def _totals(dte: DTE) -> str:
    rows = ""
    # Los descuentos/recargos globales se muestran con su monto, como pide el set.
    for discount in dte.global_discounts:
        if dte.type.is_exempt or discount.scope != "afecto":
            base = dte.base_exempt
        else:
            base = dte.base_affected
        kind = "Descuento" if discount.kind == "D" else "Recargo"
        sign = "-" if discount.kind == "D" else "+"
        value = _pct(discount.value) if discount.value_type == "%" else _money(discount.value)
        label = f"{kind} global {value}"
        if discount.reason:
            label += f" — {_esc(discount.reason)}"
        amount = _money(discount.amount_over(base))
        rows += f'<tr><td>{label}</td><td class="r">{sign}{amount}</td></tr>'

    if dte.type.is_exempt:
        rows += _total_row("Exento", dte.exempt_amount)
    else:
        rows += _total_row("Neto", dte.net_amount)
        if dte.exempt_amount:
            rows += _total_row("Exento", dte.exempt_amount)
        rows += _total_row("IVA (19%)", dte.vat)
    for retention in dte.retentions:
        label = (
            "IVA retenido"
            if retention.code == VAT_RETENTION_TOTAL
            else f"Retención {retention.code}"
        )
        amount = _money(retention.amount_over(dte.vat))
        rows += f'<tr><td>{label}</td><td class="r">-{amount}</td></tr>'
    return rows + _total_row("TOTAL", dte.total_amount, bold=True)


def _receiver_block(dte: DTE, receiver_address: str) -> str:
    """Datos del receptor.

    La boleta al consumidor final no lo identifica: el XML trae el RUT genérico
    66.666.666-6 y nada más, y la Res. Ex. N°74 no pide receptor en su
    representación. Imprimir «Señor(es):», «Giro:» y «Dirección:» vacíos sólo
    ensucia el papel. Si la boleta sí trae un comprador con nombre, se muestra.
    """
    if dte.type.is_receipt and not dte.receiver.business_name:
        return ""
    return f"""<div class="receptor">
    <div><b>Señor(es):</b> {_esc(dte.receiver.business_name)}
         &nbsp; <b>R.U.T.:</b> {_rut_display(dte.receiver.rut.value)}</div>
    <div><b>Giro:</b> {_esc(dte.receiver.activity)}</div>
    <div><b>Dirección:</b> {receiver_address}</div>
  </div>"""


def _references(dte: DTE) -> str:
    if not dte.references:
        return ""
    if dte.type.is_receipt:
        return _receipt_references(dte)
    rows = "".join(
        f"<div class='ref'>Ref: {_doc_label(ref.doc_type)} N° {ref.folio} "
        f"{' (' + ref.date.strftime('%d-%m-%Y') + ')' if ref.date else ''}"
        f"{' — ' + _esc(ref.reason) if ref.reason else ''}</div>"
        for ref in dte.references
    )
    return f"<div class='refs'>{rows}</div>"


def _receipt_references(dte: DTE) -> str:
    """En la boleta la referencia suele ser un código propio, no un documento.

    El set de certificación pide ``<CodRef>SET</CodRef>`` y
    ``<RazonRef>CASO-1</RazonRef>``, sin tipo ni folio. Con el formato de la
    factura eso se imprimía «Ref:  N°  — CASO-1».
    """
    rows = []
    for ref in dte.references:
        parts = []
        if ref.doc_type not in (None, ""):
            parts.append(_doc_label(ref.doc_type))
        if ref.folio:
            parts.append(f"N° {_esc(str(ref.folio))}")
        if ref.code is not None and not parts:
            parts.append(_esc(str(getattr(ref.code, "value", ref.code))))
        if ref.date:
            parts.append(f"({ref.date.strftime('%d-%m-%Y')})")
        text = " ".join(parts)
        if ref.reason:
            text = f"{text} — {_esc(ref.reason)}" if text else _esc(ref.reason)
        rows.append(f"<div class='ref'>Ref: {text}</div>")
    return f"<div class='refs'>{''.join(rows)}</div>"


def _verification_note(dte: DTE, url: str) -> str:
    """Sitio donde el consumidor consulta su boleta.

    El SII lo exige en la representación impresa de la boleta electrónica; en el
    resto de los documentos no corresponde.
    """
    if not dte.type.is_receipt or not url:
        return ""
    return f'<div class="ley">Consulte su boleta en: <b>{_esc(url)}</b></div>'


def _cession_block(copy: str) -> str:
    if copy != TRANSFERABLE_COPY:
        return ""
    return f"""<div class="cesion">
    <div class="titulo">Acuse de recibo</div>
    <p>{CESSION_DECLARATION}</p>
    <div class="firmas">
      <div>Nombre: ____________________________</div>
      <div>R.U.T.: ____________________________</div>
      <div>Fecha: _____________________________</div>
      <div>Recinto: ___________________________</div>
      <div>Firma: _____________________________</div>
    </div>
  </div>"""


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _total_row(label: str, amount: int, bold: bool = False) -> str:
    cls = " class='fuerte'" if bold else ""
    return f"<tr{cls}><td>{label}</td><td class='r'>{_money(amount)}</td></tr>"


def _doc_label(doc_type: int | str) -> str:
    """Nombre del tipo de documento referenciado.

    Puede no ser un número: el set de certificación exige referenciar el caso
    con el literal «SET», y en el impreso eso debe leerse tal cual —«Ref: SET
    N° 0 — CASO 5038170-1»— y no como «Tipo SET».
    """
    if isinstance(doc_type, str) and not doc_type.isdigit():
        return doc_type
    try:
        return DTEType(int(doc_type)).label
    except ValueError:
        return f"Tipo {doc_type}"


def _resolution_legend(res: ResolutionInfo) -> str:
    if res.number == 0:
        return f"Resolución N° 0 de {res.date.year} — Verifique en www.sii.cl"
    return (
        f"Resolución SII N° {res.number} de {res.date.strftime('%d-%m-%Y')} — "
        "Verifique en www.sii.cl"
    )


def _rut_display(rut: str) -> str:
    """76158145-7 → 76.158.145-7 (formato visual)."""
    body, dv = format_rut(rut).split("-")
    return f"{_thousands(int(body))}-{dv}"


def _thousands(value: int) -> str:
    """Separador de miles con punto, como exige el set de certificación."""
    return f"{value:,}".replace(",", ".")


def _money(value: float) -> str:
    return "$" + _thousands(round(value))


def _money_or_dash(value: float) -> str:
    """Un traslado interno no informa precios: mejor un guion que un '$0'."""
    return _money(value) if value else "—"


def _qty(value: float) -> str:
    if float(value).is_integer():
        return _thousands(int(value))
    integer, _, decimals = f"{value:.6f}".rstrip("0").rstrip(".").partition(".")
    return _thousands(int(integer)) + (f",{decimals}" if decimals else "")


def _pct(value: float) -> str:
    return f"{value:g}".replace(".", ",") + "%"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_STYLE = """
  * { box-sizing: border-box; }
  body { font-family: Arial, Helvetica, sans-serif; font-size: 12px; color: #111;
         margin: 0; padding: 24px; }
  .doc { max-width: 760px; margin: 0 auto 32px; }
  .doc + .doc { page-break-before: always; }
  .top { display: flex; justify-content: space-between; align-items: flex-start;
         gap: 16px; }
  .emisor h1 { font-size: 18px; margin: 0 0 4px; }
  .emisor div { margin: 1px 0; }
  .recuadro { border: 3px solid #c00; color: #c00; border-radius: 6px;
              padding: 10px 16px; text-align: center; min-width: 230px; }
  .recuadro .rut { font-size: 15px; font-weight: bold; }
  .recuadro .tipo { font-size: 14px; font-weight: bold; margin: 6px 0; }
  .recuadro .folio { font-size: 20px; font-weight: bold; }
  .recuadro .sii { font-size: 11px; margin-top: 6px; }
  .cabecera { display: flex; justify-content: space-between; align-items: center;
              margin: 10px 0 16px; }
  .ejemplar { border: 1px solid #333; border-radius: 3px; padding: 2px 10px;
              font-weight: bold; letter-spacing: 1px; }
  .receptor { border: 1px solid #999; border-radius: 4px; padding: 8px 12px;
              margin-bottom: 12px; }
  .traslado { border: 1px solid #999; border-radius: 4px; padding: 8px 12px;
              margin-bottom: 12px; display: grid; gap: 2px 20px;
              grid-template-columns: 1fr 1fr; }
  .traslado .titulo, .cesion .titulo { grid-column: 1 / -1; font-weight: bold;
                                       margin-bottom: 2px; }
  table.det { width: 100%; border-collapse: collapse; margin-bottom: 12px; }
  table.det th { background: #f0f0f0; border: 1px solid #ccc; padding: 5px;
                 text-align: left; }
  table.det td { border: 1px solid #ddd; padding: 5px; }
  .exe { font-size: 10px; color: #666; border: 1px solid #bbb; border-radius: 3px;
         padding: 0 4px; }
  .r { text-align: right; }
  .totales { width: 340px; margin-left: auto; }
  .totales table { width: 100%; border-collapse: collapse; }
  .totales td { padding: 4px 8px; }
  .totales tr.fuerte td { font-weight: bold; font-size: 14px;
                          border-top: 2px solid #333; }
  .refs { margin: 8px 0; font-size: 11px; color: #444; }
  .timbre { text-align: center; margin-top: 22px; }
  .timbre img { max-width: 420px; }
  .timbre .ley { font-size: 11px; margin-top: 4px; color: #333; }
  .cesion { border: 1px solid #999; border-radius: 4px; padding: 8px 12px;
            margin-top: 18px; font-size: 11px; }
  .cesion p { margin: 0 0 10px; }
  .cesion .firmas { display: grid; gap: 8px 20px; grid-template-columns: 1fr 1fr; }
"""

_TEMPLATE = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Representación impresa DTE</title>
<style>{style}</style></head>
<body>{body}</body></html>"""
