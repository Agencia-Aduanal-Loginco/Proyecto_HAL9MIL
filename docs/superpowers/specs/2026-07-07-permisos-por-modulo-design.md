# Permisos por módulo basados en Grupos (piloto: Finanzas)

## Contexto

Hoy el acceso a módulos sensibles (Clientes, SLA Capturistas, Finanzas) se controla
en el sidebar (`templates/base.html`) con `{% if request.user.is_superuser %}`, y
no hay ninguna verificación a nivel de vista — un usuario que conozca la URL puede
entrar a Finanzas aunque el link esté oculto.

Se quiere reemplazar esto por un control basado en `Group` de Django, reutilizable
para cualquier módulo futuro (Referencias, Glosa, Clientes, SLA, Cuenta de Gastos),
empezando por Finanzas como piloto.

## Objetivo

- Ocultar el módulo de Finanzas del sidebar para usuarios sin permiso.
- Bloquear también el acceso directo por URL a las vistas de Finanzas.
- Que el mecanismo sea trivial de extender a otros módulos más adelante.
- Los superusuarios conservan acceso total automático a todos los módulos.
- El control es "todo o nada" por módulo (no hay permisos finos dentro de Finanzas).

## Diseño

### Nueva app `core`

Vive fuera de cualquier módulo existente porque el mecanismo debe ser consumido por
todos los módulos, no solo por `finanzas`.

- **`core/permisos.py`**
  - `usuario_tiene_modulo(user, nombre_modulo: str) -> bool`: `True` si
    `user.is_superuser` o `user.groups.filter(name=nombre_modulo).exists()`.
  - `modulo_required(nombre_modulo: str)`: decorator para vistas. Exige sesión
    iniciada (equivalente a `login_required`), valida `usuario_tiene_modulo`, y si
    falla hace `messages.error(request, 'No tienes permiso para acceder a este módulo.')`
    y redirige a `dashboard`.

- **`core/templatetags/permisos_tags.py`**
  - Filtro `tiene_modulo`: `{{ request.user|tiene_modulo:'Finanzas' }}`, usado en
    el sidebar para decidir si se muestra el link.

- **Migración de datos** en `core` que crea (con `get_or_create`) el `Group`
  llamado `"Finanzas"` si no existe, para que el grupo esté disponible en
  cualquier entorno (dev, prod, clones como Reiki) sin pasos manuales.

`core` se agrega a `INSTALLED_APPS`.

### Aplicación en Finanzas (piloto)

- Cada vista de `finanzas/views.py` que hoy tiene `@login_required` gana además
  `@modulo_required('Finanzas')`.
- En `templates/base.html`, el link de Finanzas cambia de
  `{% if request.user.is_superuser %}` a `{% if request.user|tiene_modulo:'Finanzas' %}`.

### Gestión de acceso

Sin UI nueva: se usa el admin de Django ya existente
(`/admin/auth/group/`) para agregar/quitar usuarios del grupo "Finanzas".

### Fuera de alcance

- Clientes y SLA Capturistas se quedan tal cual (`is_superuser`) por ahora.
  Migrarlos después es: crear su `Group`, decorar sus vistas con
  `@modulo_required('NombreModulo')`, y cambiar su `{% if %}` en el sidebar.
- No hay permisos finos dentro de un módulo (ej. ver vs aprobar en Finanzas).
- No hay UI custom de administración de accesos; se usa el admin de Django.

## Testing

- Test unitario de `usuario_tiene_modulo` (superuser, miembro del grupo, ninguno
  de los dos).
- Test de vista: usuario sin grupo golpea una URL de Finanzas → redirige a
  `dashboard` con mensaje de error, no ve el contenido.
- Test de vista: usuario en el grupo "Finanzas" → accede normalmente.
- Verificación manual del sidebar con un usuario staff sin el grupo (no debe
  ver el link) y con el grupo (debe verlo).
