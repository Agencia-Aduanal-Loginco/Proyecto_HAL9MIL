GRUPO_GLOSADOR = 'Glosador'


def es_glosador(user):
    """Visto bueno, rechazo y reapertura: grupo Glosador (o superusuario)."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=GRUPO_GLOSADOR).exists()
