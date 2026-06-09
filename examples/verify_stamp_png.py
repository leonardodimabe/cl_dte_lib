"""Escanea el PDF417 del timbre y verifica el TED contra la RSAPK del CAF.

Replica lo que hace un lector del SII: decodifica el barcode, extrae el <DD> y
valida el FRMT con la llave pública del CAF.
"""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree
from pdf417decoder import PDF417Decoder
from PIL import Image

from dte_chile.caf import load_caf

png_path, caf_path = sys.argv[1], sys.argv[2]

# 1) Decodificar el PDF417
decoder = PDF417Decoder(Image.open(png_path))
n = decoder.decode()
print("Barcodes detectados:", n)
data = decoder.barcode_data_index_to_string(0)
scanned_ted = data.encode("iso-8859-1") if isinstance(data, str) else bytes(data)
print("TED escaneado empieza:", scanned_ted[:40])

# 2) Extraer DD y FRMT del TED escaneado
ted = etree.fromstring(scanned_ted)
frmt = ted.find("FRMT")
start = scanned_ted.find(b"<DD>")
end = scanned_ted.find(b"</DD>") + len(b"</DD>")
dd_literal = scanned_ted[start:end]
signature = base64.b64decode(frmt.text)

# 3) Verificar contra la RSAPK del CAF
caf = load_caf(caf_path)
da = caf.caf_element.find("DA")
m = int.from_bytes(base64.b64decode(da.findtext("RSAPK/M")), "big")
e = int.from_bytes(base64.b64decode(da.findtext("RSAPK/E")), "big")
pub = rsa.RSAPublicNumbers(e, m).public_key()
try:
    pub.verify(signature, dd_literal, padding.PKCS1v15(), hashes.SHA1())
    print("\n✅ Timbre escaneado VERIFICA contra la RSAPK del CAF (lo que hace el SII).")
except Exception as ex:
    print(f"\n❌ Falló: {ex!r}")
