from django import template

register = template.Library()

@register.filter
def find_fuente(fuentes_list, fuente_name):
    if not isinstance(fuentes_list, list):
        return None
    for f in fuentes_list:
        if isinstance(f, dict) and f.get('fuente') == fuente_name:
            return f
    return None

@register.filter
def split(value, arg):
    return value.split(arg)
