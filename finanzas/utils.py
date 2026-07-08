from .models import ConfiguracionFiscal


def get_configuracion_fiscal(patente: str) -> ConfiguracionFiscal:
    """
    Retorna la ConfiguracionFiscal activa para la patente dada.
    Lanza ValueError si no existe o está inactiva — nunca retorna None.
    Usar siempre esta función en lugar de ORM directo antes de generar cualquier CFDI.
    """
    try:
        return ConfiguracionFiscal.objects.get(patente=patente, activa=True)
    except ConfiguracionFiscal.DoesNotExist:
        raise ValueError(
            f'Sin ConfiguracionFiscal activa para patente {patente}. '
            f'Ejecutar: python manage.py cargar_configs_fiscales'
        )
