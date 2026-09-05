# Reenviar link de completar datos de terminal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cuando BitacoraKasu regresa `completar_datos_url` en la respuesta del push por
contenedor (ya implementado en ese repo), HAL9MIL debe recolectar esos links y agregarlos
al correo que ya envía al capturista — sin correo nuevo, sin modelo nuevo, sin tocar el
contrato de payload que ya se manda hoy.

**Architecture:** `referencias/modulacion.py` ya hace, por cada DODA nueva: push por
contenedor a BitacoraKasu (`_push_bitacorakasu`) y correo al capturista
(`_enviar_email_modulacion`), ambos registrados en un único `EnvioModulacion`. Este trabajo
agrega un campo `JSONField` a `EnvioModulacion` para persistir `{num_cont: url}`, hace que
`_push_bitacorakasu` capture el link de la respuesta de `enviar_modulacion(payload)` (hoy
ese valor de retorno se ignora), reordena `_procesar_doda`/`reintentar_envio` para que el
push corra **antes** que el email (así el email puede leer los links ya recolectados), y
hace que `_enviar_email_modulacion` agregue un botón por contenedor con link disponible.

**Tech Stack:** Django 5.2, mismo patrón de tests ya usado en `referencias/test_modulacion.py`
(mocks de `requests.post` y `SendGridAPIClient`, sin llamadas de red reales).

## Global Constraints

- Todo el código, docstrings y comentarios van en español (convención del repo).
- Ninguna falla de email o de push debe propagar una excepción fuera de
  `procesar_dodas_nuevas`/`reintentar_envio` — este invariante ya existe, no debe romperse.
- Correr pruebas con: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias`
  (la base Postgres gestionada en `.env` no tiene base `postgres` administrativa; **no
  editar `.env`**, sólo sobreescribir `DBURL` como variable de entorno).
- Cero llamadas de red reales en los tests — todo mock de `requests.post` y
  `SendGridAPIClient`, siguiendo el patrón ya usado en `referencias/test_modulacion.py`.
- Después de cualquier tarea que toque modelos: `python manage.py makemigrations --check`
  debe no reportar migraciones faltantes.

---

### Task 1: `EnvioModulacion.links_completar` — modelo, migración, admin

**Files:**
- Modify: `referencias/models.py`
- Create: `referencias/migrations/0015_enviomodulacion_links_completar.py` (autogenerado;
  el nombre exacto puede variar según lo que Django proponga)
- Modify: `referencias/admin.py`
- Test: `referencias/test_modulacion.py`

**Interfaces:**
- Produces: `EnvioModulacion.links_completar` (`JSONField(default=dict, blank=True)`) —
  guarda `{num_cont: completar_datos_url}` de los contenedores cuyo push devolvió un link.

- [ ] **Step 1: Escribir la prueba que falla**

Agregar a `referencias/test_modulacion.py`, justo antes de la sección
`# procesar_dodas_nuevas — helpers de fixture` (usa el fixture `_doda`, que se define ahí
mismo, así que Python lo resuelve en tiempo de ejecución sin problema aunque esta clase
quede antes en el archivo):

```python
class EnvioModulacionLinksCompletarTests(TestCase):
    def test_links_completar_nace_vacio(self):
        doda = _doda()
        envio = EnvioModulacion.objects.create(doda=doda)
        self.assertEqual(envio.links_completar, {})

    def test_links_completar_persiste_el_dict(self):
        doda = _doda()
        envio = EnvioModulacion.objects.create(
            doda=doda, links_completar={'HLXU1234567': 'https://bitacora.test/x/'},
        )
        envio.refresh_from_db()
        self.assertEqual(envio.links_completar, {'HLXU1234567': 'https://bitacora.test/x/'})
```

- [ ] **Step 2: Correr la prueba para verificar que falla**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias.test_modulacion.EnvioModulacionLinksCompletarTests -v 2`
Expected: FAIL — `TypeError: 'links_completar' is an invalid keyword argument for this function`
(el campo todavía no existe en el modelo).

- [ ] **Step 3: Agregar el campo al modelo**

En `referencias/models.py`, dentro de `class EnvioModulacion(models.Model):`, reemplazar
el bloque de campos completo (desde `doda` hasta `updated_at`) por (agrega
`links_completar` y realinea los `=` de todo el bloque, ya que ahora es el nombre más
largo):

```python
    doda            = models.ForeignKey(Doda, on_delete=models.CASCADE, related_name='envios_modulacion')
    email_estado    = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')
    push_estado     = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')
    sg_message_id   = models.CharField(max_length=100, blank=True)
    error_detalle   = models.TextField(blank=True)
    links_completar = models.JSONField(default=dict, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)
```

- [ ] **Step 4: Generar la migración**

Run: `python manage.py makemigrations referencias`
Expected: crea `referencias/migrations/0015_enviomodulacion_links_completar.py` (o nombre
similar autogenerado) con un único `AddField`.

- [ ] **Step 5: Correr la prueba para verificar que pasa**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias.test_modulacion.EnvioModulacionLinksCompletarTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 6: Agregar el campo al admin (solo lectura)**

En `referencias/admin.py`, en `EnvioModulacionAdmin`, agregar `'links_completar'` a la
tupla `readonly_fields` (entre `'error_detalle'` y `'created_at'`):

```python
    readonly_fields = ('doda', 'email_estado', 'push_estado', 'sg_message_id',
                       'error_detalle', 'links_completar', 'created_at', 'updated_at')
```

- [ ] **Step 7: Correr toda la suite para verificar que nada se rompió**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias -v 2`
Expected: PASS (todos los tests existentes + los 2 nuevos).

- [ ] **Step 8: Commit**

```bash
git add referencias/models.py referencias/migrations/ referencias/admin.py referencias/test_modulacion.py
git commit -m "feat(modulacion): agrega EnvioModulacion.links_completar"
```

---

### Task 2: `_push_bitacorakasu` captura `completar_datos_url` por contenedor

Depende de: Task 1 (`EnvioModulacion.links_completar`).

**Files:**
- Modify: `referencias/modulacion.py`
- Test: `referencias/test_modulacion.py`

**Interfaces:**
- Consumes: `enviar_modulacion(payload) -> dict` (ya existente en
  `referencias/bitacorakasu_client.py`, sin cambios) — ahora se usa su valor de retorno.
- Produces: al terminar `_push_bitacorakasu(doda, envio)`, `envio.links_completar` queda
  actualizado (merge, no reemplazo) con `{num_cont: url}` de cada contenedor cuya respuesta
  trajo `completar_datos_url`. Firma de la función sin cambios (sigue devolviendo `bool`).

- [ ] **Step 1: Escribir las pruebas que fallan**

Agregar a `referencias/test_modulacion.py`, dentro de la clase `ProcesarDodasNuevasTests`
(después de `test_caso_feliz_email_y_pushes_exitosos`, que ya deja fijo el `setUp` con
`self.doda`, `self.cont1` = `'HLXU1234567'`, `self.cont2` = `'TCLU7654321'`):

```python
    def test_links_completar_se_guarda_solo_para_contenedores_con_url(self):
        from .modulacion import procesar_dodas_nuevas

        def _post_side_effect(url, json=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            if json['contenedor'] == 'HLXU1234567':
                data = {'status': 'ok', 'completar_datos_url':
                        'https://bitacora.test/modulacion/completar/tok1/'}
            else:
                data = {'status': 'ok'}
            resp.json.return_value = data
            return resp

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post',
                   side_effect=_post_side_effect):
            sg_cls.return_value.send.return_value = _resp_sendgrid()

            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.links_completar, {
            'HLXU1234567': 'https://bitacora.test/modulacion/completar/tok1/',
        })

    def test_sin_completar_datos_url_no_agrega_nada_a_links(self):
        from .modulacion import procesar_dodas_nuevas

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            mock_post.return_value = _resp_bitacorakasu()  # sin completar_datos_url

            procesar_dodas_nuevas([self.doda])

        envio = EnvioModulacion.objects.get(doda=self.doda)
        self.assertEqual(envio.links_completar, {})

    def test_links_completar_se_conserva_entre_llamadas_de_push(self):
        from .modulacion import _push_bitacorakasu

        envio = EnvioModulacion.objects.create(
            doda=self.doda, links_completar={'YA': 'https://existente.test/'},
        )

        with patch('referencias.bitacorakasu_client.requests.post') as mock_post:
            mock_post.return_value = _resp_bitacorakasu()  # sin link nuevo esta vez

            _push_bitacorakasu(self.doda, envio)

        self.assertEqual(envio.links_completar, {'YA': 'https://existente.test/'})
```

- [ ] **Step 2: Correr las pruebas para verificar que fallan**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias.test_modulacion.ProcesarDodasNuevasTests -v 2`
Expected: `test_links_completar_se_guarda_solo_para_contenedores_con_url` y
`test_links_completar_se_conserva_entre_llamadas_de_push` FALLAN (`envio.links_completar`
queda `{}` en ambos casos, porque `_push_bitacorakasu` todavía ignora el valor de retorno
de `enviar_modulacion`); `test_sin_completar_datos_url_no_agrega_nada_a_links` puede pasar
de casualidad (ya es `{}` hoy) — no es problema, lo relevante es que las otras dos fallan.

- [ ] **Step 3: Implementar la captura del link en `_push_bitacorakasu`**

En `referencias/modulacion.py`, reemplazar la función `_push_bitacorakasu` completa
(líneas actuales 135-200) por:

```python
def _push_bitacorakasu(doda, envio):
    """POST a BitacoraKasu por cada contenedor de las referencias del DODA.

    Un fallo en un contenedor no aborta los demás. Si algún POST falla, el
    push_estado general queda en ERROR con el detalle de los que fallaron;
    si no había contenedores que enviar, también queda en ERROR (nada que
    confirmar). Nunca propaga BitacoraKasuError.

    Además recolecta, en envio.links_completar, el `completar_datos_url` que
    BitacoraKasu regresa por cada contenedor cuya terminal lo requiere (merge
    sobre lo que ya hubiera de una corrida anterior — un reintento parcial no
    debe perder los links de los contenedores que no se reintentan).
    """
    fallidos = []
    enviados = 0
    links = {}

    referencias = (
        doda.referencias_doda.select_related('referencia')
        .prefetch_related('referencia__contenedores')
        .all()
    )
    for doda_ref in referencias:
        referencia = doda_ref.referencia
        if referencia is None:
            continue
        for contenedor in referencia.contenedores.all():
            payload = {
                'agencia': AGENCIA,
                'terminal_portuaria': doda.terminal_nombre,
                'tipo_contenedor': contenedor.tipo,
                # peso_toneladas es requerido por BitacoraKasu (ver
                # REQUIRED_FIELDS en su views_api.py) — si Firebird no trae
                # peso_bruto, mandar '' hace que el push falle y la DODA
                # quede en ERROR para siempre. Se manda '0' para que el
                # registro sí entre y el personal de Kasu lo detecte y
                # verifique el dato manualmente.
                'peso_toneladas': (
                    str(referencia.peso_bruto) if referencia.peso_bruto is not None else '0'
                ),
                'contenedor': contenedor.num_cont,
                'cliente': referencia.nombre_cliente,
                'num_pedimento': referencia.num_pedimento,
                'num_doda': doda.num_doda,
                'fecha_doda': doda.fecha_doda.strftime('%Y-%m-%d') if doda.fecha_doda else '',
                # Clave de idempotencia estable para que BitacoraKasu pueda
                # distinguir un reenvío genuino de un duplicado: el retry
                # ocurre a nivel de DODA completa (reintentar_envio), así
                # que si 1 de 5 contenedores falla, los 5 se re-postean.
                'idempotency_key': f'{doda.id_doda}:{contenedor.num_cont}',
            }
            try:
                respuesta = enviar_modulacion(payload)
                enviados += 1
                url = respuesta.get('completar_datos_url')
                if url:
                    links[contenedor.num_cont] = url
            except BitacoraKasuError as e:
                fallidos.append(f'{contenedor.num_cont}: {e}')
                logger.error(
                    '[Modulacion] Error push contenedor %s (DODA %s) a BitacoraKasu: %s',
                    contenedor.num_cont, doda.num_doda, e,
                )

    if links:
        envio.links_completar = {**envio.links_completar, **links}

    if fallidos:
        _registrar_error(envio, 'push_estado', 'push: ' + '; '.join(fallidos))
        return False
    if enviados == 0:
        _registrar_error(envio, 'push_estado', 'push: sin contenedores para enviar')
        return False

    envio.push_estado = 'ENVIADO'
    logger.info('[Modulacion] Push BitacoraKasu DODA %s: %s contenedor(es) enviados',
                doda.num_doda, enviados)
    return True
```

(El único cambio real: `enviar_modulacion(payload)` ahora se captura en `respuesta` en vez
de descartarse, se extrae `completar_datos_url` si viene, y hay un merge de `links` sobre
`envio.links_completar` antes de los `return` existentes. El resto de la función es
idéntico al actual.)

- [ ] **Step 4: Correr las pruebas para verificar que pasan**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias.test_modulacion.ProcesarDodasNuevasTests -v 2`
Expected: PASS (todos, incluidos los 3 nuevos).

- [ ] **Step 5: Correr toda la suite**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add referencias/modulacion.py referencias/test_modulacion.py
git commit -m "feat(modulacion): _push_bitacorakasu captura completar_datos_url por contenedor"
```

---

### Task 3: Reordenar push antes que email

Depende de: Task 2 (`_push_bitacorakasu` ya puebla `envio.links_completar`).

**Files:**
- Modify: `referencias/modulacion.py`
- Test: `referencias/test_modulacion.py`

**Interfaces:**
- Consumes: `_procesar_push(doda, envio) -> bool`, `_procesar_email(doda, envio) -> bool`
  (ambas ya existentes, sin cambio de firma).
- Produces: dentro de `_procesar_doda` y `reintentar_envio`, `_procesar_push` se invoca
  **antes** que `_procesar_email` (antes era al revés). El resto del comportamiento de
  ambas funciones (qué se reintenta, qué actualiza `update_fields`, los `return` de
  `reintentar_envio`) no cambia.

- [ ] **Step 1: Escribir las pruebas que fallan**

Agregar a `referencias/test_modulacion.py` una nueva clase, después de
`ProcesarDodasNuevasTests` (usa `_doda`, ya definido como fixture de módulo):

```python
class OrdenPushAntesQueEmailTests(TestCase):
    def test_procesar_doda_llama_push_antes_que_email(self):
        from .modulacion import _procesar_doda
        doda = _doda()
        orden = []

        def _push(d, e):
            orden.append('push')
            return True

        def _email(d, e):
            orden.append('email')
            return True

        with patch('referencias.modulacion._procesar_push', side_effect=_push), \
             patch('referencias.modulacion._procesar_email', side_effect=_email):
            _procesar_doda(doda)

        self.assertEqual(orden, ['push', 'email'])

    def test_reintentar_envio_llama_push_antes_que_email(self):
        from .modulacion import reintentar_envio
        doda = _doda()
        envio = EnvioModulacion.objects.create(doda=doda)
        orden = []

        def _push(d, e):
            orden.append('push')
            return True

        def _email(d, e):
            orden.append('email')
            return True

        with patch('referencias.modulacion._procesar_push', side_effect=_push), \
             patch('referencias.modulacion._procesar_email', side_effect=_email):
            reintentar_envio(envio)

        self.assertEqual(orden, ['push', 'email'])
```

- [ ] **Step 2: Correr las pruebas para verificar que fallan**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias.test_modulacion.OrdenPushAntesQueEmailTests -v 2`
Expected: FAIL en ambos tests con `AssertionError: ['email', 'push'] != ['push', 'email']`
(el orden actual es email→push).

- [ ] **Step 3: Reordenar `_procesar_doda`**

En `referencias/modulacion.py`, reemplazar el cuerpo de `_procesar_doda` (líneas actuales
223-240):

```python
def _procesar_doda(doda, solo_push=False):
    envio = EnvioModulacion.objects.create(doda=doda)

    if _procesar_push(doda, envio):
        doda.modulacion_enviada_en = timezone.now()

    # El email se procesa después del push para poder incluir, en su
    # contenido, los links de envio.links_completar que el push acaba de
    # recolectar (ver _enviar_email_modulacion).
    if not solo_push and _procesar_email(doda, envio):
        doda.notificado_en = timezone.now()

    envio.save()

    update_fields = [
        campo for campo in ('notificado_en', 'modulacion_enviada_en')
        if getattr(doda, campo) is not None
    ]
    if update_fields:
        doda.save(update_fields=update_fields)
```

- [ ] **Step 4: Reordenar `reintentar_envio`**

En la misma función (líneas actuales 261-276), reemplazar el bloque:

```python
    doda = envio.doda
    update_fields = []

    if envio.push_estado != 'ENVIADO':
        if _procesar_push(doda, envio):
            doda.modulacion_enviada_en = timezone.now()
            update_fields.append('modulacion_enviada_en')

    # El email se procesa después del push — ver nota en _procesar_doda.
    if not solo_push and envio.email_estado != 'ENVIADO':
        if _procesar_email(doda, envio):
            doda.notificado_en = timezone.now()
            update_fields.append('notificado_en')

    envio.save()
    if update_fields:
        doda.save(update_fields=update_fields)
```

(No tocar nada después de este bloque — los `return` finales de `reintentar_envio` quedan
igual.)

- [ ] **Step 5: Correr las pruebas para verificar que pasan**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias.test_modulacion.OrdenPushAntesQueEmailTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 6: Correr toda la suite**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias -v 2`
Expected: PASS — presta atención en particular a
`ReintentarModulacionSoloPushTests`/`ReintentarModulacionCommandTests`, que ejercitan
`reintentar_envio` con distintas combinaciones de estado; el reordenamiento no debe
cambiar ningún resultado de esos tests (siguen en verde porque el orden entre `push` y
`email` no afecta qué se reintenta ni el valor final de los estados, sólo la secuencia).

- [ ] **Step 7: Commit**

```bash
git add referencias/modulacion.py referencias/test_modulacion.py
git commit -m "refactor(modulacion): procesa push antes que email para poder incluir sus links"
```

---

### Task 4: El correo incluye los links de `envio.links_completar`

Depende de: Task 2 (`links_completar` poblado), Task 3 (push ya corrió antes que el email
dentro de `_procesar_doda`/`reintentar_envio`, así que `envio.links_completar` ya está
listo cuando `_enviar_email_modulacion` se ejecuta).

**Files:**
- Modify: `referencias/modulacion.py`
- Test: `referencias/test_modulacion.py`

**Interfaces:**
- Consumes: `envio.links_completar` (dict, Task 1/2).
- Produces: el `html_content` del correo que arma `_enviar_email_modulacion` incluye un
  `<a href="...">` por cada entrada de `envio.links_completar`, ordenados por número de
  contenedor. Firma de `_enviar_email_modulacion` sin cambios.

- [ ] **Step 1: Escribir las pruebas que fallan**

Agregar a `referencias/test_modulacion.py`, dentro de `ProcesarDodasNuevasTests` (reusa
`self.doda`, `_resp_sendgrid`):

```python
    def test_email_incluye_boton_por_cada_link_completar(self):
        from .modulacion import _enviar_email_modulacion

        envio = EnvioModulacion.objects.create(
            doda=self.doda,
            links_completar={
                'HLXU1234567': 'https://bitacora.test/modulacion/completar/tok1/',
                'TCLU7654321': 'https://bitacora.test/modulacion/completar/tok2/',
            },
        )

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            _enviar_email_modulacion(self.doda, ('capt@example.com', 'Capturista'), envio)

        html = sg_cls.return_value.send.call_args[0][0].get()['content'][0]['value']
        self.assertIn('https://bitacora.test/modulacion/completar/tok1/', html)
        self.assertIn('https://bitacora.test/modulacion/completar/tok2/', html)
        self.assertIn('HLXU1234567', html)
        self.assertIn('TCLU7654321', html)

    def test_email_sin_links_completar_no_incluye_botones(self):
        from .modulacion import _enviar_email_modulacion

        envio = EnvioModulacion.objects.create(doda=self.doda)  # links_completar={} por default

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls:
            sg_cls.return_value.send.return_value = _resp_sendgrid()
            _enviar_email_modulacion(self.doda, ('capt@example.com', 'Capturista'), envio)

        html = sg_cls.return_value.send.call_args[0][0].get()['content'][0]['value']
        self.assertNotIn('Completar carril', html)

    def test_flujo_completo_email_incluye_link_del_contenedor_con_url(self):
        """Integra Task 2 + 3 + 4: procesar_dodas_nuevas de punta a punta deja,
        en el correo real que se manda, el link del contenedor cuya terminal
        lo requería (y ninguno para el que no)."""
        from .modulacion import procesar_dodas_nuevas

        def _post_side_effect(url, json=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            if json['contenedor'] == 'HLXU1234567':
                data = {'status': 'ok', 'completar_datos_url':
                        'https://bitacora.test/modulacion/completar/tok1/'}
            else:
                data = {'status': 'ok'}
            resp.json.return_value = data
            return resp

        with patch('referencias.modulacion.SendGridAPIClient') as sg_cls, \
             patch('referencias.bitacorakasu_client.requests.post',
                   side_effect=_post_side_effect):
            sg_cls.return_value.send.return_value = _resp_sendgrid()

            procesar_dodas_nuevas([self.doda])

        html = sg_cls.return_value.send.call_args[0][0].get()['content'][0]['value']
        self.assertIn('https://bitacora.test/modulacion/completar/tok1/', html)
        self.assertNotIn('TCLU7654321', html)  # ese contenedor no trajo link
```

- [ ] **Step 2: Correr las pruebas para verificar que fallan**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias.test_modulacion.ProcesarDodasNuevasTests -v 2`
Expected: `test_email_incluye_boton_por_cada_link_completar` y
`test_flujo_completo_email_incluye_link_del_contenedor_con_url` FALLAN (`assertIn` de la
URL no la encuentra en el html); `test_email_sin_links_completar_no_incluye_botones` puede
pasar de casualidad (ya no hay botones hoy) — no es problema.

- [ ] **Step 3: Implementar los botones en `_enviar_email_modulacion`**

En `referencias/modulacion.py`, dentro de `_enviar_email_modulacion` (líneas actuales
86-132), agregar la construcción de `botones` justo después de la línea
`nombre_pdf = f"DODA_{(doda.num_doda or str(doda.id_doda)).replace('/', '-')}.pdf"` y antes
de `mensaje = Mail(`:

```python
        botones = ''.join(
            f'<p><a href="{url}">Completar carril y horarios de terminal — '
            f'contenedor {num_cont}</a></p>'
            for num_cont, url in sorted(envio.links_completar.items())
        )
```

Y reemplazar el `html_content=(...)` de `Mail(...)` (que hoy es):

```python
            html_content=(
                f'<p>Estimado(a) {nombre},</p>'
                f'<p>Se generó la DODA <strong>{doda.num_doda}</strong> en la terminal '
                f'<strong>{doda.terminal_nombre}</strong>. Favor de iniciar la solicitud '
                f'de extracción del contenedor.</p>'
                f'<p>Se adjunta el pedimento + DODA para imprimir.</p>'
                f'<p>{settings.NOMBRE_AGENCIA}</p>'
            ),
```

por:

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

- [ ] **Step 4: Correr las pruebas para verificar que pasan**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias.test_modulacion.ProcesarDodasNuevasTests -v 2`
Expected: PASS (todos, incluidos los 3 nuevos).

- [ ] **Step 5: Correr toda la suite completa**

Run: `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias -v 2`
Expected: PASS. Verificar también `test_email_adjunta_pdf` (ya existente) sigue en verde —
el PDF adjunto no se ve afectado por este cambio.

- [ ] **Step 6: Verificación final de migraciones**

Run: `python manage.py makemigrations --check`
Expected: sin cambios pendientes (este task no toca modelos).

- [ ] **Step 7: Commit**

```bash
git add referencias/modulacion.py referencias/test_modulacion.py
git commit -m "feat(modulacion): el correo incluye un link por contenedor con completar_datos_url"
```

---

## Orden de implementación

Task 1 → Task 2 → Task 3 → Task 4 (estrictamente secuencial). Al terminar Task 4, correr
una vez más `DBURL=sqlite:///$(pwd)/db.sqlite3 python manage.py test referencias` completo
antes de considerar el trabajo terminado.

## Fuera de alcance de este plan

El lado de BitacoraKasu (modelo, catálogo de terminales, endpoint que genera
`completar_datos_url`, formulario público) ya está implementado y fusionado a `main` en ese
repo — ver `docs/superpowers/specs/2026-09-05-datos-terminal-modulacion-design.md`
(commiteado en ambos repos) para el contrato compartido.
