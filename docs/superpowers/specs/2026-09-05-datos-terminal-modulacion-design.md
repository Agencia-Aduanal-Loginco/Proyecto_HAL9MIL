# Captura de datos de terminal (carril/horarios) en Modulación vía link público

Fecha: 2026-09-05
Repos afectados: `Proyecto_HAL9MIL` (este repo, lado emisor/correo) y `BitacoraKasu`
(lado receptor/formulario) — mismo documento de diseño commiteado en ambos, cada uno con su
propia spec/plan de implementación.
Módulos afectados en este repo: `referencias/` (`modulacion.py`, `models.py` — `EnvioModulacion`)

## Contexto

Hoy la comunicación entre HAL9MIL y BitacoraKasu es de un solo sentido: `referencias/modulacion.py`
(`procesar_dodas_nuevas` → `_procesar_doda`) hace, por cada DODA nuevo: (1) enviar un correo
al capturista con el PDF del pedimento+DODA, y (2) hacer `POST` a BitacoraKasu por cada
contenedor (`_push_bitacorakasu`, vía `bitacorakasu_client.enviar_modulacion`).

BitacoraKasu necesita 5 campos que **no vienen de Firebird** (CARRIL, HORA DE REGISTRO,
HORA DE INGRESO, HORA DE CARGA — según terminal — y FECHA DE MODULACIÓN ANTE ADUANA): los
conoce el capturista de HAL9MIL recién agenda la cita de extracción con la terminal, **después**
de que el DODA ya se sincronizó y ya se hizo el push inicial. En vez de construir un
formulario y un modelo nuevos en HAL9MIL para esos 5 campos, BitacoraKasu expone un
formulario público (sin login) y HAL9MIL solo necesita **reenviar el link** que BitacoraKasu
ya genera y regresa en la respuesta de su API — ver el mismo documento de diseño commiteado
en `BitacoraKasu` para el detalle completo del lado receptor.

## Decisiones tomadas

Igual que en el documento gemelo de `BitacoraKasu` (resumen relevante para este lado):

| Tema | Decisión |
|---|---|
| Dónde vive el formulario de captura | En BitacoraKasu. HAL9MIL **no** construye ni persiste estos 5 campos. |
| Cómo llega el capturista | Dentro del **mismo correo** que ya envía `_enviar_email_modulacion` (PDF + aviso de extracción) — no hay correo nuevo, no hay trigger nuevo. |
| Quién genera el link | BitacoraKasu, en la respuesta JSON de `POST /modulacion/api/recibir/` (`completar_datos_url`, ausente si la terminal no aplica). HAL9MIL solo lo recolecta y lo reenvía. |
| Alcance por terminal | Determinado del lado de BitacoraKasu (catálogo `TerminalPortuaria`); HAL9MIL no necesita saber cuáles terminales aplican — simplemente incluye el botón cuando la respuesta trae el link, y lo omite cuando no. |

---

## Parte A — `EnvioModulacion`: persistir los links por contenedor

### A1. Modelo (`referencias/models.py`)

Nuevo campo en `EnvioModulacion`:

```python
links_completar = models.JSONField(default=dict, blank=True)
```

Guarda `{num_cont: completar_datos_url}` de los contenedores cuyo push devolvió un link.
Se necesita persistido (no solo en memoria durante `_procesar_doda`) porque
`reintentar_envio` puede reintentar **solo el email** cuando el push de una corrida previa
ya quedó `ENVIADO` — en ese caso no se vuelve a llamar `_push_bitacorakasu`, así que el
email debe poder leer los links de una ejecución anterior.

Migración correspondiente. Registrar en `referencias/admin.py` (opcional, como campo de
solo lectura en el detalle de `EnvioModulacion` — útil para soporte).

### A2. `_push_bitacorakasu(doda, envio)` — capturar el link de la respuesta

Hoy la función llama `enviar_modulacion(payload)` sin usar el valor de retorno. Cambia a:

```python
respuesta = enviar_modulacion(payload)
enviados += 1
url = respuesta.get('completar_datos_url')
if url:
    links[contenedor.num_cont] = url
```

con `links = {}` inicializado al principio de la función (junto a `fallidos = []`,
`enviados = 0`). Al final, antes de los `return`:

```python
if links:
    envio.links_completar = {**envio.links_completar, **links}
```

(merge, no reemplazo — un reintento con algunos contenedores ya exitosos antes conserva
esos links aunque esta corrida solo reintente los que fallaron). El resto de la función
(manejo de `fallidos`, `push_estado`) no cambia.

---

## Parte B — Reordenar push antes que email

`_enviar_email_modulacion` necesita los links del push para armar el contenido del correo,
así que el push debe correr **antes** que el email dentro de la misma corrida. El push ya
no depende del email (ya era así); ahora además el *contenido* del email depende del
*resultado* del push, pero no de si el push tuvo éxito global — si push falla para algunos
contenedores, el correo simplemente no trae botón para esos.

### B1. `_procesar_doda(doda, solo_push=False)`

Antes: email → push. Después:

```python
def _procesar_doda(doda, solo_push=False):
    envio = EnvioModulacion.objects.create(doda=doda)

    if _procesar_push(doda, envio):
        doda.modulacion_enviada_en = timezone.now()

    if not solo_push and _procesar_email(doda, envio):
        doda.notificado_en = timezone.now()

    envio.save()
    # ... (update_fields igual que hoy)
```

### B2. `reintentar_envio(envio, solo_push=False)`

Mismo cambio de orden (push antes que email), sin tocar la lógica de "solo reintentar lo
que sigue en ERROR/PENDIENTE" que ya tiene:

```python
def reintentar_envio(envio, solo_push=False):
    doda = envio.doda
    update_fields = []

    if envio.push_estado != 'ENVIADO':
        if _procesar_push(doda, envio):
            doda.modulacion_enviada_en = timezone.now()
            update_fields.append('modulacion_enviada_en')

    if not solo_push and envio.email_estado != 'ENVIADO':
        if _procesar_email(doda, envio):
            doda.notificado_en = timezone.now()
            update_fields.append('notificado_en')

    envio.save()
    # ... (resto igual)
```

Si el push ya estaba `ENVIADO` de una corrida previa (se salta), `envio.links_completar`
ya trae los links persistidos de esa corrida — el email los usa igual.

### B3. `_enviar_email_modulacion(doda, destinatario, envio)` — agregar los botones

Sin cambio de firma (ya recibe `envio`). Antes de armar el `html_content`, construir el
fragmento de links:

```python
botones = ''.join(
    f'<p><a href="{url}">Completar carril y horarios de terminal — '
    f'contenedor {num_cont}</a></p>'
    for num_cont, url in sorted(envio.links_completar.items())
)
```

E insertarlo en el `html_content` de `Mail(...)`, después del párrafo que pide iniciar la
extracción y antes de la firma:

```python
html_content=(
    f'<p>Estimado(a) {nombre},</p>'
    f'<p>Se generó la DODA <strong>{doda.num_doda}</strong> en la terminal '
    f'<strong>{doda.terminal_nombre}</strong>. Favor de iniciar la solicitud '
    f'de extracción del contenedor.</p>'
    f'<p>Se adjunta el pedimento + DODA para imprimir.</p>'
    f'{botones}'
    f'<p>{settings.NOMBRE_AGENCIA}</p>'
),
```

Si `envio.links_completar` está vacío (ninguna terminal de este DODA requiere datos extra,
o el push falló para todos), `botones` es `''` y el correo queda exactamente igual que hoy.

---

## Parte C — Pruebas (`referencias/test_modulacion.py`)

- `_push_bitacorakasu`: cuando el mock de `enviar_modulacion` regresa
  `{'success': True, 'completar_datos_url': '...'}`, el link queda en
  `envio.links_completar[num_cont]`; cuando regresa sin esa llave (terminal sin datos
  extra), no se agrega nada; merge correcto en una segunda llamada (reintento) que no
  pisa links ya guardados de contenedores que no se reintentan.
- `_procesar_doda`: orden push→email — mockear `_procesar_push` para poblar
  `envio.links_completar` y verificar que `_enviar_email_modulacion` (mockeado también)
  se llama con `envio` ya trayendo esos links.
- `_enviar_email_modulacion`: el `html_content` incluye un `<a href="...">` por cada
  entrada de `envio.links_completar`, y ninguno cuando está vacío.
- `reintentar_envio`: caso "push ya ENVIADO, solo reintenta email" — el correo sigue
  incluyendo los links persistidos de la corrida anterior.

Todo con mocks (`requests.post`, SendGrid), sin llamadas de red reales — mismo patrón que
ya usa el archivo.

---

## Fuera de alcance (se implementa en `BitacoraKasu`)

El modelo/catálogo (`Modulacion`, banderas de `TerminalPortuaria`), el endpoint que regresa
`completar_datos_url`, y el formulario público con el token firmado. Ver el mismo documento
de diseño commiteado en `BitacoraKasu`.
