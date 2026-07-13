# Envío de Cuenta de Gastos al Cliente — Diseño

**Fecha:** 2026-07-13
**Estado:** Aprobado por el usuario (diseño validado por secciones)

## Objetivo

Permitir cerrar financieramente la cuenta de gastos de una referencia (bloqueando
anticipos, gastos y carga de XML), y enviarla por correo al cliente con una balanza
anticipos vs. gastos en el cuerpo y un ZIP con los XML/PDF de los CFDI adjunto.
Registrar cada envío con tracking real de entrega/lectura vía SendGrid Event Webhook,
con listado de notificaciones en el módulo Finanzas y posibilidad de reenvío a otro
destinatario.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Naturaleza del cierre | Paso **independiente** del módulo Finanzas; NO reutiliza `referencias.CuentaGastos` (finalización operativa de capturistas) |
| Tracking | Webhook de eventos de SendGrid (delivered/open/bounce) con firma verificada |
| Destinatarios | Nuevos campos `email_cuenta_gastos` + `email_cuenta_gastos_cc` en `Cliente`, con fallback a `email_cobranza`; reenvío admite correo ad-hoc |
| Adjuntos | Un solo **ZIP** con todos los XML y PDF de los `XMLProveedor` de la referencia |
| Reapertura | Solo **superusuario** puede reabrir una cuenta cerrada |
| Flujo | Dos acciones separadas: 1) Cerrar, 2) Enviar |
| Mecanismo de envío | Librería `sendgrid` (Web API v3) con `custom_args` para correlación; el resto del proyecto sigue en SMTP |

## Modelos

### `finanzas.CierreCuentaGastos` (nuevo)

- `referencia` — OneToOne a `referencias.Referencia`, `on_delete=PROTECT`, `related_name='cierre_cg'`
- `cerrada_por` — FK User, `SET_NULL`, null
- `cerrada_en` — DateTimeField
- `nota` — CharField(300), blank
- `reabierta_por` — FK User, `SET_NULL`, null, blank
- `reabierta_en` — DateTimeField, null, blank

Semántica: la cuenta está **cerrada** si existe el registro y `reabierta_en IS NULL`.
Reapertura (solo superusuario): llena `reabierta_por/reabierta_en`. Re-cierre posterior:
actualiza `cerrada_por/cerrada_en` y limpia los campos de reapertura. Se conserva un
solo registro por referencia (auditoría del último ciclo).

### `finanzas.NotificacionCuentaGastos` (nuevo)

- `referencia` — FK a `Referencia`, `PROTECT`, `related_name='notificaciones_cg'`
- `destinatario` — EmailField
- `cc` — EmailField, blank
- `enviado_por` — FK User, `SET_NULL`, null
- `enviado_en` — DateTimeField, `auto_now_add`
- `estado` — choices: `ENVIADO`, `ENTREGADO`, `LEIDO`, `REBOTADO`, `ERROR` (default `ENVIADO`)
- `entregado_en`, `leido_en` — DateTimeField, null (los llena el webhook)
- `sg_message_id` — CharField(100), blank, `db_index=True` (header `X-Message-Id` de la API)
- `error_msg` — TextField, blank
- `es_reenvio` — BooleanField, default False
- `zip_file` — FileField(`storage=media_storage`, `upload_to='cuentas_gastos/%Y/%m/'`, null) — snapshot exacto del ZIP enviado, en DO Spaces

Los estados solo avanzan (`ENVIADO → ENTREGADO → LEIDO`); un evento `delivered` tardío
no degrada un `LEIDO`. `REBOTADO` y `ERROR` son terminales.

### `clientes.Cliente` (2 campos nuevos)

- `email_cuenta_gastos` — EmailField, blank
- `email_cuenta_gastos_cc` — EmailField, blank

Fallback: si `email_cuenta_gastos` está vacío se usa `email_cobranza`; ídem para CC.

## Flujo de UI (vista `finanzas:referencia_estado`)

**Cuenta abierta:** botón nuevo **"Cerrar cuenta de gastos"** junto a +Anticipo/+Gasto,
con modal de confirmación ("Ya no se podrán registrar anticipos ni gastos"). Solo
usuarios del módulo Finanzas.

**Cuenta cerrada:**
- Banner: "Cuenta de gastos cerrada el {fecha} por {usuario}".
- Se ocultan `+Anticipo`, `+Gasto` y toda la sección "Subir XML de proveedor".
- Defensa en servidor: las vistas `anticipo_crear`, `gasto_crear` y
  `subir_xml_proveedor` rechazan el POST si la referencia tiene cierre activo
  (mensaje de error + redirect al estado financiero).
- Aparece la **balanza**: dos columnas — izquierda "Anticipos del cliente" (fecha,
  monto); derecha "Gastos" (concepto, cantidad) — con totales por lado y saldo al pie.
  Es la vista previa del cuerpo del correo.
- Botón **"Enviar al cliente"**: modal con destinatario prellenado (fallback aplicado),
  CC editable, resumen (nº de CFDI, totales). Cliente sin ningún correo → campo vacío
  obligatorio.
- Con envíos previos: historial de notificaciones de la referencia con estado, y el
  botón cambia a **"Reenviar"** (correo ad-hoc; crea `NotificacionCuentaGastos` con
  `es_reenvio=True` reutilizando el `zip_file` guardado).
- Botón **"Reabrir cuenta"** solo para superusuario.
- "Emitir factura" NO se bloquea con el cierre.

## Servicio de envío — `finanzas/cuenta_gastos_envio.py`

Sigue el patrón de `finanzas/cobranza.py`.

1. **`construir_zip_cuenta_gastos(referencia)`** — toma todos los `XMLProveedor`
   ligados a la referencia; mete cada `xml_file` y `pdf_file` (si existe) en un ZIP
   en memoria nombrado `CG_{num_refe sin /}_{YYYYMMDD}.zip`. Guard: si supera
   **20 MB**, aborta con error claro (límite SendGrid: 30 MB por mensaje).
2. **Template `finanzas/email_cuenta_gastos.html`** — encabezado con referencia y
   cliente; balanza en dos columnas; totales y saldo. CSS inline.
3. **`enviar_cuenta_gastos(referencia, destinatario, cc, usuario, es_reenvio=False)`**:
   - Crea la `NotificacionCuentaGastos` primero (para tener `pk`).
   - `Mail` de la librería `sendgrid`: from `DEFAULT_FROM_EMAIL`, HTML de balanza,
     ZIP como attachment base64, `custom_args = {"notificacion_cg_id": str(pk)}`.
   - API key: `SENDGRID_API_KEY` existente en el entorno.
   - Captura `X-Message-Id` del response → `sg_message_id`.
   - Excepción o status ≥ 400 → estado `ERROR` + `error_msg`; reintentable desde UI.
   - Reenvío: reutiliza el `zip_file` ya guardado (no reconstruye).

**Dependencia nueva:** `sendgrid` en `requirements.txt`.

## Webhook — `POST /finanzas/webhooks/sendgrid/`

- `csrf_exempt`, sin login.
- Verifica firma ECDSA del Event Webhook con el helper `EventWebhook` de la librería
  `sendgrid`; llave pública en env var **`SENDGRID_WEBHOOK_PUBLIC_KEY`**. Firma
  inválida o ausente → 403.
- Mapeo de eventos: `delivered → ENTREGADO` (+`entregado_en`), `open → LEIDO`
  (+`leido_en`), `bounce`/`dropped → REBOTADO` (+razón en `error_msg`). Estados solo
  avanzan. Eventos sin `notificacion_cg_id` en `custom_args` (p. ej. correos de
  cobranza por SMTP) se ignoran devolviendo 200.

**Configuración manual en SendGrid (deploy, una sola vez):**
1. Settings → Mail Settings → Event Webhook: URL
   `https://<dominio-produccion>/finanzas/webhooks/sendgrid/`, eventos delivered,
   open, bounce, dropped.
2. Habilitar **Signed Event Webhook** y copiar la verification key a la env var
   `SENDGRID_WEBHOOK_PUBLIC_KEY` en App Platform.
3. Habilitar **Open Tracking** (Settings → Tracking).

**Limitación conocida:** "Leído" depende del open tracking (pixel); clientes de correo
que bloquean imágenes o hacen pre-escaneo (Outlook/Gmail corporativos) pueden no
dispararlo o dispararlo en falso. Es lo mejor disponible sin confirmación de lectura.

## Listado de notificaciones — `/finanzas/notificaciones-cg/`

- Vista del módulo Finanzas (`@modulo_required('Finanzas')`), entrada en submenú de
  Finanzas.
- Tabla: referencia (link al estado financiero), cliente, destinatario, enviado por,
  fecha, badge de estado, acciones (descargar ZIP, Reenviar con modal ad-hoc).
- Filtros por estado; búsqueda por referencia/cliente.

## Manejo de errores

| Caso | Comportamiento |
|---|---|
| Cliente sin correo | Modal exige capturar destinatario |
| ZIP > 20 MB | No se envía; mensaje con el tamaño |
| API SendGrid caída / error | Notificación `ERROR` con `error_msg`, visible en listado, botón reintentar |
| POST a anticipo/gasto/XML con cuenta cerrada | Rechazo en servidor + mensaje |
| Webhook sin firma válida | 403 |
| Evento webhook de otro correo | Ignorado con 200 |

## Testing

- **Cierre:** bloquea POST de anticipo/gasto/XML; solo Finanzas cierra; solo
  superusuario reabre; re-cierre tras reapertura limpia campos.
- **Envío:** ZIP contiene los XML/PDF esperados; fallback de correos; `custom_args`
  lleva el pk; error de API deja `ERROR` (cliente SendGrid mockeado); reenvío crea
  notificación nueva con `es_reenvio=True` y reutiliza ZIP.
- **Webhook:** firma inválida → 403; `delivered/open/bounce` actualizan estado sin
  retroceder; eventos ajenos ignorados.
- **UI:** template oculta botones y sección XML al cerrar; muestra balanza; listado
  de notificaciones filtra y muestra badges.

## Fuera de alcance

- Migrar cobranza/reportes a la Web API de SendGrid (siguen en SMTP).
- Confirmación de lectura formal (read receipts).
- Historial multi-ciclo de cierres/reaperturas (solo se audita el último ciclo).
