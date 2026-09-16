"""Liquidación Factura Electrónica (tipo 43).

La usa el mandatario que vende por cuenta de un mandante: liquida lo recaudado
en el período y descuenta su comisión. No comparte estructura con el resto de
los DTE — el XSD le da una raíz propia, ``<Liquidacion>`` en vez de
``<Documento>``, con tres diferencias que importan:

- Cada línea declara ``TpoDocLiq``: **qué tipo de documento se está liquidando**
  (facturas, boletas, notas de crédito, otra liquidación...).
- Los montos usan ``ValorType``, no ``MontoType``: **admiten negativos**. Una
  nota de crédito o una liquidación anterior entran restando.
- Hay un bloque ``<Comisiones>`` aparte del detalle, y su valor se **resta** del
  total: lo que se liquida al mandante es lo recaudado menos la comisión.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from lxml import etree

from .caf import CAF
from .document_types import DTEType
from .models import VAT_RATE, Issuer, Receiver, Reference
from .ted import build_ted
from .text import (
    DocumentDataError,
    FieldProblem,
    check,
    issuer_problems,
    receiver_problems,
    reference_problems,
)

# TipoMovim del bloque de comisiones.
COMMISSION = "C"
OTHER_CHARGE = "O"

# El XSD admite hasta 20 bloques de comisión.
MAX_COMMISSIONS = 20


@dataclass
class SettlementLine:
    """Una línea del detalle: qué se liquida y por cuánto."""

    # TpoDocLiq (máx. 3 caracteres): el código del documento que se liquida —
    # "30" factura, "33" factura electrónica, "35" boleta, "60" nota de crédito,
    # "43" liquidación factura…—, y **"99" para un anticipo u otra transacción
    # que no sea un documento**. Lo fija el formato del SII: «Se debe registrar
    # código de documento válido, (electrónico o manual) o 99 en caso de
    # anticipo u otras transacciones».
    liquidated_type: str
    name: str  # NmbItem
    amount: int  # MontoItem; puede ser negativo
    quantity: float | None = None  # QtyItem
    exempt: bool = False  # IndExe
    description: str = ""

    def __post_init__(self) -> None:
        if not self.liquidated_type or len(str(self.liquidated_type)) > 3:
            raise ValueError(f"TpoDocLiq inválido: {self.liquidated_type!r} (1 a 3 caracteres).")


@dataclass
class Commission:
    """Comisión u otro cargo del mandatario (<Comisiones>)."""

    description: str  # Glosa
    net_amount: int = 0  # ValComNeto; puede ser negativo
    exempt_amount: int = 0  # ValComExe
    vat_amount: int | None = None  # ValComIVA; si es None se calcula sobre el neto
    kind: str = COMMISSION  # TipoMovim: C comisión / O otros cargos
    rate: float | None = None  # TasaComision

    def __post_init__(self) -> None:
        if self.kind not in (COMMISSION, OTHER_CHARGE):
            raise ValueError("TipoMovim debe ser 'C' (comisión) u 'O' (otros cargos).")

    @property
    def vat(self) -> int:
        if self.vat_amount is not None:
            return self.vat_amount
        return round(self.net_amount * VAT_RATE / 100)


@dataclass
class Settlement:
    """Liquidación factura completa."""

    folio: int
    issue_date: _dt.date
    issuer: Issuer  # el mandatario, que emite
    receiver: Receiver  # el mandante, por cuya cuenta se vendió
    lines: list[SettlementLine] = field(default_factory=list)
    commissions: list[Commission] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)

    type: DTEType = DTEType.SETTLEMENT_INVOICE

    # El TED se arma con ``items[0].name``; el detalle de la liquidación cumple
    # el mismo rol que el de un DTE normal.
    @property
    def items(self) -> list[SettlementLine]:
        return self.lines

    # --- Totales ---
    @property
    def net_amount(self) -> int:
        return sum(ln.amount for ln in self.lines if not ln.exempt)

    @property
    def exempt_amount(self) -> int:
        return sum(ln.amount for ln in self.lines if ln.exempt)

    @property
    def vat(self) -> int:
        return round(self.net_amount * VAT_RATE / 100)

    @property
    def commission_net(self) -> int:
        return sum(c.net_amount for c in self.commissions)

    @property
    def commission_exempt(self) -> int:
        return sum(c.exempt_amount for c in self.commissions)

    @property
    def commission_vat(self) -> int:
        """IVA total de las comisiones: la tasa sobre el neto TOTAL.

        Lo dice la validación 38 del SII, literal: «Para las liquidaciones
        factura, el IVA de las Comisiones debe ser igual a la tasa del IVA
        (19%) por el Valor Neto de las Comisiones».

        Se redondea una sola vez sobre el total y no se suman los redondeos de
        cada comisión, porque no es lo mismo: con 630 y -2197, línea a línea da
        120 y -417 —correctos por separado— que suman -297, mientras la tasa
        sobre el neto total (-1567) da -297,73, o sea -298. El SII lo reportó
        así: «Reparo en Calculo de [ValComIVA] T:[43]-F:[4]». Y el error crece
        con el número de comisiones.

        Si el llamador fija el IVA de alguna comisión a mano, manda él: estará
        declarando algo que no se deduce de la tasa.
        """
        if any(c.vat_amount is not None for c in self.commissions):
            return sum(c.vat for c in self.commissions)
        return round(self.commission_net * VAT_RATE / 100)

    @property
    def total_amount(self) -> int:
        # Lo recaudado menos lo que se queda el mandatario.
        return (
            self.net_amount
            + self.exempt_amount
            + self.vat
            - self.commission_net
            - self.commission_exempt
            - self.commission_vat
        )

    def validate(self) -> None:
        if self.folio <= 0:
            raise ValueError("Folio inválido.")
        self.validate_content()

    def validate_content(self) -> None:
        if not self.lines:
            raise ValueError("La liquidación debe tener al menos una línea de detalle.")
        if len(self.commissions) > MAX_COMMISSIONS:
            raise ValueError(
                f"El XSD admite {MAX_COMMISSIONS} bloques de comisión y hay "
                f"{len(self.commissions)}."
            )
        self.validate_text()

    def text_problems(self) -> list[FieldProblem]:
        """Revisa el texto contra lo que acepta el SII (caracteres y largos)."""
        problems = issuer_problems(self.issuer) + receiver_problems(self.receiver)
        for position, line in enumerate(self.lines, start=1):
            where = f"Detalle[{position}]"
            problems += check(f"{where}.TpoDocLiq", str(line.liquidated_type), "TpoDocLiq")
            problems += check(f"{where}.NmbItem", line.name, "NmbItem")
            problems += check(f"{where}.DscItem", line.description, "DscItem")
        for position, commission in enumerate(self.commissions, start=1):
            problems += check(f"Comisiones[{position}].Glosa", commission.description, "Glosa")
        return problems + reference_problems(self.references)

    def validate_text(self) -> None:
        problems = self.text_problems()
        if problems:
            raise DocumentDataError(problems)


def build_settlement(settlement: Settlement, caf: CAF, timestamp: _dt.datetime) -> etree._Element:
    """Devuelve el nodo ``<Liquidacion>`` listo para firmar."""
    settlement.validate()

    root = etree.Element("Liquidacion", ID=f"F{settlement.folio}T{int(settlement.type)}")
    _header(root, settlement)
    _lines(root, settlement)
    _references(root, settlement)
    _commissions(root, settlement)

    root.append(build_ted(settlement, caf, timestamp))
    etree.SubElement(root, "TmstFirma").text = timestamp.replace(microsecond=0).isoformat()
    return root


# --------------------------------------------------------------------------- #
#  Secciones
# --------------------------------------------------------------------------- #
def _header(root: etree._Element, s: Settlement) -> None:
    header = etree.SubElement(root, "Encabezado")

    id_doc = etree.SubElement(header, "IdDoc")
    _t(id_doc, "TipoDTE", str(int(s.type)))
    _t(id_doc, "Folio", str(s.folio))
    _t(id_doc, "FchEmis", s.issue_date.isoformat())

    issuer = etree.SubElement(header, "Emisor")
    _t(issuer, "RUTEmisor", s.issuer.rut.value)
    _t(issuer, "RznSoc", s.issuer.business_name)
    _t(issuer, "GiroEmis", s.issuer.activity[:80])
    _t(issuer, "Acteco", str(s.issuer.economic_activity))
    if s.issuer.branch_code is not None:
        _t(issuer, "CdgSIISucur", str(s.issuer.branch_code))
    _t(issuer, "DirOrigen", s.issuer.address[:70])
    _t(issuer, "CmnaOrigen", s.issuer.commune)
    if s.issuer.city:
        _t(issuer, "CiudadOrigen", s.issuer.city)

    receiver = etree.SubElement(header, "Receptor")
    _t(receiver, "RUTRecep", s.receiver.rut.value)
    _t(receiver, "RznSocRecep", s.receiver.business_name[:100])
    _t(receiver, "GiroRecep", s.receiver.activity[:40])
    _t(receiver, "DirRecep", s.receiver.address[:70])
    _t(receiver, "CmnaRecep", s.receiver.commune)
    if s.receiver.city:
        _t(receiver, "CiudadRecep", s.receiver.city)

    # Orden del XSD: MntNeto?, MntExe?, TasaIVA?, IVA?, ..., Comisiones?, MntTotal.
    totals = etree.SubElement(header, "Totales")
    if s.net_amount:
        _t(totals, "MntNeto", str(s.net_amount))
    if s.exempt_amount:
        _t(totals, "MntExe", str(s.exempt_amount))
    if s.net_amount:
        _t(totals, "TasaIVA", str(VAT_RATE))
        _t(totals, "IVA", str(s.vat))
    if s.commissions:
        node = etree.SubElement(totals, "Comisiones")
        _t(node, "ValComNeto", str(s.commission_net))
        _t(node, "ValComExe", str(s.commission_exempt))
        _t(node, "ValComIVA", str(s.commission_vat))
    _t(totals, "MntTotal", str(s.total_amount))


def _lines(root: etree._Element, s: Settlement) -> None:
    for position, line in enumerate(s.lines, start=1):
        detail = etree.SubElement(root, "Detalle")
        _t(detail, "NroLinDet", str(position))
        # TpoDocLiq va antes del nombre: identifica qué se liquida en esa línea.
        _t(detail, "TpoDocLiq", str(line.liquidated_type))
        if line.exempt:
            _t(detail, "IndExe", "1")
        _t(detail, "NmbItem", line.name[:80])
        if line.description:
            _t(detail, "DscItem", line.description[:1000])
        if line.quantity:
            _t(detail, "QtyItem", _num(line.quantity))
        _t(detail, "MontoItem", str(line.amount))


def _references(root: etree._Element, s: Settlement) -> None:
    for position, reference in enumerate(s.references, start=1):
        node = etree.SubElement(root, "Referencia")
        _t(node, "NroLinRef", str(position))
        _t(node, "TpoDocRef", str(reference.doc_type))
        _t(node, "FolioRef", str(reference.folio))
        if reference.date is not None:
            _t(node, "FchRef", reference.date.isoformat())
        if reference.code is not None:
            _t(node, "CodRef", str(int(reference.code)))
        if reference.reason:
            _t(node, "RazonRef", reference.reason[:90])


def _commissions(root: etree._Element, s: Settlement) -> None:
    for position, commission in enumerate(s.commissions, start=1):
        node = etree.SubElement(root, "Comisiones")
        _t(node, "NroLinCom", str(position))
        _t(node, "TipoMovim", commission.kind)
        _t(node, "Glosa", commission.description[:60])
        if commission.rate is not None:
            _t(node, "TasaComision", _num(commission.rate))
        _t(node, "ValComNeto", str(commission.net_amount))
        _t(node, "ValComExe", str(commission.exempt_amount))
        _t(node, "ValComIVA", str(commission.vat))


def _t(parent: etree._Element, tag: str, value: str) -> None:
    etree.SubElement(parent, tag).text = value


def _num(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")
