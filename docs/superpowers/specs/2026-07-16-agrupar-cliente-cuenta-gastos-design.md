# Agrupar por cliente — Cuenta de Gastos (pestaña Pendientes)

**Fecha:** 2026-07-16
**Estado:** Aprobado por el usuario

## Objetivo

La pestaña "Pendientes" de `/cuenta-gastos/` lista hasta 22,000+ referencias
en una tabla plana ordenada por fecha de pago, repitiendo el nombre del
cliente en cada fila. El objetivo es agrupar visualmente las referencias por
cliente para que sea más fácil ubicar y trabajar las de un cliente en
particular.

Alcance: solo la pestaña **Pendientes**. La pestaña Finalizadas no cambia.

## Orden

`referencias/views.py::cuenta_gastos` (rama `else`, pendientes), la consulta
cambia de:

```python
qs = qs.order_by('-fecha_pago')
```

a:

```python
qs = qs.order_by('nombre_cliente', '-fecha_pago')
```

Cliente primero (alfabético, blancos primero), fecha de pago descendente
dentro de cada cliente.

## Agrupado (vista)

Después de paginar (50 filas/página, sin cambios), se agrupan las
referencias **de esa página** por `nombre_cliente` con `itertools.groupby`
(ya vienen ordenadas por cliente, así que agrupar la página ya paginada es
directo, sin queries adicionales):

```python
import itertools
...
grupos_cliente = [
    {'nombre_cliente': nombre, 'referencias': list(refs)}
    for nombre, refs in itertools.groupby(pagina.object_list, key=lambda r: r.nombre_cliente)
]
```

Se agrega `'grupos_cliente': grupos_cliente` al contexto de la rama
pendientes, junto al `'page'` ya existente (el template usa `grupos_cliente`
para la tabla y `page` para la paginación, igual que hoy).

Un cliente con muchas referencias pendientes puede quedar cortado entre dos
páginas; en ese caso su grupo simplemente reaparece con el mismo encabezado
en la página siguiente. No se ajusta el tamaño de página para evitarlo.

## Plantilla

En `templates/referencias/cuenta_gastos.html`, el bloque de la tabla de
Pendientes (hoy una sola tabla con `{% for ref in page.object_list %}`) se
reemplaza por un `{% for grupo in grupos_cliente %}` que renderiza, por cada
cliente, un `<details>` nativo del navegador (sin JavaScript, **contraído
por defecto** — sin atributo `open`):

- `<summary>`: nombre del cliente (o "Sin cliente" si `nombre_cliente` está
  vacío) + conteo de referencias del grupo.
- Adentro: una tabla con las mismas columnas de hoy (Referencia, F. Pago,
  Contenedores, BL, Acción) **sin la columna Cliente**, ya que el nombre
  queda en el encabezado del grupo. El botón "Finalizar" no cambia — sigue
  siendo una acción por referencia, no hay acción masiva por cliente en esta
  iteración.

La paginación al pie de la tabla no cambia (sigue usando `page`).

## Fuera de alcance

- Acción "Finalizar todas" por cliente (puede pedirse después).
- Evitar que un cliente quede cortado entre páginas.
- Cambios a la pestaña Finalizadas o al dashboard.

## Pruebas

- La vista ordena por `nombre_cliente` antes que por `fecha_pago` (rama
  pendientes).
- `grupos_cliente` en el contexto agrupa correctamente referencias
  consecutivas del mismo cliente dentro de una página; dos clientes
  distintos producen dos grupos separados.
- El template renderiza un `<details>` por grupo, contraído por defecto
  (sin atributo `open`), con el nombre del cliente y el conteo correcto de
  referencias en el `<summary>`.
- La columna Cliente ya no aparece en las filas de la tabla agrupada.
- El botón "Finalizar" sigue funcionando igual (queda dentro del `<details>`
  de su cliente).
- La pestaña Finalizadas no se modifica (regresión).
