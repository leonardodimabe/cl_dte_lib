# dte_chile — Motor de Facturación Electrónica (DTE) para Chile

Motor **standalone en Python** para emisión de Documentos Tributarios Electrónicos (DTE)
del SII de Chile. Diseñado para probarse de forma aislada (sin Odoo) y luego integrarse
como librería dentro de un módulo Odoo 19.

## Alcance del MVP

| Tipo | Documento | Estado |
|------|-----------|--------|
| 33   | Factura Electrónica afecta | ✅ MVP |
| 34   | Factura Electrónica exenta | ✅ MVP |
| 56   | Nota de Débito             | ✅ MVP |
| 61   | Nota de Crédito            | ✅ MVP |

Boleta (39) y Guía de Despacho (52) quedan fuera del MVP.

## Flujo que cubre

```
   datos negocio
        │
        ▼
  [ models.DTE ]  ── armado dominio (dataclasses)
        │
        ▼
  [ xml_builder ] ── XML <Documento> (Encabezado/Detalle/Referencias)
        │
        ▼
  [ ted ]         ── Timbre Electrónico (DD + firma RSA con llave del CAF)
        │
        ▼
  [ signer ]      ── Firma XMLDSig del documento con certificado .pfx
        │
        ▼
  [ envelope ]    ── Sobre SetDTE / EnvioDTE firmado
        │
        ▼
  [ sii_client ]  ── semilla → token → envío → consulta estado (Maullín/Palena)
```

## Estado de implementación

- ✅ Validación y formateo de RUT
- ✅ Modelos de dominio (Issuer, Receiver, Item, Reference, DTE)
- ✅ Cálculo de totales (neto/exento/IVA/total)
- ✅ Parseo del archivo CAF (folios + llave privada)
- ✅ Construcción del XML del Documento (33/34/56/61)
- ✅ Timbre Electrónico (TED) con firma RSA-SHA1
- ✅ Firma XMLDSig del documento (probada con certificado real; verifica OK)
- ✅ **Autenticación SII** semilla → firma → token (probada en vivo contra Maullín)
- ✅ Sobre **EnvioDTE (SetDTE)** + firma del sobre (ambas firmas verifican round-trip)
- ✅ **Control de folios / multi-CAF** con registro persistente (anti-duplicación)
- ✅ **Representación impresa** (HTML imprimible) con **timbre PDF417** (verificado: el barcode escaneado valida contra la RSAPK del CAF)
- ✅ **Acuses de intercambio**: parseo del EnvioDTE recibido, `RespuestaDTE` (acuse de recibo + aceptación/rechazo) y `EnvioRecibos` (recibo Ley 19.983, firmas anidadas)
- ✅ **Libro de Compras y Ventas (IECV)**: `LibroCompraVenta` con resumen por tipo y detalle, firmado
- ✅ **Validación contra XSD oficiales del SII**: DTE, EnvioDTE, RespuestaDTE, EnvioRecibos y LibroCV validan contra los esquemas oficiales (ver `schemas/README.md`)
- ✅ **Consulta del RCV** (Registro de Compra y Venta): descarga compras/ventas reales del SII por TLS mutuo con el certificado (`rcv.py`) — útil para conciliar contra Odoo
- ✅ **BHE recibidas** (Boletas de Honorarios Electrónicas): descarga del Informe Mensual de Boletas Recibidas del portal de honorarios del SII, con login por clave tributaria y paginación (`bhe.py`) — no existe vía de intercambio ni SOAP para esto, es scraping del CGI
- ✅ Envío **DTEUpload** + consulta de estado (código listo; upload requiere postulación a certificación del emisor)

## Requisitos

> **Python 3.11** (misma versión que usa Odoo 19; 3.13 da problemas con Odoo).

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate      # Windows PowerShell
pip install -r requirements.txt
```

`xmlsec` en Windows puede requerir instalar los binarios; ver requirements.txt.

## Uso rápido

```bash
python examples/generate_invoice_33.py
```

## Tests

```bash
pytest -q
```

## Desarrollo

Calidad de código (ver `pyproject.toml`):

```bash
pip install -e ".[dev]"   # incluye ruff, mypy, pytest
ruff check .              # lint
ruff format .             # formato
mypy                      # tipos
pytest -q                 # tests
```

Hay **CI en GitHub Actions** (`.github/workflows/ci.yml`) que corre todo en Linux.
En Linux las dependencias nativas de firma son paquetes del sistema:

```bash
sudo apt-get install -y libxml2-dev libxmlsec1-dev pkg-config
```

**Robustez:** las llamadas al SII usan reintentos/backoff (`_http.py`), errores
tipados (`errors.py`: `DteError` y subclases), logging vía `logging`, y el control
de folios usa **lock entre procesos** para no duplicar folios.

## ⚠️ Advertencias

- Este motor **no está certificado** ante el SII. Antes de producción debes pasar el
  **set de pruebas de certificación** en el ambiente Maullín.
- Nunca subas a git: certificados `.pfx`, archivos `CAF` ni llaves privadas.
