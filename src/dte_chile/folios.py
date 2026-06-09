"""Control de folios y gestión multi-CAF.

Resuelve el riesgo #1 de operar en producción: **no repetir un folio** (un folio
duplicado = documento duplicado = problema tributario grave). El SII no controla
qué sistema usa qué folio; esa coordinación es responsabilidad del emisor.

Diseño:
  - Se cargan uno o varios CAF por tipo de DTE.
  - Un registro persistente (JSON) guarda el último folio asignado por tipo.
  - ``next_folio`` entrega el folio siguiente y persiste el estado ANTES de
    devolverlo: si el proceso cae, el folio queda marcado como usado. Preferimos
    "quemar" un folio (hueco legal) antes que arriesgar una reutilización.
  - Los rangos de distintos CAF del mismo tipo no deben solaparse; si se detecta
    solapamiento se lanza error (sería una fuente de duplicados).

Limitación: el registro JSON asume un único proceso a la vez. Para concurrencia
real habría que añadir bloqueo de archivo / mover el estado a una BD.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from filelock import FileLock

from .caf import CAF
from .caf import load_caf as _load_caf_file
from .errors import DteError

logger = logging.getLogger(__name__)


class FolioError(DteError):
    """Error de gestión de folios."""


class FoliosExhausted(FolioError):
    """No quedan folios disponibles para el tipo solicitado."""


class FolioManager:
    """Gestiona folios sobre uno o varios CAF, con estado persistente."""

    def __init__(self, registry: str | Path):
        self._registry = Path(registry)
        self._registry.parent.mkdir(parents=True, exist_ok=True)
        self._cafs: dict[int, list[CAF]] = {}
        self._state: dict[str, int] = self._load_state()
        # Lock entre procesos: evita carreras de lectura-modificación-escritura
        # del registro (riesgo de folios duplicados con varios workers).
        self._lock = FileLock(str(self._registry) + ".lock", timeout=15)

    # ---------------- Carga de CAF ----------------
    def load_caf(self, path: str | Path) -> CAF:
        """Carga un CAF desde archivo y lo registra."""
        caf = _load_caf_file(path)
        self.register_caf(caf)
        return caf

    def load_directory(self, folder: str | Path) -> int:
        """Carga todos los CAF (*.xml) de una carpeta. Devuelve cuántos cargó."""
        count = 0
        for file_path in sorted(Path(folder).glob("*.xml")):
            try:
                self.load_caf(file_path)
                count += 1
            except Exception:
                continue  # no es un CAF válido; se ignora
        return count

    def register_caf(self, caf: CAF) -> None:
        """Agrega un CAF al gestor, validando que no solape rangos existentes."""
        entries = self._cafs.setdefault(caf.doc_type, [])

        for existing in entries:
            if existing.folio_from == caf.folio_from and existing.folio_to == caf.folio_to:
                return  # mismo rango: idempotente, no duplicar
            if _overlap(existing, caf):
                raise FolioError(
                    f"CAF solapado para tipo {caf.doc_type}: "
                    f"[{caf.folio_from}-{caf.folio_to}] choca con "
                    f"[{existing.folio_from}-{existing.folio_to}]."
                )

        entries.append(caf)
        entries.sort(key=lambda c: c.folio_from)

    # ---------------- Asignación ----------------
    def next_folio(self, doc_type: int) -> tuple[int, CAF]:
        """Asigna el siguiente folio disponible para ``doc_type`` y persiste el estado.

        Devuelve (folio, CAF que lo contiene). Lanza FoliosExhausted si no quedan.
        """
        cafs = self._cafs.get(doc_type)
        if not cafs:
            raise FolioError(f"No hay CAF cargado para el tipo {doc_type}.")

        # Toda la asignación va bajo lock, releyendo el estado desde disco: así
        # dos procesos no entregan el mismo folio.
        with self._lock:
            self._state = self._load_state()
            target = self._target(doc_type, cafs)
            candidates = [c for c in cafs if c.folio_to >= target]
            if not candidates:
                raise FoliosExhausted(
                    f"Folios agotados para tipo {doc_type} "
                    f"(último asignado: {self._state.get(str(doc_type))})."
                )

            caf = min(candidates, key=lambda c: c.folio_from)
            folio = max(target, caf.folio_from)  # salta huecos entre rangos

            self._state[str(doc_type)] = folio
            self._save_state()
        logger.info("Folio asignado: tipo %s → %s", doc_type, folio)
        return folio, caf

    # ---------------- Consulta ----------------
    def available_folios(self, doc_type: int) -> int:
        """Cantidad de folios aún no asignados para el tipo."""
        cafs = self._cafs.get(doc_type)
        if not cafs:
            return 0
        target = self._target(doc_type, cafs)
        total = 0
        for c in cafs:
            if c.folio_to >= target:
                total += c.folio_to - max(c.folio_from, target) + 1
        return total

    def last_assigned(self, doc_type: int) -> int | None:
        value = self._state.get(str(doc_type))
        return int(value) if value is not None else None

    def needs_replenishment(self, doc_type: int, threshold: int = 10) -> bool:
        """True si quedan menos de ``threshold`` folios (señal para pedir más CAF)."""
        return self.available_folios(doc_type) < threshold

    def summary(self) -> dict[int, dict]:
        """Resumen por tipo: rangos cargados, último asignado y disponibles."""
        out: dict[int, dict] = {}
        for doc_type, cafs in sorted(self._cafs.items()):
            out[doc_type] = {
                "ranges": [(c.folio_from, c.folio_to) for c in cafs],
                "last_assigned": self.last_assigned(doc_type),
                "available": self.available_folios(doc_type),
            }
        return out

    # ---------------- Internos ----------------
    def _target(self, doc_type: int, cafs: list[CAF]) -> int:
        """Primer folio candidato a asignar para el tipo."""
        last = self._state.get(str(doc_type))
        if last is None:
            return cafs[0].folio_from
        return int(last) + 1

    def _load_state(self) -> dict[str, int]:
        if self._registry.exists():
            try:
                return json.loads(self._registry.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as ex:
                raise FolioError(
                    f"Registro de folios corrupto: {self._registry}. "
                    "Revísalo manualmente antes de continuar (riesgo de duplicar folios)."
                ) from ex
        return {}

    def _save_state(self) -> None:
        self._registry.parent.mkdir(parents=True, exist_ok=True)
        # Escritura atómica: archivo temporal + replace, para no corromper el
        # registro si el proceso cae a mitad de la escritura.
        tmp = self._registry.with_suffix(self._registry.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._registry)


def _overlap(a: CAF, b: CAF) -> bool:
    return a.folio_from <= b.folio_to and b.folio_from <= a.folio_to
