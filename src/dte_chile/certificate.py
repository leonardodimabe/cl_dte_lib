"""Carga del certificado digital (.pfx/.p12) del emisor.

El certificado es la identidad del representante legal y se usa para:
  - Firmar el documento DTE (XMLDSig).
  - Firmar el sobre EnvioDTE.
  - Firmar la semilla en la autenticación con el SII.

⚠️ Secreto: no subir el .pfx ni su contraseña a git.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12


@dataclass
class Certificate:
    private_key_pem: bytes
    cert_pem: bytes
    rut: str | None = None  # RUT del titular si se logra extraer del subject

    @classmethod
    def from_pfx(cls, path: str | Path, password: str) -> Certificate:
        return cls.from_pfx_bytes(Path(path).read_bytes(), password)

    @classmethod
    def from_pfx_bytes(cls, data: bytes, password: str) -> Certificate:
        """Carga el certificado desde los bytes del .pfx (sin tocar disco).

        Útil en un servicio multi-cliente que guarda los .pfx cifrados en BD.
        """
        key, cert, _ = pkcs12.load_key_and_certificates(data, password.encode("utf-8"))
        if key is None or cert is None:
            raise ValueError("El .pfx no contiene llave privada o certificado.")

        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)

        rut = _extract_rut(cert)
        return cls(private_key_pem=key_pem, cert_pem=cert_pem, rut=rut)


# OID propietario usado por las CA chilenas para almacenar el RUT del titular
# dentro del subjectAltName (otherName).
_OID_RUT_CL = "1.3.6.1.4.1.8321.1"


def _extract_rut(cert) -> str | None:
    """Extrae el RUT del titular del certificado.

    En los certificados chilenos el RUT va en el subjectAltName como ``otherName``
    con OID ``1.3.6.1.4.1.8321.1`` (codificado como IA5String DER). Como respaldo
    se intenta el ``serialNumber`` del subject.
    """
    from cryptography import x509

    # 1) subjectAltName / otherName con el OID chileno.
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for name in san:
            if isinstance(name, x509.OtherName) and name.type_id.dotted_string == _OID_RUT_CL:
                return _decode_ia5(name.value)
    except Exception:
        pass

    # 2) Respaldo: serialNumber del subject.
    try:
        from cryptography.x509.oid import NameOID

        attributes = cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
        if attributes:
            return attributes[0].value
    except Exception:
        pass
    return None


def _decode_ia5(der: bytes) -> str:
    """Decodifica un valor DER IA5String (tag 0x16) a texto.

    Si no tiene el encabezado esperado, decodifica los bytes imprimibles tal cual.
    """
    if len(der) >= 2 and der[0] == 0x16:
        length = der[1]
        return der[2 : 2 + length].decode("ascii", "ignore")
    return der.decode("ascii", "ignore").strip()
