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
