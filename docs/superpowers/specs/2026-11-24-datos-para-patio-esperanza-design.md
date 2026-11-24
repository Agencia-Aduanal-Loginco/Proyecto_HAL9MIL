# Push a Kasu por expediente y correos de seguimiento al ejecutivo

Fecha: 2026-11-24 (revisado el mismo día con las respuestas de Tony)
Estado: propuesto, con preguntas abiertas al final
Módulos afectados: `referencias/` (`modulacion.py`, `models.py`, `views.py`, nuevo
`eventos_views.py`), `core/` (destinatarios)
Depende de: `2026-11-24-aviso-cruce-avc-y-expediente-design.md` (este repo)
Specs relacionados:
- `BitacoraKasu`: `docs/superpowers/specs/2026-11-24-integracion-patio-esperanza-v2-design.md`
- `ProyectoPatioEsperanza`: `docs/superpowers/specs/2026-11-24-api-socios-solicitud-ingreso-design.md`

## Flujo completo

```
HAL9MIL                      Kasu                              Patio Esperanza
-------                      ----                              ---------------
Expediente (DODA + AVC)
  -> push por contenedor --> Modulación
                             Destino: patio / domicilio / otro
                             Unidad + operador -> ASIGNADO
  <- evento extracción ----
  correo al ejecutivo        (si destino = patio)
                               -> solicitud de entrada ----->  PIN
                             PIN en programa de despacho
                             Semáforo aduanal / salida
  <- evento extracción ----
  correo al ejecutivo                                          Portería: ingreso
                             <- webhook ingreso ------------
  <- evento llegada patio -
  correo al ejecutivo                                          ... estancia ...
                             <- webhook despachado ---------
  <- evento salida patio --
  correo al ejecutivo
```

## Lo que ya funciona y se conserva

- Push por contenedor con `Authorization: Token`, aislamiento de fallos y reintento por
  `reintentar_envio`.
- Solo se empujan DODA de `CVE_CAAT_KASU`.
- Links `completar_datos_url` persistidos en `EnvioModulacion`.

## Decisiones propuestas

### 1. El push lleva la clave del expediente y los datos del AVC

Se mantienen todos los campos actuales (Kasu los exige) y se agregan:

| Campo | Valor |
| --- | --- |
| `clave_expediente` | `ExpedienteExtraccion.clave` (`"{num_refe}:{num_cont}"`). Kasu deduplica por aquí |
| `estado_documental` | `FALTA_AVC`, `FALTA_DODA`, `COMPLETO`, `AVC_VENCIDO`, `CANCELADO` |
| `acuse_avc`, `fecha_vigencia_avc` | Del AVC, si ya existe |
| `peso_kg` | Entero, de `Referencia.peso_bruto` (`SAAIO_PEDIME.PES_BRUT`) |
| `peso_confirmado` | `False` cuando Firebird no trae peso |
| `cliente_rfc` | `rfc_importador` del expediente |
| `cliente_clave` | `Referencia.cve_cliente` |
| `num_refe` | Referencia de Loginco |

- Se empuja cuando nace el expediente y cada vez que cambia su `estado_documental`. Kasu
  actualiza la misma modulación.
- `idempotency_key` pasa a ser `"{clave_expediente}:{estado_documental}"`.

### 2. Unidad del peso: kilos

`PES_BRUT` viene en **kilos** (confirmado por Tony). Kasu lo convierte a toneladas para
asignar unidad y armar equipos; Esperanza lo usa en kilos.

**Riesgo a revisar antes de implementar.** El código de HAL9MIL asume toneladas:

- `Referencia.peso_bruto` tiene el comentario `# CTRAO_EMBAR.PES_BRUT (toneladas)`, aunque
  el sync lo lee de `SAAIO_PEDIME.PES_BRUT`.
- `Referencia.peso_kg` multiplica por 1000.
- `context.md` dice que el cruce de la glosa usa `ref.peso_kg`.

Si `PES_BRUT` ya está en kilos, la glosa compara contra un peso **1000 veces mayor** al real.
Antes de tocar nada: tomar 3 pedimentos reales, comparar `PES_BRUT` contra el peso del B/L y
decidir. Si se confirma, `peso_kg` regresa `peso_bruto` tal cual, se corrige el comentario y
se vuelven a correr las pruebas de glosa.

En el push, `peso_toneladas` se sigue mandando por compatibilidad, pero con el **mismo valor
en kilos que hoy** (eso es lo que Kasu ya guarda). Kasu deja de leerlo cuando implemente
`peso_kg` (ver su spec).

### 3. Endpoint de eventos de seguimiento

`POST /api/integracion/eventos-contenedor/`, con `Authorization: Token <HAL9MIL_EVENTOS_TOKEN>`
(token distinto del de `/api/sync/`):

```json
{
  "evento_id": "kasu:modulacion:1234:ASIGNADO",
  "clave_expediente": "LCLF0353/26:MSCU1234567",
  "tipo": "extraccion.asignada",
  "ocurrido_en": "2026-11-25T09:10:00-06:00",
  "datos": {"unidad": "ECO 21", "placas": "12AB3C", "operador": "Juan Pérez", "destino": "PATIO_ESPERANZA"}
}
```

| `tipo` | Lo manda | Cuándo |
| --- | --- | --- |
| `extraccion.asignada` | Kasu | Asigna unidad, operador y destino |
| `extraccion.confirmada` | Kasu | El contenedor salió de la terminal (ver pregunta abierta) |
| `extraccion.reconocimiento` | Kasu | Semáforo rojo o retenido |
| `patio.ingreso` | Kasu (reenvía el webhook de Esperanza) | Entró al Patio Esperanza |
| `patio.salida` | Kasu (reenvía el webhook de Esperanza) | Salió del Patio Esperanza, con días de estancia |

- Guarda cada evento en `EventoContenedor` (`expediente` FK, `tipo`, `ocurrido_en`, `datos`
  jsonb, `evento_id` único). Un evento repetido responde 200 sin volver a mandar correo.
- Clave de expediente desconocida: 404, y Kasu lo reintenta (puede llegar antes que el sync).
- **Los eventos de Esperanza llegan a través de Kasu.** Kasu ya es el socio que recibe sus
  webhooks y conoce la clave del expediente; así HAL9MIL tiene un solo emisor y un solo
  token que administrar. Si más adelante otros transportistas usan el patio, Esperanza
  podría avisar directo a HAL9MIL.

### 4. Correo al ejecutivo

Cada evento manda un correo (SendGrid, mismo patrón que `_enviar_email_modulacion`) con
asunto `"{contenedor} · {evento}"` y cuerpo con referencia, pedimento, cliente y los datos
del evento. El destinatario se resuelve en una función nueva `resolver_ejecutivo(expediente)`:

1. Ejecutivo asignado al cliente, si existe (ver pregunta abierta).
2. Si no, el capturista del DODA (`resolver_destinatario(doda.cve_capt)`), igual que hoy.
3. Si no, `MODULACION_FALLBACK_EMAILS`.

Los eventos se muestran también en la línea de tiempo de la referencia
(`referencias/timeline.html`).

## Pruebas

- El push incluye `clave_expediente`, `peso_kg` y los datos del AVC; un cambio de estado
  documental vuelve a empujar con otra `idempotency_key`.
- Eventos: token inválido 401; `evento_id` repetido no duplica correo; clave desconocida
  404; cada tipo genera el asunto esperado.
- `resolver_ejecutivo` cae al capturista y luego al fallback.
- Regresión de glosa después de decidir la unidad del peso.

## Preguntas abiertas

- [ ] **¿Quién es "el ejecutivo"?** Hoy HAL9MIL solo conoce al capturista (`CVE_CAPT` del
  DODA). ¿Es otra persona, por ejemplo un ejecutivo de cuenta por cliente? Si sí, se agrega
  `email_ejecutivo` a `clientes.Cliente`.
- [ ] **"Kasu confirma la extracción":** ¿es al asignar unidad y operador (`ASIGNADO`), al
  entrar a la terminal (`INGRESADO`), o al salir con semáforo verde
  (`DESADUANAMIENTO_LIBRE`)? La propuesta manda los dos extremos: asignación y salida de
  la terminal.
- [ ] ¿El ejecutivo quiere un correo por evento, o un resumen diario además?
