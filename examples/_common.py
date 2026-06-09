"""Utilidades compartidas por los demos (no forman parte de la librería).

El gate de validación XSD vive aquí para no duplicarlo en cada demo. La librería
``dte_chile.validation`` se mantiene pura (solo lanza excepciones); el "abortar
el proceso" es una decisión de la capa CLI/demo.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from dte_chile.validation import ValidationError, Validator, XSDNotAvailable

_SCHEMAS = _ROOT / "schemas"


def validate_xsd_or_abort(xml: bytes, label: str = "documento") -> None:
    """Valida ``xml`` contra el XSD oficial del SII.

    - Cumple → imprime OK y continúa.
    - No cumple → imprime los errores y ABORTA el proceso (exit 1).
    - XSD no disponible → avisa y continúa (no bloquea si faltan esquemas).
    """
    print(f"\n→ Validando {label} contra el XSD del SII...")
    try:
        Validator(_SCHEMAS).validate(xml)
        print(f"  ✅ {label} válido según XSD.")
    except ValidationError as ex:
        print(f"  ❌ {label} NO cumple el XSD — se ABORTA:")
        for err in ex.errors[:10]:
            print(f"     - {err}")
        sys.exit(1)
    except XSDNotAvailable as ex:
        print(f"  ⚠️  Validación omitida (XSD no disponible): {ex}")
