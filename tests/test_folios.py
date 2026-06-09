"""Tests del control de folios / multi-CAF (sin depender de archivos CAF reales)."""

import pytest

from dte_chile.caf import CAF
from dte_chile.folios import FolioError, FolioManager, FoliosExhausted


def _caf(doc_type, folio_from, folio_to, rut="76158145-7"):
    """CAF mínimo para probar lógica de folios (sin llave ni nodo XML)."""
    return CAF(
        doc_type=doc_type,
        folio_from=folio_from,
        folio_to=folio_to,
        issuer_rut=rut,
        caf_element=None,
        rsa_private_key_pem="",
    )


def test_assigns_sequential_folios(tmp_path):
    fm = FolioManager(tmp_path / "reg.json")
    fm.register_caf(_caf(33, 1, 5))
    folios = [fm.next_folio(33)[0] for _ in range(5)]
    assert folios == [1, 2, 3, 4, 5]


def test_exhaustion(tmp_path):
    fm = FolioManager(tmp_path / "reg.json")
    fm.register_caf(_caf(33, 1, 2))
    fm.next_folio(33)
    fm.next_folio(33)
    with pytest.raises(FoliosExhausted):
        fm.next_folio(33)


def test_skips_gap_between_cafs(tmp_path):
    fm = FolioManager(tmp_path / "reg.json")
    fm.register_caf(_caf(33, 1, 3))
    fm.register_caf(_caf(33, 100, 102))  # hueco 4..99
    assigned = [fm.next_folio(33)[0] for _ in range(6)]
    assert assigned == [1, 2, 3, 100, 101, 102]


def test_returns_correct_caf(tmp_path):
    fm = FolioManager(tmp_path / "reg.json")
    c1 = _caf(33, 1, 2)
    c2 = _caf(33, 100, 101)
    fm.register_caf(c1)
    fm.register_caf(c2)
    fm.next_folio(33)  # 1 -> c1
    fm.next_folio(33)  # 2 -> c1
    folio, caf = fm.next_folio(33)  # 100 -> c2
    assert folio == 100 and caf is c2


def test_rejects_overlap(tmp_path):
    fm = FolioManager(tmp_path / "reg.json")
    fm.register_caf(_caf(33, 1, 10))
    with pytest.raises(FolioError, match="solapado"):
        fm.register_caf(_caf(33, 5, 15))


def test_duplicate_caf_is_idempotent(tmp_path):
    fm = FolioManager(tmp_path / "reg.json")
    fm.register_caf(_caf(33, 1, 5))
    fm.register_caf(_caf(33, 1, 5))  # mismo rango: no debe duplicar ni fallar
    assert fm.available_folios(33) == 5


def test_available_and_replenishment(tmp_path):
    fm = FolioManager(tmp_path / "reg.json")
    fm.register_caf(_caf(33, 1, 3))
    assert fm.available_folios(33) == 3
    fm.next_folio(33)
    assert fm.available_folios(33) == 2
    assert fm.needs_replenishment(33, threshold=5) is True
    assert fm.needs_replenishment(33, threshold=2) is False


def test_persistence_between_instances(tmp_path):
    reg = tmp_path / "reg.json"
    fm1 = FolioManager(reg)
    fm1.register_caf(_caf(33, 1, 5))
    assert fm1.next_folio(33)[0] == 1
    assert fm1.next_folio(33)[0] == 2

    # Nueva instancia: debe retomar desde el folio 3 (estado persistido).
    fm2 = FolioManager(reg)
    fm2.register_caf(_caf(33, 1, 5))
    assert fm2.next_folio(33)[0] == 3
    assert fm2.last_assigned(33) == 3


def test_independent_doc_types(tmp_path):
    fm = FolioManager(tmp_path / "reg.json")
    fm.register_caf(_caf(33, 1, 5))
    fm.register_caf(_caf(61, 200, 205))
    assert fm.next_folio(33)[0] == 1
    assert fm.next_folio(61)[0] == 200
    assert fm.next_folio(33)[0] == 2


def test_no_caf_raises(tmp_path):
    fm = FolioManager(tmp_path / "reg.json")
    with pytest.raises(FolioError):
        fm.next_folio(33)
