"""Firma XMLDSig (enveloped) del documento DTE usando el certificado.

El SII es extremadamente estricto con la firma:
  - Transform enveloped + canonicalización C14N.
  - Reference al atributo ID del nodo <Documento> (URI="#F33T...").
  - El <Signature> va como hermano del <Documento>, dentro de <DTE>.

Se usa la librería ``xmlsec`` (binding de xmlsec1), que es la referencia para
producir firmas que el SII acepta. En Windows puede requerir wheels precompilados.
"""

from __future__ import annotations

import base64

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree

try:
    import xmlsec

    _XMLSEC_OK = True
except ImportError:  # pragma: no cover - entorno sin xmlsec instalado
    _XMLSEC_OK = False

from .certificate import Certificate

NS_DTE = "http://www.sii.cl/SiiDte"
NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"


def wrap_dte(document: etree._Element) -> etree._Element:
    """Crea el nodo raíz <DTE> con el <Documento> dentro (aún sin firmar)."""
    dte = etree.Element("{%s}DTE" % NS_DTE, nsmap={None: NS_DTE}, version="1.0")
    dte.append(document)
    return dte


def sign_enveloped(
    root: etree._Element, signed_node: etree._Element, cert: Certificate
) -> etree._Element:
    """Firma XMLDSig enveloped sobre ``signed_node`` (referenciado por su ID).

    El <Signature> se agrega como último hijo de ``root``. Reutilizable para el
    <Documento> (dentro de <DTE>) y para el <SetDTE> (dentro de <EnvioDTE>).
    """
    if not _XMLSEC_OK:
        raise RuntimeError(
            "xmlsec no está instalado. Ejecuta `pip install xmlsec` "
            "(en Windows puede requerir wheels precompilados de PyPI)."
        )

    node_id = signed_node.get("ID")
    # El SII fija (xmldsignature_v10.xsd) C14N **inclusiva** (REC-xml-c14n-20010315),
    # no exclusiva. Usar exclusiva hace que el documento no valide contra el XSD.
    signature_node = xmlsec.template.create(
        root,
        c14n_method=xmlsec.constants.TransformInclC14N,
        sign_method=xmlsec.constants.TransformRsaSha1,
    )
    root.append(signature_node)

    ref = xmlsec.template.add_reference(
        signature_node, xmlsec.constants.TransformSha1, uri="#" + node_id
    )
    xmlsec.template.add_transform(ref, xmlsec.constants.TransformEnveloped)

    # El XSD del SII exige KeyInfo con <KeyValue> (RSAKeyValue) ANTES de <X509Data>.
    key_info = xmlsec.template.ensure_key_info(signature_node)
    xmlsec.template.add_key_value(key_info)
    xmlsec.template.add_x509_data(key_info)

    ctx = xmlsec.SignatureContext()
    ctx.register_id(signed_node, id_attr="ID")
    key = xmlsec.Key.from_memory(cert.private_key_pem, xmlsec.constants.KeyDataFormatPem, None)
    key.load_cert_from_memory(cert.cert_pem, xmlsec.constants.KeyDataFormatPem)
    ctx.key = key

    ctx.sign(signature_node)
    # xmlsec deja el <KeyValue> vacío; lo poblamos con el RSAKeyValue del
    # certificado. KeyInfo no está firmado (la Reference apunta al nodo con ID),
    # así que esto no invalida la firma.
    _populate_rsa_key_value(signature_node, cert.cert_pem)
    return root


def _populate_rsa_key_value(signature_node: etree._Element, cert_pem: bytes) -> None:
    """Rellena <KeyValue> con <RSAKeyValue><Modulus/><Exponent/></RSAKeyValue>."""
    kv = signature_node.find(".//{%s}KeyValue" % NS_DSIG)
    if kv is None:
        return
    public_key = x509.load_pem_x509_certificate(cert_pem).public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        return
    nums = public_key.public_numbers()
    modulus = nums.n.to_bytes((nums.n.bit_length() + 7) // 8, "big")
    exponent = nums.e.to_bytes((nums.e.bit_length() + 7) // 8, "big")
    rsa_kv = etree.SubElement(kv, "{%s}RSAKeyValue" % NS_DSIG)
    etree.SubElement(rsa_kv, "{%s}Modulus" % NS_DSIG).text = base64.b64encode(modulus).decode(
        "ascii"
    )
    etree.SubElement(rsa_kv, "{%s}Exponent" % NS_DSIG).text = base64.b64encode(exponent).decode(
        "ascii"
    )


def sign_document(document: etree._Element, cert: Certificate) -> etree._Element:
    """Devuelve el <DTE> firmado (enveloped XMLDSig sobre el <Documento>)."""
    dte = wrap_dte(document)
    return sign_enveloped(dte, document, cert)


def sign_set_dte(
    envelope: etree._Element, set_dte: etree._Element, cert: Certificate
) -> etree._Element:
    """Firma el <SetDTE> dentro de un <EnvioDTE> (enveloped XMLDSig)."""
    return sign_enveloped(envelope, set_dte, cert)


def sign_seed(seed: str, cert: Certificate) -> bytes:
    """Firma el request ``getToken`` para autenticarse ante el SII.

    Construye ``<getToken><item><Semilla>..</Semilla></item></getToken>`` y le
    aplica una firma XMLDSig enveloped sobre el documento completo (URI=""),
    con canonicalización **inclusiva** y KeyInfo con X509 + RSAKeyValue, que es
    lo que espera el servicio GetTokenFromSeed.
    """
    if not _XMLSEC_OK:
        raise RuntimeError("xmlsec no está instalado.")

    root = etree.Element("getToken")
    item = etree.SubElement(root, "item")
    etree.SubElement(item, "Semilla").text = str(seed)

    signature_node = xmlsec.template.create(
        root,
        c14n_method=xmlsec.constants.TransformInclC14N,
        sign_method=xmlsec.constants.TransformRsaSha1,
    )
    root.append(signature_node)

    ref = xmlsec.template.add_reference(signature_node, xmlsec.constants.TransformSha1, uri="")
    xmlsec.template.add_transform(ref, xmlsec.constants.TransformEnveloped)

    key_info = xmlsec.template.ensure_key_info(signature_node)
    xmlsec.template.add_x509_data(key_info)
    xmlsec.template.add_key_value(key_info)

    ctx = xmlsec.SignatureContext()
    key = xmlsec.Key.from_memory(cert.private_key_pem, xmlsec.constants.KeyDataFormatPem, None)
    key.load_cert_from_memory(cert.cert_pem, xmlsec.constants.KeyDataFormatPem)
    ctx.key = key
    ctx.sign(signature_node)

    return etree.tostring(root, encoding="UTF-8")


def verify_signature(dte: etree._Element) -> bool:
    """Verifica la firma XMLDSig de un <DTE> usando el certificado embebido en KeyInfo.

    Devuelve True si la firma es criptográficamente válida. Útil para autochequeo
    antes de enviar al SII (evita rechazos por firma corrupta).
    """
    if not _XMLSEC_OK:
        raise RuntimeError("xmlsec no está instalado.")

    signature_node = dte.find("{%s}Signature" % NS_DSIG)
    if signature_node is None:
        return False

    document = dte.find("Documento")
    if document is None:
        # El documento puede estar namespaced; buscar por nombre local.
        for child in dte:
            if etree.QName(child).localname == "Documento":
                document = child
                break
    ctx = xmlsec.SignatureContext()
    if document is not None and document.get("ID"):
        ctx.register_id(document, id_attr="ID")

    key = xmlsec.Key.from_memory(
        _cert_pem_from_keyinfo(signature_node),
        xmlsec.constants.KeyDataFormatCertPem,
        None,
    )
    ctx.key = key
    try:
        ctx.verify(signature_node)
        return True
    except xmlsec.VerificationError:
        return False


def verify_signatures(root: etree._Element) -> list[bool]:
    """Verifica TODAS las firmas XMLDSig del árbol (cada una contra su KeyInfo).

    Registra todos los atributos ``ID`` del documento para que cada Reference
    resuelva. Devuelve una lista de booleanos, una por <Signature> encontrada.
    """
    if not _XMLSEC_OK:
        raise RuntimeError("xmlsec no está instalado.")

    results = []
    for sig in root.iter("{%s}Signature" % NS_DSIG):
        ctx = xmlsec.SignatureContext()
        for node in root.iter():
            if node.get("ID"):
                ctx.register_id(node, id_attr="ID")
        ctx.key = xmlsec.Key.from_memory(
            _cert_pem_from_keyinfo(sig),
            xmlsec.constants.KeyDataFormatCertPem,
            None,
        )
        try:
            ctx.verify(sig)
            results.append(True)
        except xmlsec.VerificationError:
            results.append(False)
    return results


def _cert_pem_from_keyinfo(signature_node: etree._Element) -> bytes:
    """Reconstruye el certificado PEM a partir del <X509Certificate> del KeyInfo."""
    cert_node = signature_node.find(".//{%s}X509Certificate" % NS_DSIG)
    if cert_node is None or not cert_node.text:
        raise ValueError("La firma no contiene X509Certificate en KeyInfo.")
    b64 = "".join(cert_node.text.split())
    pem = "-----BEGIN CERTIFICATE-----\n"
    pem += "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64))
    pem += "\n-----END CERTIFICATE-----\n"
    return pem.encode("ascii")
