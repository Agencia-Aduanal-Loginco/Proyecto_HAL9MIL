from datetime import date, timedelta

from .models import Referencia


def _percentil(datos_sorted, p):
    n = len(datos_sorted)
    if n == 0:
        return None
    return datos_sorted[max(0, min(int(n * p / 100), n - 1))]


def _calcular_stats(pares_arribo_pago):
    dias = sorted(
        (pago - arribo).days
        for arribo, pago in pares_arribo_pago
        if arribo and pago and 0 <= (pago - arribo).days <= 365
    )
    if not dias:
        return None
    return {
        'n':   len(dias),
        'p25': _percentil(dias, 25),
        'p50': _percentil(dias, 50),
        'p75': _percentil(dias, 75),
    }


def _label_dias(resta):
    if resta > 0:
        return f'en {resta} día{"s" if resta != 1 else ""}'
    elif resta == 0:
        return 'hoy'
    else:
        return f'hace {abs(resta)} día{"s" if abs(resta) != 1 else ""}'


def predecir_pago(referencia):
    """
    Estima fechas de pago para una referencia no pagada con fecha_arribo.
    Retorna None si ya está pagada, sin arribo, o sin datos históricos suficientes.
    """
    if not referencia.fecha_arribo:
        return None
    if referencia.num_operacion and referencia.linea_captura:
        return None

    fecha_arribo = referencia.fecha_arribo
    base_qs = Referencia.objects.filter(
        es_rectificacion=False,
        num_operacion__gt='',
        linea_captura__gt='',
        fecha_arribo__isnull=False,
        fecha_pago__isnull=False,
        fecha_pago__gte='2022-01-01',
    )

    pares_cliente = list(
        base_qs.filter(
            nombre_cliente=referencia.nombre_cliente,
            patente=referencia.patente,
        ).values_list('fecha_arribo', 'fecha_pago')
    )
    stats = _calcular_stats(pares_cliente)

    if stats and stats['n'] >= 10:
        confianza = 'alta'
        fuente = 'cliente'
    elif stats and stats['n'] >= 5:
        confianza = 'media'
        fuente = 'cliente'
    else:
        pares_patente = list(
            base_qs.filter(patente=referencia.patente)
            .values_list('fecha_arribo', 'fecha_pago')
        )
        stats = _calcular_stats(pares_patente)
        confianza = 'baja'
        fuente = 'patente'

    if not stats:
        return None

    today = date.today()
    dias_transcurridos = (today - fecha_arribo).days

    resta_opt    = stats['p25'] - dias_transcurridos
    resta_normal = stats['p50'] - dias_transcurridos
    resta_pes    = stats['p75'] - dias_transcurridos

    return {
        'p25': stats['p25'],
        'p50': stats['p50'],
        'p75': stats['p75'],
        'n':   stats['n'],
        'confianza':    confianza,
        'fuente':       fuente,
        'fecha_opt':    fecha_arribo + timedelta(days=stats['p25']),
        'fecha_normal': fecha_arribo + timedelta(days=stats['p50']),
        'fecha_pes':    fecha_arribo + timedelta(days=stats['p75']),
        'dias_transcurridos': dias_transcurridos,
        'resta_opt':    resta_opt,
        'resta_normal': resta_normal,
        'resta_pes':    resta_pes,
        'label_opt':    _label_dias(resta_opt),
        'label_normal': _label_dias(resta_normal),
        'label_pes':    _label_dias(resta_pes),
        'ya_pasado':    dias_transcurridos > stats['p75'],
    }
