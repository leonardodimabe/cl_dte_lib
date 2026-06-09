"""Fábrica de sesiones HTTP con reintentos para los servicios del SII.

El SII es intermitente (502/503 y cortes de conexión transitorios). Una sesión
con reintentos + backoff evita fallos espurios. Los reintentos aplican a errores
de conexión y a 5xx; las operaciones del SII que reintentamos son idempotentes
(semilla, token, consulta de estado, descarga RCV). El upload reenvía el mismo
sobre (mismos folios), que el SII trata como reenvío del mismo documento.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def build_session(total_retries: int = 3, backoff: float = 0.5) -> requests.Session:
    """Devuelve una ``requests.Session`` con reintentos/backoff montados."""
    retry = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
