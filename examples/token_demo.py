"""Prueba en vivo de autenticación contra el SII (ambiente Maullín / certificación).

Flujo: getSeed -> firmar semilla con el certificado -> getToken.
Si imprime un TOKEN, significa que el SII aceptó la firma de autenticación.

Uso:
    $env:PFX_PASS = "********"
    python examples/token_demo.py "C:\\ruta\\firma.pfx"
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dte_chile.certificate import Certificate
from dte_chile.sii_client import Environment, SIIClient


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PFX_PATH", "")
    password = os.environ.get("PFX_PASS", "")
    if not path or not password:
        print("Falta ruta del .pfx (arg1) o PFX_PASS en entorno.")
        sys.exit(2)

    cert = Certificate.from_pfx(path, password)
    print(f"Certificado: RUT {cert.rut}")

    client = SIIClient(cert, Environment.CERTIFICATION)

    print("→ Solicitando semilla a Maullín...")
    seed = client.get_seed()
    print(f"  SEMILLA: {seed}")

    print("→ Firmando semilla y pidiendo token...")
    token = client.get_token(seed)
    print(f"  TOKEN:   {token}")

    print("\n✅ Autenticación con el SII (certificación) exitosa.")


if __name__ == "__main__":
    main()
