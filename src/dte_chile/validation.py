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

# Namespaces del SII y de XML Schema Instance.
SII_NS = "http://www.sii.cl/SiiDte"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# Elemento raíz (nombre local) → archivo XSD que lo valida.
SCHEMAS: dict[str, str] = {
    "DTE": "dte/DTE_v10.xsd",
    "EnvioDTE": "dte/EnvioDTE_v10.xsd",
    "RespuestaDTE": "response/RespuestaEnvioDTE_v10.xsd",
    "EnvioRecibos": "receipts/EnvioRecibos_v10.xsd",
    "LibroCompraVenta": "iecv/LibroCV_v10.xsd",
    "LibroGuia": "lgd/LibroGuia_v10.xsd",
    "EnvioBOLETA": "bol/EnvioBOLETA_v11.xsd",
    "ConsumoFolios": "bol/ConsumoFolio_v10.xsd",
}


def schema_location(root_localname: str) -> str:
    """Valor de ``xsi:schemaLocation`` que el SII espera para ese documento."""
    return f"{SII_NS} {Path(SCHEMAS[root_localname]).name}"


def build_root(localname: str, **attributes: str) -> etree._Element:
    """Crea la raíz de un envío, ya declarada para el SII.

    El SII **rechaza** con ``SCH-00001: Invalid Schema Name`` cualquier archivo
    que no traiga ``xsi:schemaLocation``, aunque el XML sea válido contra el XSD.
    Por eso todas las raíces se construyen acá y no a mano.
    """
    element = etree.Element(
        f"{{{SII_NS}}}{localname}", nsmap={None: SII_NS, "xsi": XSI_NS}, **attributes
    )
    element.set(f"{{{XSI_NS}}}schemaLocation", schema_location(localname))
    return element


# El SII exige la declaración EXACTAMENTE así, con comillas dobles. lxml la
# emite con comillas simples y el Servicio responde "CHR-00001: Invalid
# Character Set", por eso se escribe a mano.
XML_DECLARATION = b'<?xml version="1.0" encoding="ISO-8859-1"?>\n'


#: El SII rechaza el envío entero si alguna línea del XML pasa de ~4.090
#: caracteres: «CHR-00002: Line too long». No está en ningún XSD. Se avisa un
#: poco antes para no depender del valor exacto.
MAX_LINE_LENGTH = 4000


class LineTooLong(DteError):
    """Una línea del XML supera lo que el SII acepta."""


def serialize_document(element: etree._Element) -> bytes:
    """Serializa un envío en ISO-8859-1 con la declaración que espera el SII."""
    body = etree.tostring(element, xml_declaration=False, encoding="ISO-8859-1")
    xml = XML_DECLARATION + body
    _check_line_length(xml)
    return xml


def wrap_long_lines(node: etree._Element, max_len: int = MAX_LINE_LENGTH) -> None:
    """Corta en líneas las ramas cuya serialización sería demasiado larga.

    Sólo toca lo necesario: si un elemento cabe en una línea, se deja como está.
    Donde no cabe, cada hijo pasa a su propia línea y se repite el análisis hacia
    dentro. Así un documento corriente sale igual que siempre y sólo los grandes
    —un libro de 26 detalles, una liquidación con muchas líneas— se reparten.

    Hay que llamarlo **antes de firmar**: el espacio en blanco es contenido para
    la canonicalización, así que añadirlo después movería el digest y la firma
    dejaría de valer.
    """
    if len(etree.tostring(node, encoding="ISO-8859-1")) <= max_len:
        return
    for hijo in node:
        if not (hijo.tail or "").endswith("\n"):
            hijo.tail = (hijo.tail or "") + "\n"
        wrap_long_lines(hijo, max_len)


def _check_line_length(xml: bytes) -> None:
    """Falla acá antes de que el SII rechace el envío por una línea larga.

    Un XML sin saltos va todo en una línea, y el Servicio lo rechaza entero con
    «ENV - 3 - Error en Schema - CHR-00002: Line too long». Pasó con un libro de
    26 detalles, cuya única línea llegó a 10.848 caracteres. Es un límite que no
    aparece en ningún esquema, así que el XSD lo da por bueno y el rechazo llega
    después de gastar un TrackID.
    """
    for numero, linea in enumerate(xml.splitlines(), start=1):
        if len(linea) > MAX_LINE_LENGTH:
            raise LineTooLong(
                f"La línea {numero} tiene {len(linea)} caracteres y el SII rechaza"
                f" el envío por encima de ~{MAX_LINE_LENGTH} («CHR-00002: Line too"
                " long»). Hay que cortar el XML en líneas ANTES de firmarlo: el"
                " espacio en blanco entra en la canonicalización."
            )


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
