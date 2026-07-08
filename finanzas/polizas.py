from decimal import Decimal
from django.db.models import Max
from .models import CuentaContable, PartidaPoliza, PolizaContable


def _siguiente_numero(tipo: str, anio: int, mes: int) -> str:
    prefix = f'{tipo}{anio}{mes:02d}'
    ultimo = PolizaContable.objects.filter(
        tipo=tipo, anio=anio, mes=mes
    ).aggregate(Max('numero'))['numero__max']
    if ultimo:
        try:
            seq = int(ultimo.rsplit('-', 1)[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1
    return f'{prefix}-{seq:04d}'


def generar_poliza_anticipo(anticipo) -> PolizaContable:
    """
    Anticipo recibido → póliza tipo H (Ingreso):
      DEBE:  Bancos (1-100-002)
      HABER: Anticipos de clientes (2-100-001)
    Llama desde la vista DESPUÉS de guardar el anticipo.
    """
    try:
        bancos = CuentaContable.objects.get(numero='1-100-002')
        anticipos_cli = CuentaContable.objects.get(numero='2-100-001')
    except CuentaContable.DoesNotExist as e:
        raise ValueError(f'Cuenta contable requerida no encontrada: {e}')

    fecha = anticipo.fecha
    poliza = PolizaContable.objects.create(
        numero=_siguiente_numero('H', fecha.year, fecha.month),
        tipo='H',
        fecha=fecha,
        mes=fecha.month,
        anio=fecha.year,
        concepto=f'Anticipo recibido — {anticipo.referencia.num_refe}',
        referencia=anticipo.referencia,
        creado_por=anticipo.registrado_por,
    )
    PartidaPoliza.objects.bulk_create([
        PartidaPoliza(
            poliza=poliza, linea=1,
            cuenta=bancos,
            concepto=f'Anticipo {anticipo.referencia.num_refe}',
            debe=anticipo.monto, haber=Decimal('0'),
        ),
        PartidaPoliza(
            poliza=poliza, linea=2,
            cuenta=anticipos_cli,
            concepto=f'Cliente: {anticipo.referencia.nombre_cliente}',
            debe=Decimal('0'), haber=anticipo.monto,
        ),
    ])
    return poliza


def generar_poliza_gasto(gasto) -> PolizaContable:
    """
    Gasto registrado → póliza tipo E (Egreso):
      DEBE:  Cuenta de gasto (gasto.cuenta_gasto o 5-100-006 Gastos varios)
      HABER: Proveedores por pagar (2-100-002)
    Llama desde la vista DESPUÉS de guardar el gasto.
    """
    try:
        proveedores = CuentaContable.objects.get(numero='2-100-002')
        cuenta_gasto = gasto.cuenta_gasto or CuentaContable.objects.get(numero='5-100-006')
    except CuentaContable.DoesNotExist as e:
        raise ValueError(f'Cuenta contable requerida no encontrada: {e}')

    fecha = gasto.fecha
    poliza = PolizaContable.objects.create(
        numero=_siguiente_numero('E', fecha.year, fecha.month),
        tipo='E',
        fecha=fecha,
        mes=fecha.month,
        anio=fecha.year,
        concepto=f'{gasto.get_tipo_display()} — {gasto.referencia.num_refe}',
        referencia=gasto.referencia,
        creado_por=gasto.registrado_por,
    )
    PartidaPoliza.objects.bulk_create([
        PartidaPoliza(
            poliza=poliza, linea=1,
            cuenta=cuenta_gasto,
            concepto=gasto.concepto,
            debe=gasto.monto, haber=Decimal('0'),
        ),
        PartidaPoliza(
            poliza=poliza, linea=2,
            cuenta=proveedores,
            concepto=f'Por pagar: {gasto.proveedor or "proveedor"}',
            debe=Decimal('0'), haber=gasto.monto,
        ),
    ])
    return poliza
