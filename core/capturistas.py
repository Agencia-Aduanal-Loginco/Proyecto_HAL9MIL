import logging

from django.conf import settings

from core.models import PerfilUsuario

logger = logging.getLogger(__name__)


def resolver_destinatario(cve_capt: str) -> tuple[str, str] | None:
    """
    Devuelve (email, nombre) del capturista, o None si no hay perfil vinculado.

    Lógica:
    1. Busca PerfilUsuario por cve_capturista
    2. Si existe: devuelve (email_alterno o user.email, nombre completo o username)
    3. Si no existe: loguea warning y devuelve:
       - None si MODULACION_FALLBACK_EMAILS está vacío
       - (primer fallback email, cve_capt) si hay fallbacks configurados
    """
    try:
        perfil = PerfilUsuario.objects.select_related('user').get(cve_capturista=cve_capt)
        email = perfil.email_alterno or perfil.user.email
        nombre = perfil.user.get_full_name() or perfil.user.username
        return (email, nombre)
    except PerfilUsuario.DoesNotExist:
        logger.warning(f'No hay PerfilUsuario vinculado para cve_capturista={cve_capt}')

        fallbacks = settings.MODULACION_FALLBACK_EMAILS
        if not fallbacks:
            return None

        # Si hay fallbacks configurados, devuelve el primero con el cve_capt como nombre
        return (fallbacks[0], cve_capt)
