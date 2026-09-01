"""Validación y saneado del texto que va en un DTE.

Dos cosas rechaza el SII que no se ven a simple vista y que llegan solas desde
un ERP:

- **Caracteres fuera de ISO-8859-1.** El SII exige esa codificación. Comillas
  tipográficas, guiones largos, viñetas o emojis —que Word, Excel y los
  navegadores insertan sin avisar— no existen en ella. Al serializar salen como
  referencias numéricas (``&#8212;``) y el Servicio responde
  ``CHR-00001: Invalid Character Set``.
- **Largos excedidos.** Cada campo tiene su tope en el XSD. Truncar en silencio
  es peor que fallar: el documento sale con la razón social cortada y nadie se
  entera hasta que el cliente reclama.

Por eso acá se valida **antes** de armar el XML, se acumulan *todos* los
problemas —no el primero— y se informa qué campo, qué valor y qué le pasa.

Para el caso habitual de datos sucios importados, :func:`sanitize` traduce los
caracteres problemáticos a su equivalente ISO-8859-1 en vez de rechazarlos. Es
una decisión del llamador, nunca automática: cambiar lo que el usuario escribió
sin decírselo es su propia clase de error.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .errors import DteError

ENCODING = "ISO-8859-1"

# Reemplazos para los caracteres que más ensucian los datos importados.
# Todos salen de autocorrección de Office o de copiar y pegar desde la web.
_REPLACEMENTS = {
    "\u2018": "'",  # comilla simple izquierda
    "\u2019": "'",  # comilla simple derecha (la del apóstrofo de Word)
    "\u201c": '"',  # comilla doble izquierda
    "\u201d": '"',  # comilla doble derecha
    "\u2013": "-",  # guion corto (en dash)
    "\u2014": "-",  # guion largo (em dash)
    "\u2026": "...",  # puntos suspensivos
    "\u00a0": " ",  # espacio duro
    "\u2022": "-",  # viñeta
    "\u20ac": "EUR",  # euro: no existe en ISO-8859-1
    "\u2122": "TM",
    "\u00ad": "",  # guion suave invisible
}

# Largos máximos según los XSD oficiales del SII (DTE_v10 y SiiTypes_v10).
MAX_LENGTHS: dict[str, int] = {
    "RznSoc": 100,
    "RznSocRecep": 100,
    "GiroEmis": 80,
    "GiroRecep": 40,
    "DirOrigen": 70,
    "DirRecep": 70,
    "DirDest": 70,
    "CmnaOrigen": 20,
    "CmnaRecep": 20,
    "CmnaDest": 20,
    "CiudadOrigen": 20,
    "CiudadRecep": 20,
    "CiudadDest": 20,
    "Sucursal": 20,
    "Contacto": 80,
    "NmbItem": 80,
    "DscItem": 1000,
    "UnmdItem": 4,
    "RazonRef": 90,
    "FolioRef": 18,
    "GlosaDR": 45,
    "Glosa": 60,
    "Patente": 8,
    "PatenteCarro": 8,
    "NombreChofer": 30,
    "RznSocEmisor": 100,
    "GiroEmisor": 80,
    # Liquidación factura (43)
    "TpoDocLiq": 3,
    # Exportación (110/111/112): bloque Aduana
    "NombreTransp": 40,
    "NomCiaTransp": 40,
    "IdAdicTransp": 20,
    "Booking": 20,
    "Operador": 20,
    "IdAdicPtoEmb": 20,
    "IdAdicPtoDesemb": 20,
    "Marcas": 255,
    "IdContainer": 25,
    "Sello": 20,
    "EmisorSello": 70,
}


@dataclass(frozen=True)
class FieldProblem:
    """Un problema concreto en un campo del documento."""

    field: str  # dónde: "Emisor.RznSoc", "Detalle[2].NmbItem"
    problem: str  # qué le pasa, en castellano
    value: str  # el valor tal como venía

    def __str__(self) -> str:
        shown = self.value if len(self.value) <= 60 else self.value[:57] + "..."
        return f"{self.field}: {self.problem} (valor: {shown!r})"


class DocumentDataError(DteError):
    """Los datos del documento no cumplen lo que exige el SII.

    Trae **todos** los problemas encontrados, para poder corregirlos de una vez
    en lugar de descubrirlos de a uno por reintento.
    """

    def __init__(self, problems: list[FieldProblem]):
        self.problems = problems
        detail = "\n".join(f"  - {p}" for p in problems)
        super().__init__(
            f"El documento tiene {len(problems)} problema(s) que el SII rechazaría:\n{detail}"
        )


def unsupported_characters(value: str) -> list[str]:
    """Devuelve los caracteres del texto que ISO-8859-1 no puede representar."""
    bad = []
    for char in value:
        try:
            char.encode(ENCODING)
        except UnicodeEncodeError:
            if char not in bad:
                bad.append(char)
    return bad


def check(field: str, value: str | None, tag: str | None = None) -> list[FieldProblem]:
    """Revisa un campo: caracteres, largo y de control. Devuelve los problemas."""
    if not value:
        return []

    problems: list[FieldProblem] = []

    bad = unsupported_characters(value)
    if bad:
        described = ", ".join(f"{c!r} (U+{ord(c):04X} {_name_of(c)})" for c in bad)
        problems.append(
            FieldProblem(
                field,
                f"tiene caracteres que no existen en {ENCODING}: {described}",
                value,
            )
        )

    control = [c for c in value if ord(c) < 32 and c not in "\t\n\r"]
    if control:
        codes = ", ".join(f"U+{ord(c):04X}" for c in dict.fromkeys(control))
        problems.append(FieldProblem(field, f"tiene caracteres de control: {codes}", value))

    limit = MAX_LENGTHS.get(tag or "")
    if limit is not None and len(value) > limit:
        problems.append(
            FieldProblem(
                field,
                f"excede el máximo de {limit} caracteres (tiene {len(value)})",
                value,
            )
        )
    return problems


def issuer_problems(issuer) -> list[FieldProblem]:
    """Campos de texto del emisor, comunes a todos los tipos de documento."""
    return (
        check("Emisor.RznSoc", issuer.business_name, "RznSoc")
        + check("Emisor.GiroEmis", issuer.activity, "GiroEmis")
        + check("Emisor.DirOrigen", issuer.address, "DirOrigen")
        + check("Emisor.CmnaOrigen", issuer.commune, "CmnaOrigen")
        + check("Emisor.CiudadOrigen", issuer.city, "CiudadOrigen")
        + check("Emisor.Sucursal", issuer.branch_name, "Sucursal")
    )


def receiver_problems(receiver) -> list[FieldProblem]:
    """Campos de texto del receptor."""
    return (
        check("Receptor.RznSocRecep", receiver.business_name, "RznSocRecep")
        + check("Receptor.GiroRecep", receiver.activity, "GiroRecep")
        + check("Receptor.DirRecep", receiver.address, "DirRecep")
        + check("Receptor.CmnaRecep", receiver.commune, "CmnaRecep")
        + check("Receptor.CiudadRecep", receiver.city, "CiudadRecep")
    )


def reference_problems(references) -> list[FieldProblem]:
    """Campos de texto de las referencias."""
    problems: list[FieldProblem] = []
    for position, reference in enumerate(references, start=1):
        where = f"Referencia[{position}]"
        problems += check(f"{where}.RazonRef", reference.reason, "RazonRef")
        problems += check(f"{where}.FolioRef", str(reference.folio), "FolioRef")
    return problems


def sanitize(value: str) -> str:
    """Convierte el texto a algo representable en ISO-8859-1.

    Primero traduce los caracteres conocidos (comillas tipográficas, guiones
    largos, espacios duros); lo que quede sin equivalente se descompone por
    Unicode y se le quitan los acentos combinantes. Como último recurso, el
    carácter se elimina: preferible a mandar un documento que el SII rechaza.
    """
    if not value:
        return value

    translated = "".join(_REPLACEMENTS.get(c, c) for c in value)
    out = []
    for char in translated:
        try:
            char.encode(ENCODING)
            out.append(char)
            continue
        except UnicodeEncodeError:
            pass
        # Sin equivalente directo: quitar el acento y reintentar.
        plain = "".join(
            c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c)
        )
        for c in plain:
            try:
                c.encode(ENCODING)
                out.append(c)
            except UnicodeEncodeError:
                pass
    return "".join(out)


def truncate(value: str, tag: str) -> str:
    """Corta al máximo del campo. Sólo para uso interno del constructor de XML."""
    limit = MAX_LENGTHS.get(tag)
    return value if limit is None else value[:limit]


def _name_of(char: str) -> str:
    try:
        return unicodedata.name(char)
    except ValueError:
        return "sin nombre"
