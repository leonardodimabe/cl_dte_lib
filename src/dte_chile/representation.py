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

#: Nombre del documento en el recuadro, tal como lo lista el «Manual de Muestras
#: Impresas» del SII (1.1.4): «sólo en español, sin traducción, en mayúscula».
#: Ojo con la exenta: es «FACTURA NO AFECTA O EXENTA ELECTRÓNICA».
PRINT_NAMES: dict[int, str] = {
    33: "FACTURA ELECTRÓNICA",
    34: "FACTURA NO AFECTA O EXENTA ELECTRÓNICA",
    39: "BOLETA ELECTRÓNICA",
    41: "BOLETA NO AFECTA O EXENTA ELECTRÓNICA",
    43: "LIQUIDACIÓN FACTURA ELECTRÓNICA",
    46: "FACTURA DE COMPRA ELECTRÓNICA",
    52: "GUÍA DE DESPACHO ELECTRÓNICA",
    56: "NOTA DE DÉBITO ELECTRÓNICA",
    61: "NOTA DE CRÉDITO ELECTRÓNICA",
    110: "FACTURA DE EXPORTACIÓN ELECTRÓNICA",
    111: "NOTA DE DÉBITO DE EXPORTACIÓN ELECTRÓNICA",
    112: "NOTA DE CRÉDITO DE EXPORTACIÓN ELECTRÓNICA",
}

#: Leyenda de destino del ejemplar cedible (manual, 1.4): «CEDIBLE», salvo la
#: guía de despacho, que dice «CEDIBLE CON SU FACTURA».
CEDIBLE_LEGEND = "CEDIBLE"
CEDIBLE_GUIDE_LEGEND = "CEDIBLE CON SU FACTURA"

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

    El manual de muestras impresas del SII (1.4) lista los que lo llevan:
    factura, factura exenta, guía de despacho, factura de compra y liquidación
    factura. Las notas de crédito y débito NO.
    """
    if dte.type.is_dispatch_note:
        return dte.transfer_type is TransferType.SALE
    return dte.type in (
        DTEType.AFFECTED_INVOICE,
        DTEType.EXEMPT_INVOICE,
        DTEType.PURCHASE_INVOICE,
        DTEType.SETTLEMENT_INVOICE,
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
  {
        _header_block(
            dte.issuer.business_name,
            dte.issuer.activity,
            issuer_address,
            dte.issuer.rut.value,
            int(dte.type),
            dte.folio,
            resolution,
        )
    }
  <div class="cabecera">
    <span>Fecha emisión: {dte.issue_date.strftime("%d-%m-%Y")}</span>
  </div>
  {_receiver_block(dte, receiver_address)}
  {_transfer_block(dte)}
  {_items_table(dte)}
  {_references(dte)}
  <div class="totales"><table>{_totals(dte)}</table></div>
  {_cession_block(copy)}
  {
        _stamp_block(
            barcode,
            resolution,
            _verification_note(dte, verification_url),
            _destination_legend(copy, int(dte.type)),
        )
    }
</div>"""


def _header_block(
    name: str, activity: str, address: str, rut: str, doc_type: int, folio: int, resolution
) -> str:
    """Emisor a la izquierda; recuadro a la derecha y, BAJO él, la Unidad del SII.

    Manual de muestras impresas, 1.1.4: el recuadro lleva «sólo» RUT, nombre del
    documento y folio; «Bajo el recuadro se debe indicar la Dirección Regional o
    Unidad del SII a la que pertenece el emisor».
    """
    return f"""<div class="top">
    <div class="emisor">
      <h1>{_esc(name)}</h1>
      <div>{_esc(activity)}</div>
      <div>{address}</div>
    </div>
    <div class="lado">
      <div class="recuadro">
        <div class="rut">R.U.T.: {_rut_display(rut)}</div>
        <div class="tipo">{PRINT_NAMES.get(doc_type, f"DOCUMENTO {doc_type}")}</div>
        <div class="folio">N° {folio}</div>
      </div>
      <div class="sii">S.I.I. - {_esc(resolution.sii_office)}</div>
    </div>
  </div>"""


def _stamp_block(barcode: str, resolution, note: str = "", destination: str = "") -> str:
    """Timbre abajo, a más de 2 cm del borde izquierdo, y la leyenda de destino.

    Manual, 1.5: el timbre mide entre 2x5 y 4x9 cm; bajo él «Timbre Electrónico
    SII» —el SII lo usa para localizarlo— y la resolución con «Verifique
    documento: www.sii.cl». La leyenda de destino va abajo a la derecha.
    """
    legend = f'<div class="destino">{destination}</div>' if destination else ""
    return f"""<div class="pie">
    <div class="timbre">
      <img src="data:image/png;base64,{barcode}" alt="Timbre Electrónico SII">
      <div class="ley">Timbre Electrónico SII</div>
      <div class="ley">{_resolution_legend(resolution)}</div>
      {note}
    </div>
    {legend}
  </div>"""


def _destination_legend(copy: str, doc_type: int) -> str:
    if copy != TRANSFERABLE_COPY:
        return ""  # el tributario va «sin identificación de destino»
    return CEDIBLE_GUIDE_LEGEND if doc_type == int(DTEType.DISPATCH_NOTE) else CEDIBLE_LEGEND


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

    # Manual, 1.4: los documentos que sólo tienen operaciones exentas «deben
    # obviar los totalizadores de Monto Neto e IVA».
    only_exempt = dte.type.is_exempt or (dte.exempt_amount and not dte.net_amount and not dte.vat)
    if only_exempt:
        rows += _total_row("Monto Exento", dte.exempt_amount)
    else:
        rows += _total_row("Monto Neto", dte.net_amount)
        if dte.exempt_amount:
            rows += _total_row("Monto Exento", dte.exempt_amount)
        rows += _total_row("IVA (19%)", dte.vat)
    for retention in dte.retentions:
        label = (
            "Menos: IVA retenido (19%)"
            if retention.code == VAT_RETENTION_TOTAL
            else f"Menos: retención código {retention.code}"
        )
        amount = _money(retention.amount_over(dte.vat))
        rows += f'<tr><td>{label}</td><td class="r">-{amount}</td></tr>'
    return rows + _total_row("Monto Total", dte.total_amount, bold=True)


def _receiver_block(dte: DTE, receiver_address: str) -> str:
    """Datos del receptor.

    La boleta al consumidor final no lo identifica: el XML trae el RUT genérico
    66.666.666-6 y nada más, y la Res. Ex. N°74 no pide receptor en su
    representación. Imprimir «Señor(es):», «Giro:» y «Dirección:» vacíos sólo
    ensucia el papel. Si la boleta identifica a un comprador, se muestra.
    """
    from .receipt import ANONYMOUS_RECEIVER_RUT

    anonymous = dte.receiver.rut.value == ANONYMOUS_RECEIVER_RUT or not dte.receiver.business_name
    if dte.type.is_receipt and anonymous:
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


def _doc_label(doc_type: int | str | None) -> str:
    """Nombre del tipo de documento referenciado.

    Puede no ser un número: el set de certificación exige referenciar el caso
    con el literal «SET», y en el impreso eso debe leerse tal cual —«Ref: SET
    N° 0 — CASO 5038170-1»— y no como «Tipo SET». Y puede faltar: la
    referencia de una boleta suele ser sólo un código propio.
    """
    if doc_type is None:
        return ""
    if isinstance(doc_type, str) and not doc_type.isdigit():
        return doc_type
    code = int(doc_type)
    if code in REFERENCE_NAMES:
        return REFERENCE_NAMES[code]
    if code in PRINT_NAMES:
        # El nombre oficial en palabras («Factura no afecta o exenta
        # electrónica»), no el abreviado del catálogo.
        return PRINT_NAMES[code].capitalize()
    try:
        return DTEType(code).label
    except ValueError:
        return f"Tipo {doc_type}"


#: Tipos de documento que no son DTE pero se referencian (formato DTE, tabla de
#: TpoDocRef). El manual pide el «Tipo de documento (en palabras)».
REFERENCE_NAMES: dict[int, str] = {
    30: "Factura",
    32: "Factura no afecta o exenta",
    35: "Boleta",
    38: "Boleta exenta",
    40: "Liquidación factura",
    45: "Factura de compra",
    50: "Guía de despacho",
    55: "Nota de débito",
    60: "Nota de crédito",
    801: "Orden de compra",
    802: "Nota de pedido",
    803: "Contrato",
    804: "Resolución",
    805: "Proceso ChileCompra",
    806: "Ficha ChileCompra",
    807: "DUS",
    808: "B/L (Conocimiento de embarque)",
    809: "AWB (Air Waybill)",
    810: "MIC/DTA",
    811: "Carta de porte",
    812: "Resolución del SNA",
    813: "Pasaporte",
}


def _resolution_legend(res: ResolutionInfo) -> str:
    """Manual, 1.5: «Res. XX de AAAA» y, en la misma línea separada por un guión,
    «Verifique documento: www.sii.cl»."""
    return f"Res. {res.number} de {res.date.year} - Verifique documento: www.sii.cl"


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
  @page { size: 21.5cm 27.9cm; margin: 1cm; }
  * { box-sizing: border-box; }
  body { font-family: Arial, Helvetica, sans-serif; font-size: 12px; color: #111;
         margin: 0; padding: 24px; }
  .doc { max-width: 760px; margin: 0 auto 32px; }
  .doc + .doc { page-break-before: always; }
  .top { display: flex; justify-content: space-between; align-items: flex-start;
         gap: 16px; }
  .emisor h1 { font-size: 18px; margin: 0 0 4px; }
  .emisor div { margin: 1px 0; }
  .lado { text-align: center; }
  .recuadro { border: 1mm solid #c00; color: #c00;
              padding: 8px 14px; text-align: center; width: 7cm; }
  .recuadro .rut { font-size: 15px; font-weight: bold; }
  .recuadro .tipo { font-size: 14px; font-weight: bold; margin: 6px 0; }
  .recuadro .folio { font-size: 20px; font-weight: bold; }
  .lado .sii { font-size: 12px; font-weight: bold; color: #c00; margin-top: 4px; }
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
  .pie { display: flex; justify-content: space-between; align-items: flex-end;
         margin-top: 18px; }
  .timbre { text-align: center; margin-left: 2.5cm; width: 7.5cm; }
  .timbre img { width: 7.5cm; height: auto; max-height: 3.8cm; }
  .timbre .ley { font-size: 9px; margin-top: 3px; color: #111; }
  .destino { font-size: 16px; font-weight: bold; border: 1px solid #111;
             padding: 4px 10px; }
  .cesion { border: 1px solid #999; border-radius: 4px; padding: 8px 12px;
            margin-top: 18px; font-size: 11px; }
  .cesion p { margin: 0 0 10px; }
  .cesion .firmas { display: grid; gap: 8px 20px; grid-template-columns: 1fr 1fr; }
"""

_TEMPLATE = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Representación impresa DTE</title>
<style>{style}</style></head>
<body>{body}</body></html>"""


# --------------------------------------------------------------------------- #
#  Una página por copia, para cualquier DTE (muestras impresas)
# --------------------------------------------------------------------------- #
@dataclass
class PrintedCopy:
    """Un ejemplar de un documento, en su propia página HTML.

    La aplicación «Upload de Muestras Impresas» del SII exige que «cada archivo
    solo debe contener una página con un documento DTE»: el tributario y el
    cedible van por separado.
    """

    doc_type: int
    folio: int
    copy: str  # TAX_COPY o TRANSFERABLE_COPY
    html: str


def generate_pages(
    xml: bytes | etree._Element,
    resolution: ResolutionInfo,
    *,
    verification_url: str = "",
) -> list[PrintedCopy]:
    """Cada ejemplar de cada DTE de un sobre, uno por página.

    Además de los documentos (<Documento>) imprime la exportación
    (<Exportaciones>) y la liquidación factura (<Liquidacion>), que el SII pide
    en las muestras y que no pasan por el modelo de dominio del DTE.
    """
    from .parser import parse_document

    root = xml if isinstance(xml, etree._Element) else etree.fromstring(xml)
    pages: list[PrintedCopy] = []
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        name = etree.QName(node).localname
        if name == "Documento":
            dte = parse_document(node)
            copies = [TAX_COPY] + ([TRANSFERABLE_COPY] if is_cedible(dte) else [])
            for copy in copies:
                html = generate_html(
                    dte, node, resolution, copy=copy, verification_url=verification_url
                )
                pages.append(PrintedCopy(int(dte.type), dte.folio, copy, html))
        elif name == "Exportaciones":
            pages.append(
                PrintedCopy(
                    int(_xt(node, "Encabezado/IdDoc/TipoDTE")),
                    int(_xt(node, "Encabezado/IdDoc/Folio")),
                    TAX_COPY,
                    _page(_export_body(node, resolution)),
                )
            )
        elif name == "Liquidacion":
            folio = int(_xt(node, "Encabezado/IdDoc/Folio"))
            for copy in (TAX_COPY, TRANSFERABLE_COPY):
                pages.append(
                    PrintedCopy(43, folio, copy, _page(_settlement_body(node, resolution, copy)))
                )
    return pages


def _page(body: str) -> str:
    return _TEMPLATE.format(style=_STYLE, body=body)


def _xt(node: etree._Element, path: str) -> str:
    """Texto de una ruta relativa, sin importar el namespace."""
    found = node.find("/".join("{*}" + part for part in path.split("/")))
    return (found.text or "").strip() if found is not None else ""


def _xall(node: etree._Element, path: str) -> list[etree._Element]:
    return node.findall("/".join("{*}" + part for part in path.split("/")))


def _xml_barcode(node: etree._Element) -> str:
    return base64.b64encode(generate_pdf417_png(ted_bytes(node))).decode("ascii")


def _num(text: str) -> str:
    """Cifra con separador de miles «.» y decimales «,»: 4631.13 → 4.631,13."""
    if not text:
        return ""
    value = float(text)
    if value.is_integer():
        return _thousands(int(value))
    integer, _, decimals = f"{abs(value):.4f}".rstrip("0").partition(".")
    sign = "-" if value < 0 else ""
    return f"{sign}{_thousands(int(integer))},{decimals}"


def _amount(text: str) -> int:
    return int(float(text or 0))


def _code_name(table: dict[str, int], code: str) -> str:
    """Nombre de un código aduanero («517» → «ESPANA (517)»), o el código solo."""
    if not code:
        return ""
    names = [name for name, value in table.items() if str(value) == code]
    return f"{names[0]} ({code})" if names else code


def _issuer_from_xml(node: etree._Element, resolution: ResolutionInfo, doc_type: int) -> str:
    parts = (_xt(node, "Encabezado/Emisor/DirOrigen"), _xt(node, "Encabezado/Emisor/CmnaOrigen"))
    return _header_block(
        _xt(node, "Encabezado/Emisor/RznSoc"),
        _xt(node, "Encabezado/Emisor/GiroEmis"),
        _esc(", ".join(p for p in parts if p)),
        _xt(node, "Encabezado/Emisor/RUTEmisor"),
        doc_type,
        int(_xt(node, "Encabezado/IdDoc/Folio")),
        resolution,
    )


def _receiver_from_xml(node: etree._Element, extra: list[tuple[str, str]] | None = None) -> str:
    r = "Encabezado/Receptor/"
    parts = (_xt(node, r + "DirRecep"), _xt(node, r + "CmnaRecep"), _xt(node, r + "CiudadRecep"))
    name = _esc(_xt(node, r + "RznSocRecep"))
    rut = _rut_display(_xt(node, r + "RUTRecep"))
    rows = [
        f"<div><b>Señor(es):</b> {name} &nbsp; <b>R.U.T.:</b> {rut}</div>",
        f"<div><b>Giro:</b> {_esc(_xt(node, r + 'GiroRecep'))}</div>",
        f"<div><b>Dirección:</b> {_esc(', '.join(p for p in parts if p))}</div>",
    ]
    rows += [f"<div><b>{label}:</b> {_esc(value)}</div>" for label, value in extra or [] if value]
    return f'<div class="receptor">{"".join(rows)}</div>'


def _dmy(date: str) -> str:
    return "-".join(reversed(date.split("-"))) if date else ""


def _xml_references(node: etree._Element) -> str:
    rows = []
    for ref in _xall(node, "Referencia"):
        text = f"Ref: {_doc_label(_xt(ref, 'TpoDocRef'))} N° {_esc(_xt(ref, 'FolioRef'))}"
        if _xt(ref, "FchRef"):
            text += f" ({_dmy(_xt(ref, 'FchRef'))})"
        if _xt(ref, "RazonRef"):
            text += f" — {_esc(_xt(ref, 'RazonRef'))}"
        rows.append(f"<div class='ref'>{text}</div>")
    return f"<div class='refs'>{''.join(rows)}</div>" if rows else ""


def _date_row(node: etree._Element) -> str:
    shown = _dmy(_xt(node, "Encabezado/IdDoc/FchEmis"))
    return f'<div class="cabecera"><span>Fecha emisión: {shown}</span></div>'


def _row(label: str, value: str, bold: bool = False) -> str:
    cls = " class='fuerte'" if bold else ""
    return f"<tr{cls}><td>{label}</td><td class='r'>{value}</td></tr>"


def _export_body(node: etree._Element, resolution: ResolutionInfo) -> str:
    """Factura, nota de débito o de crédito de exportación.

    Manual de muestras impresas, 1.4: cuando hay transporte de mercaderías,
    «Puerto de Embarque, Puerto de Desembarque, Total de Bultos, RUT y País
    receptor y Tipo de Moneda» son obligatorios en el impreso.
    """
    from .customs_codes import (
        COUNTRIES,
        PACKAGE_TYPES,
        PAYMENT_MODES,
        PORTS,
        SALE_CLAUSES,
        SALE_MODES,
        TRANSPORT_ROUTES,
    )

    doc_type = int(_xt(node, "Encabezado/IdDoc/TipoDTE"))
    a = "Encabezado/Transporte/Aduana/"
    currency = _esc(_xt(node, "Encabezado/Totales/TpoMoneda"))
    receiver = _receiver_from_xml(
        node,
        [
            ("País receptor", _code_name(COUNTRIES, _xt(node, a + "CodPaisRecep"))),
            ("Tipo de moneda", _xt(node, "Encabezado/Totales/TpoMoneda")),
        ],
    )

    customs_rows = [
        ("Forma de pago", _code_name(PAYMENT_MODES, _xt(node, "Encabezado/IdDoc/FmaPagExp"))),
        ("Modalidad de venta", _code_name(SALE_MODES, _xt(node, a + "CodModVenta"))),
        ("Cláusula de venta", _code_name(SALE_CLAUSES, _xt(node, a + "CodClauVenta"))),
        ("Total cláusula", _num(_xt(node, a + "TotClauVenta"))),
        ("Vía de transporte", _code_name(TRANSPORT_ROUTES, _xt(node, a + "CodViaTransp"))),
        ("Puerto de embarque", _code_name(PORTS, _xt(node, a + "CodPtoEmbarque"))),
        ("Puerto de desembarque", _code_name(PORTS, _xt(node, a + "CodPtoDesemb"))),
        ("Total de bultos", _num(_xt(node, a + "TotBultos"))),
        ("Flete", _num(_xt(node, a + "MntFlete"))),
        ("Seguro", _num(_xt(node, a + "MntSeguro"))),
        ("País de destino", _code_name(COUNTRIES, _xt(node, a + "CodPaisDestin"))),
    ]
    for package in _xall(node, a + "TipoBultos"):
        parts = [
            _code_name(PACKAGE_TYPES, _xt(package, "CodTpoBultos")),
            f"{_num(_xt(package, 'CantBultos'))} bulto(s)",
            _xt(package, "Marcas"),
            f"contenedor {_xt(package, 'IdContainer')}" if _xt(package, "IdContainer") else "",
            f"sello {_xt(package, 'Sello')}" if _xt(package, "Sello") else "",
        ]
        customs_rows.append(("Bultos", " · ".join(p for p in parts if p)))
    customs = "".join(
        f"<div><b>{label}:</b> {_esc(value)}</div>" for label, value in customs_rows if value
    )
    customs_block = (
        f'<div class="traslado"><div class="titulo">Exportación</div>{customs}</div>'
        if customs
        else ""
    )

    rows = []
    for position, item in enumerate(_xall(node, "Detalle"), start=1):
        adjust = []
        if _xt(item, "DescuentoMonto"):
            pct = f"{_num(_xt(item, 'DescuentoPct'))}% " if _xt(item, "DescuentoPct") else ""
            adjust.append(f"Desc. {pct}{_num(_xt(item, 'DescuentoMonto'))}")
        if _xt(item, "RecargoMonto"):
            pct = f"{_num(_xt(item, 'RecargoPct'))}% " if _xt(item, "RecargoPct") else ""
            adjust.append(f"Rec. {pct}{_num(_xt(item, 'RecargoMonto'))}")
        rows.append(
            f"<tr><td>{position}</td><td>{_esc(_xt(item, 'NmbItem'))}</td>"
            f"<td>{_esc(_xt(item, 'UnmdItem'))}</td>"
            f"<td class='r'>{_num(_xt(item, 'QtyItem'))}</td>"
            f"<td class='r'>{_num(_xt(item, 'PrcItem'))}</td>"
            f"<td class='r'>{' / '.join(adjust)}</td>"
            f"<td class='r'>{_num(_xt(item, 'MontoItem'))}</td></tr>"
        )
    head = (
        "<th>#</th><th>Detalle</th><th>Un.</th><th class='r'>Cant.</th>"
        f"<th class='r'>Precio ({currency})</th><th class='r'>Desc./Recargo</th>"
        f"<th class='r'>Monto ({currency})</th>"
    )
    items = (
        f'<table class="det"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )

    totals = ""
    for dr in _xall(node, "DscRcgGlobal"):
        kind = "Descuento" if _xt(dr, "TpoMov") == "D" else "Recargo"
        value = _num(_xt(dr, "ValorDR"))
        value = f"{value}%" if _xt(dr, "TpoValor") == "%" else value
        glosa = f" — {_esc(_xt(dr, 'GlosaDR'))}" if _xt(dr, "GlosaDR") else ""
        totals += _row(f"{kind} global{glosa}", value)
    t = "Encabezado/Totales/"
    totals += _row(f"Monto Exento ({currency})", _num(_xt(node, t + "MntExe")))
    totals += _row(f"Monto Total ({currency})", _num(_xt(node, t + "MntTotal")), bold=True)
    o = "Encabezado/OtraMoneda/"
    if _xt(node, o + "TpoMoneda"):
        other = _esc(_xt(node, o + "TpoMoneda"))
        totals += _row(f"Tipo de cambio ({other})", _num(_xt(node, o + "TpoCambio")))
        # Un total en otra moneda en cero no es un dato, es un campo sin llenar.
        if _amount(_xt(node, o + "MntTotOtrMnda")):
            totals += _row(f"Monto Total ({other})", _num(_xt(node, o + "MntTotOtrMnda")))

    return f"""<div class="doc">
  {_issuer_from_xml(node, resolution, doc_type)}
  {_date_row(node)}
  {receiver}
  {customs_block}
  {items}
  {_xml_references(node)}
  <div class="totales"><table>{totals}</table></div>
  {_stamp_block(_xml_barcode(node), resolution)}
</div>"""


def _settlement_body(node: etree._Element, resolution: ResolutionInfo, copy: str) -> str:
    """Liquidación factura: los documentos liquidados y las comisiones."""
    rows = []
    for position, line in enumerate(_xall(node, "Detalle"), start=1):
        kind = "Exento" if _xt(line, "IndExe") == "1" else "Afecto"
        rows.append(
            f"<tr><td>{position}</td><td>{_esc(_doc_label(_xt(line, 'TpoDocLiq')))}</td>"
            f"<td>{_esc(_xt(line, 'NmbItem'))}</td>"
            f"<td class='r'>{_num(_xt(line, 'QtyItem'))}</td><td>{kind}</td>"
            f"<td class='r'>{_money(_amount(_xt(line, 'MontoItem')))}</td></tr>"
        )
    head = (
        "<th>#</th><th>Documento liquidado</th><th>Detalle</th><th class='r'>Cant.</th>"
        "<th>Afecto/Exento</th><th class='r'>Monto</th>"
    )
    items = (
        f'<table class="det"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    )

    commission_rows = []
    for c in _xall(node, "Comisiones"):
        kind = "Comisión" if _xt(c, "TipoMovim") == "C" else "Otros cargos"
        commission_rows.append(
            f"<tr><td>{kind}</td><td>{_esc(_xt(c, 'Glosa'))}</td>"
            f"<td class='r'>{_money(_amount(_xt(c, 'ValComNeto')))}</td>"
            f"<td class='r'>{_money(_amount(_xt(c, 'ValComExe')))}</td>"
            f"<td class='r'>{_money(_amount(_xt(c, 'ValComIVA')))}</td></tr>"
        )
    commissions = ""
    if commission_rows:
        commissions = (
            '<table class="det"><thead><tr><th>Tipo</th><th>Comisiones y otros cargos</th>'
            "<th class='r'>Neto</th><th class='r'>Exento</th><th class='r'>IVA</th></tr>"
            f"</thead><tbody>{''.join(commission_rows)}</tbody></table>"
        )

    t = "Encabezado/Totales/"
    totals = _total_row("Monto Neto", _amount(_xt(node, t + "MntNeto")))
    if _xt(node, t + "MntExe"):
        totals += _total_row("Monto Exento", _amount(_xt(node, t + "MntExe")))
    rate = _num(_xt(node, t + "TasaIVA") or "19")
    totals += _total_row(f"IVA ({rate}%)", _amount(_xt(node, t + "IVA")))
    c = "Encabezado/Totales/Comisiones/"
    if _xt(node, c + "ValComNeto"):
        net = _money(_amount(_xt(node, c + "ValComNeto")))
        totals += _row("Menos: comisiones y otros cargos (neto)", f"-{net}")
        if _amount(_xt(node, c + "ValComExe")):
            exempt = _money(_amount(_xt(node, c + "ValComExe")))
            totals += _row("Menos: comisiones y otros cargos (exento)", f"-{exempt}")
        vat = _money(_amount(_xt(node, c + "ValComIVA")))
        totals += _row("Menos: IVA de comisiones", f"-{vat}")
    totals += _total_row("Monto Total", _amount(_xt(node, t + "MntTotal")), bold=True)

    return f"""<div class="doc">
  {_issuer_from_xml(node, resolution, 43)}
  {_date_row(node)}
  {_receiver_from_xml(node)}
  {items}
  {commissions}
  {_xml_references(node)}
  <div class="totales"><table>{totals}</table></div>
  {_cession_block(copy)}
  {_stamp_block(_xml_barcode(node), resolution, "", _destination_legend(copy, 43))}
</div>"""
