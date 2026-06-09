"""Parseo del archivo CAF (Código de Autorización de Folios).

El CAF es un XML que el SII entrega por cada tipo de documento. Contiene:
  - El rango de folios autorizados (RNG/D..H).
  - El bloque <CAF> que debe incrustarse tal cual dentro del TED.
  - La llave privada RSA (<RSASK>) con la que se firma el timbre (TED/FRMT).

⚠️ El archivo CAF es secreto: contiene la llave privada de timbraje. No subir a git.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lxml import etree


@dataclass
class CAF:
    doc_type: int
    folio_from: int
    folio_to: int
    issuer_rut: str
    caf_element: etree._Element  # nodo <CAF> a incrustar en el TED
    rsa_private_key_pem: str  # contenido de <RSASK>

    def contains(self, folio: int) -> bool:
        return self.folio_from <= folio <= self.folio_to

    @property
    def caf_xml_bytes(self) -> bytes:
        """Serializa el nodo <CAF> tal como debe ir dentro del TED."""
        return etree.tostring(self.caf_element)


def load_caf(path: str | Path) -> CAF:
    """Carga y parsea un archivo CAF desde disco."""
    return load_caf_bytes(Path(path).read_bytes())


def load_caf_bytes(data: bytes) -> CAF:
    """Parsea un CAF desde bytes (sin tocar disco; p.ej. CAF cifrado en BD)."""
    root = etree.fromstring(data)  # <AUTORIZACION>

    caf_node = root.find("CAF")
    if caf_node is None:
        raise ValueError("Archivo CAF inválido: falta nodo <CAF>.")

    da = caf_node.find("DA")
    doc_type = int(da.findtext("TD"))
    folio_from = int(da.find("RNG/D").text)
    folio_to = int(da.find("RNG/H").text)
    issuer_rut = da.findtext("RE")

    rsask = root.findtext("RSASK")
    if not rsask:
        raise ValueError("Archivo CAF inválido: falta llave privada <RSASK>.")

    return CAF(
        doc_type=doc_type,
        folio_from=folio_from,
        folio_to=folio_to,
        issuer_rut=issuer_rut,
        caf_element=caf_node,
        rsa_private_key_pem=rsask.strip(),
    )
