"""
Motor de cálculo de comisiones por referencia.

La tasa se lee desde la variable de entorno COMISION_TASA (decimal, p.ej. 0.05 = 5%).
Si no está definida se usa la constante TASA_DEFAULT.

Por diseño actual hay una tasa global. Cuando se requiera tasa por cliente
o por agente, agregar un modelo TasaComision y consultarlo aquí.
"""
import logging
import os
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Sum

from .models import ComisionReferencia, GastoReferencia

logger = logging.getLogger(__name__)

TASA_DEFAULT = Decimal('0.0500')   # 5% — sobreescribible con COMISION_TASA en .env


def _tasa_global() -> Decimal:
    raw = os.environ.get('COMISION_TASA', '').strip()
    if raw:
        try:
            return Decimal(raw)
        except Exception:
            logger.warning('COMISION_TASA inválida (%r), usando default %s', raw, TASA_DEFAULT)
    return TASA_DEFAULT


def calcular_comisiones_mes(mes: int, anio: int) -> list[ComisionReferencia]:
    """
    Calcula/actualiza comisiones para todas las referencias con gastos en el periodo.

    - valor_operacion = suma de GastoReferencia.monto del mes/año
    - tasa_comision   = COMISION_TASA (env) o TASA_DEFAULT
    - monto_comision  = valor_operacion × tasa_comision
    - agente          = referencia.registrado_por si existe, else None

    Usa update_or_create por referencia — se puede recalcular sin duplicar.
    Retorna la lista de instancias ComisionReferencia creadas/actualizadas.
    """
    User = get_user_model()
    tasa = _tasa_global()

    # Agrupar gastos del periodo por referencia
    gastos_por_ref = (
        GastoReferencia.objects
        .filter(fecha__month=mes, fecha__year=anio)
        .values('referencia_id', 'referencia__num_refe')
        .annotate(total=Sum('monto'))
        .order_by('referencia__num_refe')
    )

    resultado: list[ComisionReferencia] = []
    for row in gastos_por_ref:
        ref_id         = row['referencia_id']
        valor_operacion = row['total'] or Decimal('0')
        monto_comision  = (valor_operacion * tasa).quantize(Decimal('0.01'))

        # Intentar obtener agente desde la referencia (campo registrado_por si existe)
        agente = None
        try:
            from referencias.models import Referencia
            ref = Referencia.objects.get(pk=ref_id)
            if hasattr(ref, 'registrado_por') and ref.registrado_por:
                agente = ref.registrado_por
        except Exception:
            pass

        comision, _ = ComisionReferencia.objects.update_or_create(
            referencia_id=ref_id,
            defaults={
                'agente':          agente,
                'valor_operacion': valor_operacion,
                'tasa_comision':   tasa,
                'monto_comision':  monto_comision,
                'mes':             mes,
                'anio':            anio,
            },
        )
        resultado.append(comision)

    logger.info(
        'Comisiones %02d/%d calculadas: %d referencias, tasa %.4f',
        mes, anio, len(resultado), tasa,
    )
    return resultado


def calcular_comisiones_mes_actual() -> list[ComisionReferencia]:
    """Wrapper sin args para el scheduler APScheduler (día 1 de cada mes)."""
    hoy = date.today()
    # Calcula el mes anterior (el que acaba de cerrar)
    if hoy.month == 1:
        mes, anio = 12, hoy.year - 1
    else:
        mes, anio = hoy.month - 1, hoy.year
    return calcular_comisiones_mes(mes, anio)
