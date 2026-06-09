"""Inspecciona los datos públicos de un CAF (sin exponer la llave privada).

Indica el ambiente según <IDK>:  100 = certificación (Maullín),  300 = producción (Palena).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dte_chile.caf import load_caf

caf = load_caf(sys.argv[1])
da = caf.caf_element.find("DA")

idk = da.findtext("IDK")
environment = {"100": "CERTIFICACIÓN (Maullín)", "300": "PRODUCCIÓN (Palena)"}.get(
    idk, f"desconocido (IDK={idk})"
)

print("RUT emisor (RE) :", da.findtext("RE"))
print("Razón social(RS):", da.findtext("RS"))
print("Tipo DTE (TD)   :", da.findtext("TD"))
print("Rango folios    :", da.findtext("RNG/D"), "→", da.findtext("RNG/H"))
print("Fecha autoriz.  :", da.findtext("FA"))
print("IDK             :", idk)
print("=> AMBIENTE      :", environment)
