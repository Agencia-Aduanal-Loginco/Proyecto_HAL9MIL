"""
Motor de cierre mensual.

Proceso:
  1. Validar que ninguna póliza del periodo esté descuadrada (debe ≠ haber)
  2. Verificar conciliación bancaria: advertir si hay cuentas bancarias con
     movimientos sin conciliar (no bloquea, solo reporta en observaciones)
  3. Calcular totales del periodo: ingresos, egresos, anticipos, facturas emitidas
  4. Marcar todas las PolizaContable del periodo como cerrado=True
  5. Crear CierreMensual — falla con IntegrityError si ya existe (unique_together)

Uso:
    from finanzas.cierre import ejecutar_cierre_mensual, CierreError
    try:
        cierre = ejecutar_cierre_mensual(mes=6, anio=2026, patente='1627', user=request.user)
    except CierreError as e:
        print(e.problemas)
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from .models import (
    Anticipo, CierreMensual, ConfiguracionFiscal, Factura,
    MovimientoBancario, PolizaContable,
)


class CierreError(Exception):
    def __init__(self, problemas: list[str]):
        self.problemas = problemas
        super().__init__('; '.join(problemas))


def _validar_polizas(mes: int, anio: int) -> list[str]:
    """Retorna lista de errores para pólizas descuadradas en el periodo."""
    errores: list[str] = []
    polizas = (
        PolizaContable.objects
        .filter(mes=mes, anio=anio, cerrado=False)
        .prefetch_related('partidas')
    )
    for p in polizas:
        debe  = p.partidas.aggregate(t=Sum('debe'))['t']  or Decimal('0')
        haber = p.partidas.aggregate(t=Sum('haber'))['t'] or Decimal('0')
        if debe != haber:
            errores.append(
                f'Póliza {p.tipo}{p.numero} ({p.fecha}) descuadrada: DEBE {debe} ≠ HABER {haber}'
            )
    return errores


def _advertencias_conciliacion(mes: int, anio: int) -> list[str]:
    """Advertencias (no bloqueantes) de movimientos sin conciliar."""
    advertencias: list[str] = []
    pendientes = (
        MovimientoBancario.objects
        .filter(mes=mes, anio=anio, conciliado=False)
        .values('cuenta__nombre', 'cuenta__banco')
        .annotate(total=Sum('cargo') if True else Sum('abono'))
    )
    cuentas_pendientes = (
        MovimientoBancario.objects
        .filter(mes=mes, anio=anio, conciliado=False)
        .values_list('cuenta__banco', 'cuenta__nombre')
        .distinct()
    )
    for banco, nombre in cuentas_pendientes:
        n = MovimientoBancario.objects.filter(
            mes=mes, anio=anio, conciliado=False,
            cuenta__banco=banco, cuenta__nombre=nombre
        ).count()
        advertencias.append(
            f'{banco} — {nombre}: {n} movimiento(s) sin conciliar'
        )
    return advertencias


def _calcular_totales(mes: int, anio: int, patente: str) -> dict:
    """Calcula los totales del periodo para el CierreMensual."""
    # Ingresos: pólizas tipo H (Ingreso/Haber) — suma de debe de sus partidas
    polizas_h = PolizaContable.objects.filter(mes=mes, anio=anio, tipo='H')
    total_ingresos = Decimal('0')
    for p in polizas_h:
        total_ingresos += p.partidas.aggregate(t=Sum('debe'))['t'] or Decimal('0')

    # Egresos: pólizas tipo E
    polizas_e = PolizaContable.objects.filter(mes=mes, anio=anio, tipo='E')
    total_egresos = Decimal('0')
    for p in polizas_e:
        total_egresos += p.partidas.aggregate(t=Sum('debe'))['t'] or Decimal('0')

    # Anticipos recibidos en el periodo (por patente vía referencia.patente)
    try:
        config = ConfiguracionFiscal.objects.get(patente=patente, activa=True)
    except ConfiguracionFiscal.DoesNotExist:
        config = None

    total_anticipos = (
        Anticipo.objects
        .filter(fecha__month=mes, fecha__year=anio)
        .aggregate(t=Sum('monto'))['t'] or Decimal('0')
    )

    # Facturas emitidas TIMBRADAS en el periodo para esta patente
    qs_facturas = Factura.objects.filter(
        estado='TIMBRADA',
        fecha_emision__month=mes,
        fecha_emision__year=anio,
    )
    if config:
        qs_facturas = qs_facturas.filter(configuracion_fiscal=config)

    total_facturas = qs_facturas.count()

    total_polizas = PolizaContable.objects.filter(mes=mes, anio=anio).count()

    return {
        'total_polizas':            total_polizas,
        'total_facturas_emitidas':  total_facturas,
        'total_ingresos':           total_ingresos,
        'total_egresos':            total_egresos,
        'total_anticipos_recibidos': total_anticipos,
    }


@transaction.atomic
def ejecutar_cierre_mensual(mes: int, anio: int, patente: str, user,
                             observaciones: str = '') -> CierreMensual:
    """
    Ejecuta el cierre mensual para la patente y periodo dados.

    Raises:
        CierreError  — si hay pólizas descuadradas
        IntegrityError — si ya existe un cierre para ese mes/año/patente
    """
    # 1. Validar pólizas
    errores = _validar_polizas(mes, anio)
    if errores:
        raise CierreError(errores)

    # 2. Advertencias de conciliación (no bloquean)
    advertencias = _advertencias_conciliacion(mes, anio)
    if advertencias and observaciones:
        observaciones += '\n'
    if advertencias:
        observaciones += 'Advertencias conciliación:\n' + '\n'.join(f'  • {a}' for a in advertencias)

    # 3. Calcular totales
    totales = _calcular_totales(mes, anio, patente)

    # 4. Congelar pólizas del periodo
    PolizaContable.objects.filter(mes=mes, anio=anio).update(cerrado=True)

    # 5. Crear CierreMensual (unique_together garantiza unicidad)
    cierre = CierreMensual.objects.create(
        mes=mes,
        anio=anio,
        patente=patente,
        cerrado_por=user,
        observaciones=observaciones,
        **totales,
    )

    return cierre
