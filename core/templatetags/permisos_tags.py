from django import template

from core.permisos import usuario_tiene_modulo

register = template.Library()


@register.filter(name='tiene_modulo')
def tiene_modulo(user, nombre_modulo):
    return usuario_tiene_modulo(user, nombre_modulo)
