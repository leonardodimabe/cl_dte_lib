"""Representación impresa del DTE con timbre electrónico PDF417.

Genera una página HTML lista para imprimir o exportar a PDF (desde el navegador),
con el código de barras **PDF417** que codifica el TED — el "timbre" que el SII
exige en la representación impresa.

El PDF417 codifica los bytes del <TED> tal cual van en el XML, de modo que un
lector pueda reconstruir el DD y verificar el timbre contra la RSAPK del CAF.

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

from .models import DTE
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
def generate_html(dte: DTE, document: etree._Element, resolution: ResolutionInfo) -> str:
    """Construye la representación impresa (HTML auto-contenido)."""
    pdf417_png = generate_pdf417_png(ted_bytes(document))
    barcode_b64 = base64.b64encode(pdf417_png).decode("ascii")

    rows = "".join(
        f"<tr><td>{i}</td><td>{_esc(item.name)}</td><td class='r'>{_num(item.quantity)}</td>"
        f"<td class='r'>{_money(item.unit_price)}</td>"
        f"<td class='r'>{_money(item.amount)}</td></tr>"
        for i, item in enumerate(dte.items, start=1)
    )

    totals = ""
    if not dte.type.is_exempt:
        if dte.exempt_amount:
            totals += _total_row("Exento", dte.exempt_amount)
        totals += _total_row("Neto", dte.net_amount)
        totals += _total_row("IVA (19%)", dte.vat)
    else:
        totals += _total_row("Exento", dte.exempt_amount)
    totals += _total_row("TOTAL", dte.total_amount, bold=True)

    references = ""
    if dte.references:
        ref_rows = "".join(
            f"<div class='ref'>Ref: Tipo {ref.doc_type} N° {ref.folio} "
            f"({ref.date.isoformat()}){' — ' + _esc(ref.reason) if ref.reason else ''}</div>"
            for ref in dte.references
        )
        references = f"<div class='refs'>{ref_rows}</div>"

    return _TEMPLATE.format(
        doc_label=dte.type.label.upper(),
        issuer_rut=_rut_display(dte.issuer.rut.value),
        folio=dte.folio,
        office=_esc(resolution.sii_office),
        issuer_name=_esc(dte.issuer.business_name),
        issuer_activity=_esc(dte.issuer.activity),
        issuer_address=_esc(f"{dte.issuer.address}, {dte.issuer.commune}"),
        date=dte.issue_date.strftime("%d-%m-%Y"),
        receiver_rut=_rut_display(dte.receiver.rut.value),
        receiver_name=_esc(dte.receiver.business_name),
        receiver_activity=_esc(dte.receiver.activity),
        receiver_address=_esc(f"{dte.receiver.address}, {dte.receiver.commune}"),
        rows=rows,
        totals=totals,
        references=references,
        barcode=barcode_b64,
        resolution_legend=_resolution_legend(resolution),
    )


def save_html(html: str, path) -> None:
    from pathlib import Path

    Path(path).write_text(html, encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _total_row(label: str, amount: int, bold: bool = False) -> str:
    cls = " class='fuerte'" if bold else ""
    return f"<tr{cls}><td>{label}</td><td class='r'>{_money(amount)}</td></tr>"


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
    thousands = f"{int(body):,}".replace(",", ".")
    return f"{thousands}-{dv}"


def _money(value: int) -> str:
    return "$" + f"{value:,}".replace(",", ".")


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_TEMPLATE = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>{doc_label} N° {folio}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: Arial, Helvetica, sans-serif; font-size: 12px; color: #111;
          margin: 0; padding: 24px; }}
  .doc {{ max-width: 760px; margin: 0 auto; }}
  .top {{ display: flex; justify-content: space-between; align-items: flex-start;
          gap: 16px; }}
  .emisor h1 {{ font-size: 18px; margin: 0 0 4px; }}
  .emisor div {{ margin: 1px 0; }}
  .recuadro {{ border: 3px solid #c00; color: #c00; border-radius: 6px;
               padding: 10px 16px; text-align: center; min-width: 230px; }}
  .recuadro .rut {{ font-size: 15px; font-weight: bold; }}
  .recuadro .tipo {{ font-size: 14px; font-weight: bold; margin: 6px 0; }}
  .recuadro .folio {{ font-size: 20px; font-weight: bold; }}
  .recuadro .sii {{ font-size: 11px; margin-top: 6px; }}
  .fecha {{ text-align: right; margin: 8px 0 16px; }}
  .receptor {{ border: 1px solid #999; border-radius: 4px; padding: 8px 12px;
               margin-bottom: 14px; }}
  table.det {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; }}
  table.det th {{ background: #f0f0f0; border: 1px solid #ccc; padding: 5px;
                  text-align: left; }}
  table.det td {{ border: 1px solid #ddd; padding: 5px; }}
  .r {{ text-align: right; }}
  .totales {{ width: 280px; margin-left: auto; }}
  .totales table {{ width: 100%; border-collapse: collapse; }}
  .totales td {{ padding: 4px 8px; }}
  .totales tr.fuerte td {{ font-weight: bold; font-size: 14px;
                           border-top: 2px solid #333; }}
  .refs {{ margin: 8px 0; font-size: 11px; color: #444; }}
  .timbre {{ text-align: center; margin-top: 22px; }}
  .timbre img {{ max-width: 420px; }}
  .timbre .ley {{ font-size: 11px; margin-top: 4px; color: #333; }}
</style></head>
<body><div class="doc">
  <div class="top">
    <div class="emisor">
      <h1>{issuer_name}</h1>
      <div>{issuer_activity}</div>
      <div>{issuer_address}</div>
    </div>
    <div class="recuadro">
      <div class="rut">R.U.T. {issuer_rut}</div>
      <div class="tipo">{doc_label}</div>
      <div class="folio">N° {folio}</div>
      <div class="sii">S.I.I. — {office}</div>
    </div>
  </div>
  <div class="fecha">Fecha emisión: {date}</div>
  <div class="receptor">
    <div><b>Señor(es):</b> {receiver_name} &nbsp; <b>R.U.T.:</b> {receiver_rut}</div>
    <div><b>Giro:</b> {receiver_activity}</div>
    <div><b>Dirección:</b> {receiver_address}</div>
  </div>
  <table class="det">
    <thead><tr><th>#</th><th>Detalle</th><th class="r">Cant.</th>
      <th class="r">Precio</th><th class="r">Monto</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  {references}
  <div class="totales"><table>{totals}</table></div>
  <div class="timbre">
    <img src="data:image/png;base64,{barcode}" alt="Timbre Electrónico SII">
    <div class="ley">Timbre Electrónico SII</div>
    <div class="ley">{resolution_legend}</div>
  </div>
</div></body></html>"""
