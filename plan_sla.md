# Plan: Dashboard SLA por Capturista

## Contexto

Con 26,234 referencias que tienen capturista registrado y 28 capturistas únicos en la base de
datos, existe suficiente señal histórica para medir calidad operativa por persona.
La idea es responder: ¿quién captura más rápido? ¿quién produce más rectificaciones?
¿quién tiene mayor tasa de glosas en sus referencias?

---

## Métricas a calcular por capturista

| Métrica | Cálculo | Fuente |
|---|---|---|
| Total referencias | COUNT | `Referencia.nombre_capturista` |
| Avg días Cap→Validación | AVG(fecha_validacion - fecha_captura) | fechas en modelo |
| Avg días Cap→Pago | AVG(fecha_pago - fecha_captura) | fechas en modelo |
| % Rectificaciones | refs con es_rectificacion / total | flag en modelo |
| % Con glosa | refs con glosa / total | `GlosaRegistro` |
| Referencias último mes | COUNT filtrado por mes | fecha_captura |
| Tendencia (12 meses) | array mensual de volumen | para sparkline |

---

## Arquitectura propuesta

### 1. Vista nueva en `referencias/views.py`

```python
@login_required
def sla_capturistas(request):
    # Parámetros de filtro opcionales
    year  = int(request.GET.get('año', now.year))
    month = int(request.GET.get('mes', now.month))  # 0 = todo el año

    # Query principal: una sola pasada por capturista
    qs = (
        Referencia.objects
        .filter(nombre_capturista__gt='', fecha_pago__isnull=False)
        .exclude(es_rectificacion=True)
    )
    # Agrupar por capturista con anotaciones de conteo y promedios
    # Glosas: subquery o segundo query con dict lookup
    # Rectificaciones: segundo query contando es_rectificacion=True por capturista
```

### 2. URL

```python
# referencias/urls.py
path('sla/capturistas/', views.sla_capturistas, name='sla_capturistas'),
```

### 3. Template `referencias/sla_capturistas.html`

Estructura de la página:

```
┌─────────────────────────────────────────────────────┐
│  SLA por Capturista          [Año ▾] [Mes ▾]        │
├─────────────────────────────────────────────────────┤
│  Tarjetas resumen (top del equipo)                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ Más rápido│ │Más volumen│ │Menor riesgo│          │
│  └──────────┘ └──────────┘ └──────────┘            │
├─────────────────────────────────────────────────────┤
│  Tabla comparativa                                  │
│  Capturista │ Refs │ Avg C→V │ Avg C→P │ %Rect │ 🔴 │
│  ─────────────────────────────────────────────────  │
│  García J.  │  312 │  1.2d   │  4.8d   │  1%   │ 3% │
│  López M.   │  198 │  2.1d   │  6.3d   │  3%   │ 8% │
│  ...                                               │
├─────────────────────────────────────────────────────┤
│  Sparkline de volumen mensual por capturista        │
│  (Chart.js — barras apiladas o líneas múltiples)   │
└─────────────────────────────────────────────────────┘
```

### 4. Sidebar

Agregar enlace bajo "Referencias" o en sección "Análisis":

```html
{% if request.user.is_superuser %}
<a href="{% url 'sla_capturistas' %}" class="sidebar-link ...">
  SLA Capturistas
</a>
{% endif %}
```

---

## Lógica de semáforo en tabla

Aplicar los mismos umbrales del semáforo de clientes:

| Columna | Verde | Amarillo | Rojo |
|---|---|---|---|
| Avg Cap→Pago | < 5 días | 5–10 días | > 10 días |
| % Rectificaciones | < 2% | 2–5% | > 5% |
| % Con glosa | < 5% | 5–15% | > 15% |

---

## Orden de implementación

- [ ] **Paso 1** — Vista `sla_capturistas` con query base (conteos y promedios)
- [ ] **Paso 2** — Cruzar datos de glosas y rectificaciones por capturista
- [ ] **Paso 3** — Template: tarjetas resumen + tabla con semáforos
- [ ] **Paso 4** — Gráfica de tendencia mensual (Chart.js inline)
- [ ] **Paso 5** — Filtros por año/mes y enlace en sidebar (solo superusuario)
- [ ] **Paso 6** — Prueba con datos reales de producción

---

## Consideraciones técnicas

- **Rendimiento**: hacer el cruce de glosas con un segundo `values/annotate` y un dict lookup,
  no con N+1 queries por capturista.
- **Nombres**: `nombre_capturista` es texto libre; normalizar con `.strip()` al agrupar para
  evitar duplicados por espacios.
- **Privacidad**: limitar la vista a superusuarios (`@user_passes_test`) dado que expone
  métricas individuales de desempeño.
- **Fechas nulas**: excluir referencias sin `fecha_captura` o `fecha_pago` de los promedios
  de tiempo; sí contarlas en el total de volumen.

---

## Datos disponibles (al 10 jun 2026)

- 26,234 referencias con capturista registrado
- 28 capturistas únicos
- Rango histórico: ago 2017 → hoy
- Años con mayor volumen: 2024 (6,373 refs), 2025 (5,441 refs)
