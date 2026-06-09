"""Utilidades de RUT chileno: validación, dígito verificador y formateo.

El SII exige el RUT en formato ``99999999-D`` (sin puntos, con guion y DV en
mayúscula). Estas funciones normalizan cualquier entrada a ese formato.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CLEAN = re.compile(r"[.\-\s]")


def _check_digit(body: int) -> str:
    """Calcula el dígito verificador de un RUT por el algoritmo módulo 11."""
    total = 0
    multiplier = 2
    for digit in reversed(str(body)):
        total += int(digit) * multiplier
        multiplier = 2 if multiplier == 7 else multiplier + 1
    remainder = 11 - (total % 11)
    if remainder == 11:
        return "0"
    if remainder == 10:
        return "K"
    return str(remainder)


def validate_rut(rut: str) -> bool:
    """Devuelve True si el RUT (con o sin formato) tiene un DV correcto."""
    try:
        body, dv = _split(rut)
    except ValueError:
        return False
    return _check_digit(body) == dv


def format_rut(rut: str) -> str:
    """Normaliza a formato SII ``99999999-D``. Lanza ValueError si es inválido."""
    body, dv = _split(rut)
    if _check_digit(body) != dv:
        raise ValueError(f"RUT con dígito verificador inválido: {rut}")
    return f"{body}-{dv}"


def _split(rut: str) -> tuple[int, str]:
    cleaned = _CLEAN.sub("", rut.strip()).upper()
    if len(cleaned) < 2 or not cleaned[:-1].isdigit():
        raise ValueError(f"RUT con formato inválido: {rut}")
    return int(cleaned[:-1]), cleaned[-1]


@dataclass(frozen=True)
class Rut:
    """RUT validado y normalizado al formato del SII."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", format_rut(self.value))

    def __str__(self) -> str:
        return self.value
