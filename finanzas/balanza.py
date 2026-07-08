from decimal import Decimal

from django.db.models import Q, Sum

from .models import CuentaContable, PartidaPoliza


def calcular_balanza(mes: int, anio: int) -> list[dict]:
    """
    Calcula la balanza de comprobación para el periodo dado.

    Por cada CuentaContable con movimientos en cualquier periodo hasta mes/anio:
      - saldo_inicial : movimientos ANTERIORES al periodo (acumulado histórico)
      - debe          : movimientos DEBE del mes
      - haber         : movimientos HABER del mes
      - saldo_final   : saldo_inicial + variación del mes (según naturaleza de cuenta)

    Para cuentas Deudoras (naturaleza='D'):
        saldo_inicial acumula (debe - haber) de periodos anteriores
        saldo_final   = saldo_inicial + debe_mes - haber_mes

    Para cuentas Acreedoras (naturaleza='A'):
        saldo_inicial acumula (haber - debe) de periodos anteriores
        saldo_final   = saldo_inicial - debe_mes + haber_mes

    El SAT espera saldos en valor absoluto; el signo queda implícito en la naturaleza.

    Retorna lista de dicts ordenada por numero de cuenta:
        {cuenta, numero, nombre, nivel, naturaleza, codigo_agrupador_sat,
         saldo_inicial, debe, haber, saldo_final}
    """
    # Filtro "meses anteriores al periodo"
    antes_q = Q(poliza__anio__lt=anio) | (
        Q(poliza__anio=anio) & Q(poliza__mes__lt=mes)
    )
    # Filtro "exactamente este periodo"
    periodo_q = Q(poliza__anio=anio, poliza__mes=mes)

    # Cuentas con al menos una partida (en cualquier periodo hasta este mes/año)
    cuentas_ids = (
        PartidaPoliza.objects
        .filter(
            Q(poliza__anio__lt=anio) |
            Q(poliza__anio=anio, poliza__mes__lte=mes)
        )
        .values_list('cuenta_id', flat=True)
        .distinct()
    )

    cuentas = CuentaContable.objects.filter(pk__in=cuentas_ids).order_by('numero')

    resultado: list[dict] = []
    for cuenta in cuentas:
        # Saldo inicial — movimientos anteriores al periodo
        ant = PartidaPoliza.objects.filter(antes_q, cuenta=cuenta).aggregate(
            d=Sum('debe'), h=Sum('haber')
        )
        ant_debe  = ant['d'] or Decimal('0')
        ant_haber = ant['h'] or Decimal('0')

        if cuenta.naturaleza == 'D':
            saldo_inicial = ant_debe - ant_haber
        else:
            saldo_inicial = ant_haber - ant_debe

        # Movimientos del mes
        per = PartidaPoliza.objects.filter(periodo_q, cuenta=cuenta).aggregate(
            d=Sum('debe'), h=Sum('haber')
        )
        debe  = per['d'] or Decimal('0')
        haber = per['h'] or Decimal('0')

        if cuenta.naturaleza == 'D':
            saldo_final = saldo_inicial + debe - haber
        else:
            saldo_final = saldo_inicial - debe + haber

        resultado.append({
            'cuenta':               cuenta,
            'numero':               cuenta.numero,
            'nombre':               cuenta.nombre,
            'nivel':                cuenta.nivel,
            'naturaleza':           cuenta.naturaleza,
            'codigo_agrupador_sat': cuenta.codigo_agrupador_sat,
            'saldo_inicial':        saldo_inicial,
            'debe':                 debe,
            'haber':                haber,
            'saldo_final':          saldo_final,
        })

    return resultado


def totales_balanza(filas: list[dict]) -> dict:
    """Suma las columnas numéricas de la balanza para mostrar en el pie de tabla."""
    zero = Decimal('0')
    return {
        'saldo_inicial': sum((r['saldo_inicial'] for r in filas), zero),
        'debe':          sum((r['debe']          for r in filas), zero),
        'haber':         sum((r['haber']         for r in filas), zero),
        'saldo_final':   sum((r['saldo_final']   for r in filas), zero),
    }
