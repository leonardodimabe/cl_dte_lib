"""Lectura de un DTE firmado de vuelta al dominio.

El camino inverso de :mod:`xml_builder`. Sirve para reimprimir un documento a
partir del XML que ya se emitió (el servicio no guarda los DTE: el emisor
conserva el sobre) y para inspeccionar documentos recibidos.

Se trabaja por nombre local de etiqueta, así funciona tanto con el ``<DTE>``
suelto como con el ``<EnvioDTE>`` completo, esté o no en el namespace SiiDte.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from lxml import etree

from .document_types import (
    DispatchType,
    DTEType,
    ReferenceCode,
    ServiceIndicator,
    TransferType,
)
from .models import (
    DTE,
    Driver,
    GlobalDiscount,
    Issuer,
    Item,
    Receiver,
    Reference,
    Retention,
    Transport,
)

# IndExeDR → ámbito del descuento/recargo global.
_SCOPE_BY_INDICATOR = {None: "afecto", "1": "exento", "2": "no_afecto"}


class ParseError(ValueError):
    """El XML no tiene la forma de un DTE que se pueda reconstruir."""


@dataclass
class ParsedDocument:
    """Un DTE del sobre: el dominio reconstruido y su nodo original.

    El nodo se conserva porque el TED debe salir del XML tal cual —es lo que
    firma el CAF—; reconstruirlo daría un timbre distinto.
    """

    dte: DTE
    element: etree._Element


def parse_documents(xml: bytes | etree._Element) -> list[ParsedDocument]:
    """Devuelve todos los documentos de un EnvioDTE (o el único de un DTE suelto)."""
    root = xml if isinstance(xml, etree._Element) else etree.fromstring(xml)
    documents = [node for node in root.iter() if _localname(node) == "Documento"]
    if not documents:
        raise ParseError("El XML no contiene ningún <Documento>.")
    return [ParsedDocument(dte=parse_document(node), element=node) for node in documents]


def parse_document(document: etree._Element) -> DTE:
    """Reconstruye el DTE de dominio a partir de un nodo ``<Documento>``.

    Verifica que los totales que se recalculan desde el detalle coincidan con
    los que trae el XML: si no calzan, el documento se leyó mal y es preferible
    fallar antes que imprimir cifras que no son las timbradas.
    """
    header = _child(document, "Encabezado", required=True)
    id_doc = _child(header, "IdDoc", required=True)
    totals = _child(header, "Totales", required=True)

    doc_type = DTEType(int(_text(id_doc, "TipoDTE")))
    # En la boleta los precios van con IVA incluido salvo que se declare
    # IndMntNeto=2; sin recuperar ese matiz, los totales al releer no cuadran.
    prices_include_vat = doc_type.is_receipt and _text(id_doc, "IndMntNeto") is None

    dte = DTE(
        type=doc_type,
        folio=int(_text(id_doc, "Folio")),
        issue_date=_date(_text(id_doc, "FchEmis")),
        issuer=_issuer(_child(header, "Emisor", required=True)),
        receiver=_receiver(_child(header, "Receptor", required=True)),
        items=[_item(node) for node in _children(document, "Detalle")],
        references=[_reference(node) for node in _children(document, "Referencia")],
        global_discounts=[_global_discount(node) for node in _children(document, "DscRcgGlobal")],
        dispatch_type=_enum(DispatchType, _text(id_doc, "TipoDespacho")),
        transfer_type=_enum(TransferType, _text(id_doc, "IndTraslado")),
        transport=_transport(_child(header, "Transporte")),
        retentions=[_retention(node) for node in _children(totals, "ImptoReten")],
        prices_include_vat=prices_include_vat,
        service_indicator=_enum(ServiceIndicator, _text(id_doc, "IndServicio")),
    )

    declared = _text(totals, "MntTotal")
    if declared is not None and int(declared) != dte.total_amount:
        raise ParseError(
            f"Los totales no cuadran al releer el documento {int(dte.type)} folio "
            f"{dte.folio}: el XML declara {declared} y el detalle suma {dte.total_amount}."
        )
    return dte


# --------------------------------------------------------------------------- #
#  Bloques
# --------------------------------------------------------------------------- #
def _issuer(node: etree._Element) -> Issuer:
    # La boleta nombra distinto los mismos campos: RznSocEmisor/GiroEmisor.
    return Issuer(
        rut=_text(node, "RUTEmisor") or "",
        business_name=_text(node, "RznSoc") or _text(node, "RznSocEmisor") or "",
        activity=_text(node, "GiroEmis") or _text(node, "GiroEmisor") or "",
        economic_activity=int(_text(node, "Acteco") or 0),
        address=_text(node, "DirOrigen") or "",
        commune=_text(node, "CmnaOrigen") or "",
        city=_text(node, "CiudadOrigen") or "",
        branch_name=_text(node, "Sucursal") or "",
        branch_code=int(_text(node, "CdgSIISucur")) if _text(node, "CdgSIISucur") else None,
    )


def _receiver(node: etree._Element) -> Receiver:
    return Receiver(
        rut=_text(node, "RUTRecep") or "",
        business_name=_text(node, "RznSocRecep") or "",
        activity=_text(node, "GiroRecep") or "",
        address=_text(node, "DirRecep") or "",
        commune=_text(node, "CmnaRecep") or "",
        city=_text(node, "CiudadRecep") or "",
    )


def _transport(node: etree._Element | None) -> Transport | None:
    if node is None:
        return None
    chofer = _child(node, "Chofer")
    driver = None
    if chofer is not None:
        driver = Driver(
            rut=_text(chofer, "RUTChofer") or "",
            name=_text(chofer, "NombreChofer") or "",
        )
    return Transport(
        plate=_text(node, "Patente") or "",
        trailer_plate=_text(node, "PatenteCarro") or "",
        carrier_rut=_text(node, "RUTTrans"),
        driver=driver,
        dest_address=_text(node, "DirDest") or "",
        dest_commune=_text(node, "CmnaDest") or "",
        dest_city=_text(node, "CiudadDest") or "",
        departure_date=_optional_date(_text(node, "FchSalida")),
        departure_time=_optional_time(_text(node, "HraSalida")),
        arrival_date=_optional_date(_text(node, "FchLlegada")),
    )


def _item(node: etree._Element) -> Item:
    # MontoItem es la fuente de verdad del monto; el descuento se deriva de él
    # para que la línea vuelva a sumar exactamente lo timbrado.
    quantity = float(_text(node, "QtyItem") or 0)
    unit_price = int(float(_text(node, "PrcItem") or 0))
    discount_amount = _text(node, "DescuentoMonto")
    return Item(
        name=_text(node, "NmbItem") or "",
        quantity=quantity,
        unit_price=unit_price,
        exempt=_text(node, "IndExe") == "1",
        description=_text(node, "DscItem") or "",
        unit=_text(node, "UnmdItem") or "",
        discount_pct=float(_text(node, "DescuentoPct") or 0),
        discount_amount=int(discount_amount) if discount_amount is not None else None,
    )


def _reference(node: etree._Element) -> Reference:
    code = _text(node, "CodRef")
    # En la boleta TpoDocRef es alfanumérico: el set de certificación usa "SET".
    raw_type = _text(node, "TpoDocRef") or ""
    return Reference(
        doc_type=int(raw_type) if raw_type.isdigit() else raw_type,
        folio=_text(node, "FolioRef") or "",
        date=_optional_date(_text(node, "FchRef")),
        code=ReferenceCode(int(code)) if code else None,
        reason=_text(node, "RazonRef") or "",
    )


def _retention(node: etree._Element) -> Retention:
    """Lee un <ImptoReten>. El monto viene explícito, así que no se deriva del IVA."""
    rate = _text(node, "TasaImp")
    return Retention(
        code=int(_text(node, "TipoImp") or 0),
        amount=int(_text(node, "MontoImp") or 0),
        rate=float(rate) if rate else None,
    )


def _global_discount(node: etree._Element) -> GlobalDiscount:
    return GlobalDiscount(
        value=float(_text(node, "ValorDR") or 0),
        kind=_text(node, "TpoMov") or "D",
        value_type=_text(node, "TpoValor") or "%",
        reason=_text(node, "GlosaDR") or "",
        scope=_SCOPE_BY_INDICATOR.get(_text(node, "IndExeDR"), "afecto"),
    )


# --------------------------------------------------------------------------- #
#  Helpers de XML
# --------------------------------------------------------------------------- #
def _localname(node) -> str:
    if not isinstance(node.tag, str):  # comentarios, PIs
        return ""
    return etree.QName(node).localname


def _children(parent: etree._Element, name: str) -> list[etree._Element]:
    return [node for node in parent if _localname(node) == name]


def _child(parent: etree._Element, name: str, *, required: bool = False):
    for node in parent:
        if _localname(node) == name:
            return node
    if required:
        raise ParseError(f"Falta el bloque <{name}> en el documento.")
    return None


def _text(parent: etree._Element | None, name: str) -> str | None:
    if parent is None:
        return None
    node = _child(parent, name)
    if node is None or node.text is None:
        return None
    return node.text.strip()


def _date(value: str | None) -> _dt.date:
    if not value:
        raise ParseError("Falta una fecha obligatoria en el documento.")
    return _dt.date.fromisoformat(value)


def _optional_date(value: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(value) if value else None


def _optional_time(value: str | None) -> _dt.time | None:
    return _dt.time.fromisoformat(value) if value else None


def _enum(enum_class, value: str | None):
    return enum_class(int(value)) if value else None
