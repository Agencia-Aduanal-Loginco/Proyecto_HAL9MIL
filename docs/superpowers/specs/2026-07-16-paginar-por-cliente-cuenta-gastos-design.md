# Paginar por cliente — Cuenta de Gastos (pestaña Pendientes)

**Fecha:** 2026-07-16
**Estado:** Aprobado por el usuario

## Objetivo

Tras agrupar por cliente (spec `2026-07-16-agrupar-cliente-cuenta-gastos-design.md`),
la paginación sigue siendo de 50 **referencias** por página, así que cada
página solo alcanza a mostrar 2-3 clientes completos (ej. producción real:
"Página 1 de 442" con solo 3 clientes visibles). El objetivo es paginar por
**50 clientes** por página en vez de por 50 referencias, para que cada
página muestre siempre 50 grupos de cliente completos.

Alcance: solo la pestaña Pendientes (misma que el spec anterior).

## Cambio en la vista

`referencias/views.py::cuenta_gastos`, rama pendientes. Reemplaza el
`Paginator(qs, 50)` actual (que pagina objetos `Referencia`) por un
paginador sobre la lista de **nombres de cliente distintos**:

```python
clientes_distintos = list(
    qs.values_list('nombre_cliente', flat=True).distinct().order_by('nombre_cliente')
)
paginador = Paginator(clientes_distintos, 50)
pagina    = paginador.get_page(request.GET.get('page', 1))

referencias_pagina = (
    qs.filter(nombre_cliente__in=list(pagina.object_list))
    .order_by('nombre_cliente', '-fecha_pago')
)
grupos_cliente = [
    {'nombre_cliente': nombre, 'referencias': list(refs)}
    for nombre, refs in itertools.groupby(
        referencias_pagina, key=lambda r: r.nombre_cliente
    )
]
```

`qs` ya viene filtrada (q, patente, año, mes) y ordenada (`nombre_cliente`,
`-fecha_pago`) por el spec anterior — se reutiliza tal cual como base tanto
para la lista de clientes distintos como para las referencias de la página.

El header de arriba ("X referencias pendientes de finalizar") sigue usando
`qs.count()`, sin cambio. El pie de página ("Página X de Y") sigue usando
`page` (ahora un `Page` de clientes), y el texto del conteo pasa de
"{{ total }} registros" a "{{ page.paginator.count }} clientes" — no
requiere una variable de contexto nueva, `page.paginator.count` ya es el
total de clientes distintos paginados.

## Fuera de alcance

- Cambios a la pestaña Finalizadas.
- Cambios al tamaño de página (sigue siendo 50).
- Acciones masivas por cliente.

## Pruebas

- Con más de 50 clientes distintos en la BD, la página 1 muestra exactamente
  50 grupos de cliente (no cortados) y la página 2 continúa con el
  cliente 51 en adelante.
- El número total de páginas corresponde a `ceil(clientes_distintos / 50)`,
  no a `ceil(referencias / 50)`.
- Los filtros (q, patente, año, mes) siguen aplicando correctamente sobre
  la lista de clientes distintos y sus referencias.
- El pie de página muestra "... · N clientes" en vez de "... · N registros".
- La pestaña Finalizadas no se modifica (regresión).
