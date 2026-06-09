# Esquemas XSD del SII

Coloca aquí los archivos **XSD oficiales del SII**. El validador
(`dte_chile.validation.Validator`) los carga desde esta carpeta.

## Archivos esperados

| Documento | XSD |
|-----------|-----|
| DTE (33/34/56/61) | `DTE_v10.xsd` |
| Sobre de envío | `EnvioDTE_v10.xsd` |
| Respuesta / acuse de recibo | `RespuestaEnvioDTE_v10.xsd` |
| Recibo de mercaderías (Ley 19.983) | `Recibos_v10.xsd` |
| Libro de Compras y Ventas | `LibroCV_v10.xsd` |

Estos esquemas **se referencian entre sí** por ruta relativa (`xsd:import` /
`xsd:include`), por lo que hay que colocar **todos** los archivos del paquete
en esta misma carpeta, incluyendo dependencias como:

- `SiiTypes_v10.xsd`
- `xmldsignature_v10.xsd`  (firma XMLDSig)

## Estructura (subcarpetas por familia)

Los zips del SII **comparten nombres de archivo** (`SiiTypes_v10.xsd`,
`xmldsignature_v10.xsd`) con contenidos distintos, así que cada familia vive en
su propia subcarpeta para no pisarse:

```
schemas/
├── dte/        ← DTE_v10.xsd, EnvioDTE_v10.xsd, SiiTypes, xmldsignature
├── iecv/       ← LibroCV_v10.xsd + Lce*  (LibroCompraVenta)
├── response/   ← RespuestaEnvioDTE_v10.xsd (+ SiiTypes, xmldsignature copiados)
└── receipts/   ← EnvioRecibos_v10.xsd, Recibos_v10.xsd  (Ley 19.983)
```

## Descarga automática (recomendado)

```powershell
powershell -ExecutionPolicy Bypass -File schemas\download_schemas.ps1
```

Descarga los 4 paquetes oficiales del SII, los ubica en sus subcarpetas, copia
las dependencias que faltan y aplica un parche menor (libxml2 no acepta un
decimal de 34 dígitos en `LceSiiTypes_v10.xsd`).

## Fuentes oficiales del SII

| Paquete | Contenido |
|---------|-----------|
| `schema_dte.zip` | DTE, EnvioDTE |
| `schema_iecv.zip` | LibroCV (IECV) |
| `schema_ic.zip` | RespuestaEnvioDTE |
| `schema19983.zip` | Recibos (Ley 19.983) |

Base: `https://www.sii.cl/factura_electronica/factura_mercado/`

> ⚠️ Usa siempre los XSD **oficiales del SII** (no copias de terceros).

Esta carpeta está en `.gitignore` (salvo este README y el script): los XSD no se
versionan.
