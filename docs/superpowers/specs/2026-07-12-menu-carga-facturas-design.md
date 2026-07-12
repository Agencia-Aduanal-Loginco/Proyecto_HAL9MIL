# Carga de Facturas Abierta a Todos los Usuarios — Diseño

**Fecha:** 2026-07-12
**Estado:** Aprobado por el usuario (brainstorming 2026-07-12)

## Problema

La vista `finanzas.carga_xml_cliente` (implementada en el plan
`2026-07-11-carga-facturas-cliente.md`, ya mergeada a `main`) permite subir
XML+PDF de facturas de cliente, pero hoy exige pertenecer al grupo
`Finanzas` (`@modulo_required('Finanzas')`) y solo es alcanzable desde dentro
del dashboard de Finanzas. El usuario quiere que **cualquier persona con
cuenta** pueda subir sus facturas, y que el personal de Finanzas siga siendo
quien las integra a la cuenta de gastos (paso ya existente: `xml_pendientes`).

## Decisiones tomadas (con el usuario)

| Decisión | Elección |
|----------|----------|
| Permisos de la vista de carga | `@login_required` (cualquier usuario logueado), igual que Dashboard/Referencias/Glosa/Cuenta de Gastos |
| Etiqueta y ubicación en el menú | "Carga de Facturas", después del bloque de Clientes/SLA Capturistas (visualmente), pero **fuera** de su `{% if is_superuser %}` — visible a todos |
| Pantalla de resultado tras subir | Se reutiliza tal cual (conteos + tabla), sin crear una versión simplificada |
| Tarjeta "Facturas de cliente" en el dashboard de Finanzas | Se conserva, como acceso adicional para el personal ya dentro de Finanzas |

## Arquitectura

Cambio acotado sobre código ya existente — sin modelos, sin migraciones, sin
tests nuevos de lógica de negocio (el pipeline de carga no cambia).

### Componente 1: Relajar el permiso de la vista

`finanzas/views.py`: `carga_xml_cliente` cambia su decorador de
`@modulo_required('Finanzas')` a `@login_required` (requiere agregar el
import `from django.contrib.auth.decorators import login_required`, no
presente hoy en el archivo). La URL, el nombre (`finanzas:carga_xml_cliente`)
y la app que la aloja no cambian.

### Componente 2: Nueva entrada de menú

`templates/base.html`: nuevo `<a>` en el `<nav>` del sidebar, insertado
inmediatamente después del `{% endif %}` que cierra el bloque
`{% if request.user.is_superuser %}` (que contiene Clientes y SLA
Capturistas) y antes del enlace "Cuenta de Gastos" — sin envolverlo en
ningún `{% if %}`, para que sea visible a cualquier usuario autenticado.
Ícono SVG de subida de documento (outline, mismo estilo `w-4 h-4
stroke="currentColor"` que el resto de los íconos del sidebar). Estado
activo (`class="active"`) cuando
`request.resolver_match.url_name == 'carga_xml_cliente'`.

### Componente 3: Reparar 2 enlaces internos rotos para usuarios sin acceso a Finanzas

Al abrir la vista de carga a cualquier usuario, dos enlaces existentes que
apuntan a páginas exclusivas de Finanzas quedarían rotos (redirigen con
"No tienes permiso") para quien no sea del grupo Finanzas:

1. `templates/finanzas/carga_cliente_form.html`:
   - El breadcrumb `← Finanzas` (`{% url 'finanzas:dashboard' %}`) cambia a
     `← Inicio` (`{% url 'dashboard' %}` — la vista de dashboard general,
     accesible a todos).
   - El párrafo que menciona "XMLs pendientes" con enlace a
     `finanzas:xml_pendientes` se envuelve en
     `{% if request.user|tiene_modulo:'Finanzas' %}`; para el resto de los
     usuarios se muestra un texto sin enlace indicando que el equipo de
     Finanzas revisará e integrará la factura.
2. `templates/finanzas/carga_masiva_resultado.html`:
   - El enlace `Asignar los pendientes →` (`finanzas:xml_pendientes`) se
     envuelve en `{% if request.user|tiene_modulo:'Finanzas' %}`. El resto
     de la pantalla (conteos, tabla de resultados por archivo) no cambia
     para nadie — cumple la decisión de "misma pantalla para todos" sin
     dejar un enlace que truena.

Este mismo template (`carga_masiva_resultado.html`) también es usado por
`carga_masiva_xml` (la carga masiva de proveedores LCT/APM, que sigue siendo
exclusiva de Finanzas) — el `{% if %}` no le afecta, ya que quien llega ahí
siempre tiene el módulo Finanzas.

### Fuera de alcance

- `xml_pendientes` (integración a gasto) sigue exclusivo de Finanzas, sin
  cambios.
- No se toca el pipeline de carga (`carga_xml.py`), el modelo
  `XMLProveedor`, ni el storage.
- No se crea una pantalla de resultado simplificada — decisión explícita del
  usuario.

## Pruebas

- `carga_xml_cliente` responde 200 (GET) para un usuario autenticado **sin**
  el grupo Finanzas (antes: 302 redirect por falta de permiso).
- El formulario, para ese mismo usuario, NO contiene el texto/enlace hacia
  `xml_pendientes`.
- Para un usuario **con** el grupo Finanzas, el formulario SÍ contiene el
  enlace a `xml_pendientes` (regresión del comportamiento actual).
- `carga_masiva_resultado.html`, con `conteos.pendientes > 0`: usuario sin
  Finanzas no ve "Asignar los pendientes →"; usuario con Finanzas sí lo ve
  (regresión).
- El nuevo `<a>` del sidebar aparece en el HTML de cualquier página para un
  usuario autenticado cualquiera (sin necesidad de grupo ni superusuario).
