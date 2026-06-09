"""Validación de documentos contra los esquemas XSD oficiales del SII.

El SII rechaza cualquier documento que no calce con su XSD. Validar localmente
ANTES de enviar evita la mayoría de los rechazos (estructura, orden de campos,
tipos, campos faltantes).

Los XSD son archivos oficiales del SII. Cada familia vive en su propia subcarpeta
de ``schemas/`` porque los zips del SII comparten nombres de archivo con
contenidos distintos (ver schemas/README.md).

Uso:
    v = Validator("schemas")
    v.validate(xml_bytes)            # lanza ValidationError con el detalle
    if v.is_valid(xml_bytes): ...    # versión booleana
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from .errors import DteError

# Elemento raíz (nombre local) → archivo XSD que lo valida.
SCHEMAS: dict[str, str] = {
    "DTE": "dte/DTE_v10.xsd",
    "EnvioDTE": "dte/EnvioDTE_v10.xsd",
    "RespuestaDTE": "response/RespuestaEnvioDTE_v10.xsd",
    "EnvioRecibos": "receipts/EnvioRecibos_v10.xsd",
    "LibroCompraVenta": "iecv/LibroCV_v10.xsd",
}


class XSDNotAvailable(DteError):
    """No se encontró el archivo XSD requerido en la carpeta de esquemas."""


class ValidationError(DteError):
    """El documento no cumple el XSD. ``errors`` lista los problemas."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Documento inválido según XSD:\n" + "\n".join(errors))


class Validator:
    """Valida documentos del motor contra los XSD del SII."""

    def __init__(self, schemas_dir: str | Path = "schemas"):
        self._dir = Path(schemas_dir)
        self._cache: dict[str, etree.XMLSchema] = {}

    def available(self, root_localname: str) -> bool:
        """True si existe el XSD para ese tipo de documento."""
        filename = SCHEMAS.get(root_localname)
        return filename is not None and (self._dir / filename).exists()

    def validate(self, document: bytes | etree._Element) -> None:
        """Valida el documento. Lanza ValidationError si no cumple el XSD."""
        root = _root_element(document)
        localname = etree.QName(root).localname
        filename = SCHEMAS.get(localname)
        if filename is None:
            raise ValueError(f"No hay XSD mapeado para el elemento raíz <{localname}>.")

        schema = self._load(filename)
        if not schema.validate(root):
            raise ValidationError([_fmt(e) for e in schema.error_log])

    def is_valid(self, document: bytes | etree._Element) -> bool:
        """Versión booleana de :meth:`validate` (no lanza por errores de XSD)."""
        try:
            self.validate(document)
            return True
        except ValidationError:
            return False

    def _load(self, filename: str) -> etree.XMLSchema:
        if filename not in self._cache:
            path = self._dir / filename
            if not path.exists():
                raise XSDNotAvailable(
                    f"Falta el esquema {path}. Descarga los XSD oficiales del SII "
                    f"y colócalos en '{self._dir}/'. Ver schemas/README.md."
                )
            # Parseo con base en la carpeta para resolver imports/includes relativos.
            doc = etree.parse(str(path))
            try:
                self._cache[filename] = etree.XMLSchema(doc)
            except etree.XMLSchemaParseError as ex:
                raise XSDNotAvailable(
                    f"lxml no pudo compilar el esquema {filename}: {ex}. "
                    "Algunos XSD del SII (p.ej. LceCal en LibroCV) usan decimales "
                    "fuera de rango que libxml2 no acepta; requieren un parche."
                ) from ex
        return self._cache[filename]


def _root_element(document: bytes | etree._Element) -> etree._Element:
    if isinstance(document, (bytes, bytearray)):
        return etree.fromstring(document)
    return document


def _fmt(error) -> str:
    return f"línea {error.line}: {error.message}"
