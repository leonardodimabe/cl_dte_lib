"""Parseo del archivo CAF (Código de Autorización de Folios).

El CAF es un XML que el SII entrega por cada tipo de documento. Contiene:
  - El rango de folios autorizados (RNG/D..H).
  - La fecha de autorización (FA), de la que depende su vigencia.
  - La llave pública del timbre (RSAPK) y el identificador de llave (IDK).
  - El bloque <CAF> que debe incrustarse tal cual dentro del TED.
  - La llave privada RSA (<RSASK>) con la que se firma el timbre (TED/FRMT).

⚠️ El archivo CAF es secreto: contiene la llave privada de timbraje. No subir a git.
"""

from __future__ import annotations

import base64
import binascii
import calendar
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree

#: IDK de los CAF del ambiente de certificación (Maullín). Los de producción
#: traen otro.
CERTIFICATION_IDK = 100

#: Documentos cuyo CAF vence. La Res. Ex. SII N° 58 de 2017 fija «una validez
#: de seis meses contados desde la fecha de su autorización» para los CAF
#: «asociados a los documentos tributarios electrónicos que otorgan derecho al
#: crédito fiscal», y el SII «rechazará al momento de su recepción» los
#: documentos con folios de un CAF vencido. Boletas, guías, facturas exentas y
#: de exportación no dan crédito fiscal.
EXPIRING_TYPES = frozenset({33, 43, 46, 56, 61})
VALIDITY_MONTHS = 6


@dataclass
class CAF:
    doc_type: int
    folio_from: int
    folio_to: int
    issuer_rut: str
    caf_element: etree._Element  # nodo <CAF> a incrustar en el TED
    rsa_private_key_pem: str  # contenido de <RSASK>
    authorized_on: dt.date | None = None  # <FA>
    key_id: int | None = None  # <IDK>

    def contains(self, folio: int) -> bool:
        return self.folio_from <= folio <= self.folio_to

    @property
    def expires_on(self) -> dt.date | None:
        """Último día en que se puede emitir con este CAF; None si no vence.

        Se cuenta hacia el lado seguro: autorizado el 31-08, el último día es
        el 27-02 (o 28-02 en bisiesto), el día anterior al «mismo día» seis
        meses después.
        """
        if self.doc_type not in EXPIRING_TYPES or self.authorized_on is None:
            return None
        return _add_months(self.authorized_on, VALIDITY_MONTHS) - dt.timedelta(days=1)

    def is_expired(self, today: dt.date | None = None) -> bool:
        expires = self.expires_on
        return expires is not None and (today or dt.date.today()) > expires

    @property
    def is_certification(self) -> bool:
        return self.key_id == CERTIFICATION_IDK

    def keys_match(self) -> bool:
        """¿La llave privada (RSASK) corresponde a la pública del CAF (RSAPK)?

        Si no, cada timbre sale con una firma que el SII no puede verificar y
        cada documento se rechaza con su folio ya gastado. Se detecta al cargar.
        """
        da = self.caf_element.find("DA")
        modulus = da.findtext("RSAPK/M") if da is not None else None
        exponent = da.findtext("RSAPK/E") if da is not None else None
        if not modulus or not exponent:
            return False
        try:
            private_key = serialization.load_pem_private_key(
                self.rsa_private_key_pem.encode(), password=None
            )
            expected = rsa.RSAPublicNumbers(_b64_int(exponent), _b64_int(modulus))
        except (ValueError, TypeError, binascii.Error):
            return False
        if not isinstance(private_key, rsa.RSAPrivateKey):
            return False
        return private_key.public_key().public_numbers() == expected


def load_caf(path: str | Path) -> CAF:
    """Carga y parsea un archivo CAF desde disco."""
    return load_caf_bytes(Path(path).read_bytes())


def load_caf_bytes(data: bytes) -> CAF:
    """Parsea un CAF desde bytes (sin tocar disco; p.ej. CAF cifrado en BD).

    Cualquier defecto de estructura se informa como ``ValueError``: quien carga
    el archivo recibe un mensaje, no un error interno.
    """
    try:
        root = etree.fromstring(data)  # <AUTORIZACION>
    except etree.XMLSyntaxError as ex:
        raise ValueError(f"Archivo CAF inválido: no es XML ({ex}).") from ex

    caf_node = root.find("CAF")
    if caf_node is None:
        raise ValueError("Archivo CAF inválido: falta nodo <CAF>.")
    da = caf_node.find("DA")
    if da is None:
        raise ValueError("Archivo CAF inválido: falta nodo <DA>.")

    try:
        doc_type = int(da.findtext("TD") or "")
        folio_from = int(da.findtext("RNG/D") or "")
        folio_to = int(da.findtext("RNG/H") or "")
    except ValueError as ex:
        raise ValueError("Archivo CAF inválido: falta el tipo o el rango de folios.") from ex
    if folio_from < 1 or folio_to < folio_from:
        raise ValueError(f"Archivo CAF inválido: rango {folio_from}-{folio_to}.")

    issuer_rut = da.findtext("RE")
    if not issuer_rut:
        raise ValueError("Archivo CAF inválido: falta el RUT emisor <RE>.")

    rsask = root.findtext("RSASK")
    if not rsask:
        raise ValueError("Archivo CAF inválido: falta llave privada <RSASK>.")

    authorized_on = None
    if fa := (da.findtext("FA") or "").strip():
        try:
            authorized_on = dt.date.fromisoformat(fa)
        except ValueError as ex:
            raise ValueError(f"Archivo CAF inválido: fecha de autorización {fa!r}.") from ex

    idk = (da.findtext("IDK") or "").strip()
    return CAF(
        doc_type=doc_type,
        folio_from=folio_from,
        folio_to=folio_to,
        issuer_rut=issuer_rut,
        caf_element=caf_node,
        rsa_private_key_pem=rsask.strip(),
        authorized_on=authorized_on,
        key_id=int(idk) if idk.isdigit() else None,
    )


def _b64_int(value: str) -> int:
    return int.from_bytes(base64.b64decode(value), "big")


def _add_months(day: dt.date, months: int) -> dt.date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return day.replace(
        year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1])
    )
