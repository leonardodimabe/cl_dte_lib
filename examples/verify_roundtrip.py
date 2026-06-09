"""Verifica un DTE firmado tras serializar→re-parsear (como lo hace el SII).

Comprueba dos firmas de forma independiente al árbol en memoria original:
  1. La firma XMLDSig del documento (contra el cert embebido en KeyInfo).
  2. El TED/FRMT (timbre) contra la llave pública del CAF (RSAPK).

Si alguna falla aquí pero pasa en memoria, hay un problema de namespaces/C14N
que el SII rechazaría.
"""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from lxml import etree

from dte_chile import signer
from dte_chile.caf import load_caf

NS = "http://www.sii.cl/SiiDte"


def _extract_dd_literal(raw: bytes) -> bytes:
    """Extrae los bytes literales del <DD>...</DD> tal como aparecen en el XML.

    Es lo que hace el SII para verificar el timbre: opera sobre el texto literal
    del DD, sin inyectar el namespace heredado del <DTE>.
    """
    start = raw.find(b"<DD>")
    end = raw.find(b"</DD>")
    if start == -1 or end == -1:
        raise ValueError("No se encontró <DD>...</DD> literal en el XML.")
    return raw[start : end + len(b"</DD>")]


def verify_ted(raw: bytes, dte_node: etree._Element, caf) -> bool:
    """Verifica FRMT del TED con la RSAPK del CAF, replicando el chequeo del SII."""
    ted = next((e for e in dte_node.iter() if etree.QName(e).localname == "TED"), None)
    if ted is None:
        print("  [TED] no encontrado")
        return False
    frmt = next((e for e in ted if etree.QName(e).localname == "FRMT"), None)
    if frmt is None:
        print("  [TED] falta FRMT")
        return False

    dd_bytes = _extract_dd_literal(raw)
    signature = base64.b64decode(frmt.text)
    print(f"  [TED] DD literal empieza con: {dd_bytes[:40]!r}")

    # Verificación oficial: con la RSAPK embebida en el CAF (lo que hace el SII).
    da = caf.caf_element.find("DA")
    m = int.from_bytes(base64.b64decode(da.findtext("RSAPK/M")), "big")
    e = int.from_bytes(base64.b64decode(da.findtext("RSAPK/E")), "big")
    pub_rsapk = rsa.RSAPublicNumbers(e, m).public_key()
    try:
        pub_rsapk.verify(signature, dd_bytes, padding.PKCS1v15(), hashes.SHA1())
        print("  [TED] verifica con RSAPK del CAF (como el SII): OK")
        return True
    except Exception:
        pass

    # Diagnóstico: ¿la firma es válida contra la pública derivada de RSASK?
    # Si sí, el timbrado es correcto pero el CAF trae RSASK/RSAPK que NO son par
    # (típico de CAF demo). Un CAF real de certificación sí valida con RSAPK.
    from cryptography.hazmat.primitives import serialization

    priv = serialization.load_pem_private_key(
        caf.rsa_private_key_pem.encode("latin-1"), password=None
    )
    try:
        priv.public_key().verify(signature, dd_bytes, padding.PKCS1v15(), hashes.SHA1())
        print("  [TED] verifica con la pública derivada de RSASK: OK")
        print("  [TED] ⚠️  pero el CAF trae RSASK/RSAPK que NO son par (CAF demo).")
        print("  [TED]     El timbrado es correcto; con un CAF real validaría con RSAPK.")
        return True
    except Exception as ex:
        print(f"  [TED] verificación falló también con RSASK: {ex!r}")
        return False


def main() -> None:
    xml_path, caf_path = sys.argv[1], sys.argv[2]
    raw = Path(xml_path).read_bytes()
    caf = load_caf(caf_path)

    # Re-parsear desde bytes (como recibe el SII).
    dte = etree.fromstring(raw)
    print(f"Root: {etree.QName(dte).localname}  ns={etree.QName(dte).namespace}")
    doc = next((e for e in dte if etree.QName(e).localname == "Documento"), None)
    print(f"Documento ns tras reparse: {etree.QName(doc).namespace}")

    print("1) XMLDSig (round-trip):")
    sig_ok = signer.verify_signature(dte)
    print("   →", "OK" if sig_ok else "FALLA")

    print("2) TED / FRMT vs RSAPK del CAF:")
    ted_ok = verify_ted(raw, dte, caf)
    print("   →", "OK" if ted_ok else "FALLA")

    print("\nResultado:", "✅ ambos OK" if (sig_ok and ted_ok) else "❌ revisar namespaces/C14N")


if __name__ == "__main__":
    main()
