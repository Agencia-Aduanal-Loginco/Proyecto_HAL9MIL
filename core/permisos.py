from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def usuario_tiene_modulo(user, nombre_modulo):
    """True si `user` puede acceder al módulo `nombre_modulo`.

    Un superusuario siempre tiene acceso. Cualquier otro usuario necesita
    pertenecer a un Group de Django cuyo `name` sea exactamente `nombre_modulo`.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=nombre_modulo).exists()


def modulo_required(nombre_modulo):
    """Decorator para vistas: exige sesión iniciada y acceso al módulo
    `nombre_modulo` (ver `usuario_tiene_modulo`). Si no cumple, redirige a
    'dashboard' con un mensaje de error."""
    def decorador(vista):
        @login_required
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            if not usuario_tiene_modulo(request.user, nombre_modulo):
                messages.error(request, 'No tienes permiso para acceder a este módulo.')
                return redirect('dashboard')
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador
