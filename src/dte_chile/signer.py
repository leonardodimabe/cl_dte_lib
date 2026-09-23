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
import re

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree

try:
    import xmlsec

    _XMLSEC_OK = True
except ImportError:  # pragma: no cover - entorno sin xmlsec instalado
    _XMLSEC_OK = False

from .certificate import Certificate
from .validation import wrap_long_lines

NS_DTE = "http://www.sii.cl/SiiDte"
NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"


def wrap_dte(document: etree._Element) -> etree._Element:
    """Crea el nodo raíz <DTE> con el <Documento> dentro (aún sin firmar).

    Declara **sólo** el namespace del SII. Nada de ``xmlns:xsi``, aunque el
    sobre que lo va a contener sí lo declare por el ``xsi:schemaLocation``.

    El motivo está en cómo valida el SII: no canonicaliza el <Documento> dentro
    del sobre, sino que extrae el <DTE> como fragmento suelto y lo canonicaliza
    ahí. Y la C14N **inclusiva** emite en el nodo firmado todos los namespaces
    en ámbito: si al firmar hay un ``xmlns:xsi`` que en el fragmento no está, el
    digest no coincide y el SII responde «(DTE-3-505) Firma DTE Incorrecta».

    Antes se declaraba aquí, buscando que el contexto de firma fuese idéntico al
    de dentro del sobre. El razonamiento estaba invertido: el que manda es el
    del fragmento. Medido sobre un sobre real rechazado —misma firma, mismo
    documento— el fragmento sin ``xsi`` da inválida y con ``xsi`` válida.

    Es lo que hace el módulo chileno de Odoo, que está certificado: su plantilla
    escribe ``<DTE xmlns="http://www.sii.cl/SiiDte" version="1.0">`` y calcula el
    digest sobre el <Documento> serializado por separado.

    Consecuencia que conviene saber: la firma de un <Documento> NO valida si se
    verifica con el sobre entero como contexto, porque ahí el ``xsi`` del sobre
    sí está en ámbito. Es correcto, y a los DTE de Odoo les pasa igual: se
    verifican extrayendo el <DTE>, que es como los lee el Servicio.
    """
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

    # El SII rechaza el envío entero si una línea del XML pasa de ~4.090
    # caracteres («CHR-00002: Line too long»), y un XML sin saltos va todo en
    # una. Se corta ACÁ porque es el único punto por el que pasan todos los
    # documentos, y porque tiene que ser antes de firmar: el espacio en blanco
    # entra en la canonicalización y añadirlo después movería el digest.
    wrap_long_lines(signed_node)

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


#: Cuántas veces se vuelve a firmar un <DTE> cuya firma no verifica.
_REINTENTOS_FIRMA = 2


def sign_document(document: etree._Element, cert: Certificate) -> etree._Element:
    """Devuelve el <DTE> firmado, con la firma ya comprobada.

    Firmar y no mirar el resultado costó un rechazo del SII —`(DTE-3-505) Firma
    DTE Incorrecta` en uno de los tres documentos de un sobre de exportación,
    con el mismo contenido que había firmado bien el día anterior y que vuelve a
    firmar bien al reintentarlo—. El documento ya tiene su folio y su timbre
    cuando llega aquí, así que **volver a firmarlo no gasta nada**: sólo
    recalcula la firma sobre el mismo contenido.

    Si tras los reintentos sigue sin verificar, se aborta. Es preferible fallar
    al emitir, con el folio ya gastado pero sabiendo cuál es el documento, que
    mandar al SII un sobre que va a rechazar: eso cuesta además el intento y un
    día de espera hasta su respuesta.

    La verificación aísla el <DTE>, que es como lo comprueba el Servicio.
    """
    for _ in range(_REINTENTOS_FIRMA + 1):
        dte = sign_enveloped(wrap_dte(document), document, cert)
        # Sobre una copia: `verify_signatures` aísla el nodo y no debe tocar el
        # árbol que se devuelve.
        if verify_signatures(etree.fromstring(etree.tostring(dte)))[0]:
            return dte
        # Quitar la firma que no sirvió antes de volver a intentarlo.
        for firma in document.findall("{%s}Signature" % NS_DSIG):
            document.remove(firma)
    folio = document.findtext(".//{%s}Folio" % NS_DTE) or "?"
    tipo = document.findtext(".//{%s}TipoDTE" % NS_DTE) or "?"
    raise ValueError(
        f"la firma del documento tipo {tipo} folio {folio} no verifica tras"
        f" {_REINTENTOS_FIRMA + 1} intentos: el SII lo rechazaría con DTE-3-505."
    )


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


def _verify_in(contexto: etree._Element, sig: etree._Element) -> bool:
    """Verifica ``sig`` usando ``contexto`` como documento."""
    ctx = xmlsec.SignatureContext()
    for node in contexto.iter():
        if node.get("ID"):
            ctx.register_id(node, id_attr="ID")
    ctx.key = xmlsec.Key.from_memory(
        _cert_pem_from_keyinfo(sig),
        xmlsec.constants.KeyDataFormatCertPem,
        None,
    )
    try:
        ctx.verify(sig)
        return True
    except xmlsec.VerificationError:
        return False


#: Fragmentos que se firman sueltos y después se meten dentro de otro documento:
#: el ``<DTE>`` en su sobre, y el documento cedido y la cesión dentro del AEC.
#: Su firma se calculó sin los namespaces del envoltorio, así que verificarla
#: exige volver a aislarlos.
_FRAGMENTOS_FIRMADOS_SUELTOS = frozenset(
    {
        "{%s}DTE" % NS_DTE,
        "{%s}DTECedido" % NS_DTE,
        "{%s}Cesion" % NS_DTE,
    }
)


def verify_signatures(root: etree._Element) -> list[bool]:
    """Verifica TODAS las firmas XMLDSig del árbol, como las verifica el SII.

    Devuelve una lista de booleanos, una por <Signature> encontrada, en orden.

    La firma de un ``<DTE>`` se verifica **con ese <DTE> aislado como
    contexto**, no con el sobre entero. Es como lo hace el Servicio, y la
    diferencia no es cosmética: dentro del sobre está en ámbito el
    ``xmlns:xsi`` de la raíz, que la C14N inclusiva mete en el digest; en el
    fragmento no está. Verificar con el sobre entero daba por buenas firmas que
    el SII rechaza —y por malas las correctas—, que es exactamente el agujero
    por el que se colaron dos envíos.

    Lo mismo vale para el documento cedido y la cesión dentro de un AEC: se
    firman sueltos y viajan dentro de un sobre que sí declara ``xsi``.

    Las demás firmas (el <SetDTE> del sobre, el <DocumentoAEC>, un acuse) sí se
    verifican contra el árbol completo: son la raíz de lo que se transmite, no
    un trozo.
    """
    if not _XMLSEC_OK:
        raise RuntimeError("xmlsec no está instalado.")

    results = []
    for sig in root.iter("{%s}Signature" % NS_DSIG):
        padre = sig.getparent()
        if padre is not None and padre.tag in _FRAGMENTOS_FIRMADOS_SUELTOS:
            aislado = _aislar(padre)
            suya = aislado.find("{%s}Signature" % NS_DSIG)
            results.append(suya is not None and _verify_in(aislado, suya))
        else:
            results.append(_verify_in(root, sig))
    return results


#: Cada ``<DTE>`` del texto transmitido, de la apertura al cierre.
_DTE_EN_TEXTO = re.compile(rb"<DTE[\s>].*?</DTE>", re.S)
#: Declaración XML del sobre: el fragmento suelto la necesita para decodificar
#: bien los caracteres ISO-8859-1.
_DECLARACION = re.compile(rb"^<\?xml[^>]*\?>")


def verify_transmitted(xml: bytes) -> list[bool]:
    """Verifica las firmas de un sobre **tal como se va a transmitir**.

    `verify_signatures` trabaja sobre un árbol, y un árbol no conserva dónde
    está escrita cada declaración de namespace: al aislar un <DTE>, lxml le
    repone el ``xmlns`` que hereda del sobre aunque en el texto no esté. El SII
    no tiene árbol, tiene bytes: corta cada <DTE> del texto y lo verifica suelto.
    Si al <DTE> le falta su ``xmlns``, el fragmento queda sin namespace, el
    digest cambia y responde «Firma DTE Incorrecta».

    Pasó con el primer sobre de boletas: sus cinco firmas verificaban sobre el
    árbol y el SII rechazó las cinco. Aquí los <DTE> se cortan del texto igual
    que lo hace el Servicio; las demás firmas (la del SetDTE) se verifican con
    el documento completo, que es su contexto real.
    """
    if not _XMLSEC_OK:
        raise RuntimeError("xmlsec no está instalado.")

    declaracion = _DECLARACION.match(xml)
    prefijo = declaracion.group(0) if declaracion else b""
    results = []
    for trozo in _DTE_EN_TEXTO.findall(xml):
        fragmento = etree.fromstring(prefijo + trozo)
        firma = fragmento.find("{%s}Signature" % NS_DSIG)
        results.append(firma is not None and _verify_in(fragmento, firma))

    root = etree.fromstring(xml)
    for sig in root.iter("{%s}Signature" % NS_DSIG):
        padre = sig.getparent()
        if padre is None or padre.tag != "{%s}DTE" % NS_DTE:
            results.append(_verify_in(root, sig))
    return results


#: Declaración de namespace en la etiqueta de apertura que no sea la del SII.
_NS_AJENO = re.compile(rb'\s+xmlns:[A-Za-z0-9_.-]+="[^"]*"')


def _aislar(dte: etree._Element) -> etree._Element:
    """El fragmento suelto, igual que lo extrae el SII del documento que lo trae.

    Serializar el subárbol no basta: lxml repone en la etiqueta de apertura los
    namespaces que el nodo heredaba del sobre, con lo que el ``xmlns:xsi``
    volvería a colarse y el fragmento dejaría de parecerse al que viaja. En el
    sobre transmitido el <DTE> declara sólo el namespace del SII.
    """
    crudo = etree.tostring(dte)
    apertura = crudo[: crudo.index(b">") + 1]
    return etree.fromstring(crudo.replace(apertura, _NS_AJENO.sub(b"", apertura), 1))


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
