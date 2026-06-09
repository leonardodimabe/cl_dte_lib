"""Valida los documentos generados en out/ contra los XSD oficiales del SII."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lxml import etree

from dte_chile.validation import ValidationError, Validator, XSDNotAvailable

root = Path(__file__).resolve().parents[1]
v = Validator(root / "schemas")
out = root / "out"

files = sorted(out.glob("*.xml"))
if not files:
    print("No hay XML en out/. Corre antes los demos (enviar/timbrar/libro/...).")
    sys.exit(0)

for file_path in files:
    raw = file_path.read_bytes()
    try:
        localname = etree.QName(etree.fromstring(raw)).localname
    except Exception as ex:
        print(f"⚠️  {file_path.name}: no es XML ({ex})")
        continue

    if not v.available(localname):
        print(f"⏭️  {file_path.name} (<{localname}>): sin XSD local, omitido")
        continue

    try:
        v.validate(raw)
        print(f"✅ {file_path.name} (<{localname}>): VÁLIDO según XSD del SII")
    except ValidationError as ex:
        print(f"❌ {file_path.name} (<{localname}>): INVÁLIDO")
        for err in ex.errors[:8]:
            print(f"     - {err}")
    except XSDNotAvailable as ex:
        print(f"⏭️  {file_path.name}: {ex}")
