"""
Motor de conciliación bancaria automática.

Prioridades de match (en orden descendente):
  1. EXACTO     — referencia_banco del movimiento == numero de la póliza (case-insensitive)
  2. ALTO       — monto (cargo≈debe o abono≈haber de la póliza) + fecha ±1 día
  3. SUGERIDO   — mismo monto + fecha ±3 días (requiere confirmación del usuario)

Solo se consideran pólizas del mismo mes/año que los movimientos.
Solo se procesan movimientos con conciliado=False.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum

from .models import MovimientoBancario, PolizaContable


def conciliar_automatico(mes: int, anio: int, cuenta_id: int) -> dict:
    """
    Intenta conciliar automáticamente los movimientos bancarios del periodo
    con las pólizas contables del mismo periodo.

    Retorna:
        {
          'conciliados': [ {movimiento, poliza, prioridad} ],   ← guardados automáticamente
          'sugeridos':   [ {movimiento, poliza, prioridad} ],   ← requieren confirmación
          'sin_match':   [ movimiento, ... ],
        }
    """
    movimientos = list(
        MovimientoBancario.objects
        .filter(cuenta_id=cuenta_id, mes=mes, anio=anio, conciliado=False)
        .order_by('fecha')
    )

    polizas = list(
        PolizaContable.objects
        .filter(mes=mes, anio=anio)
        .prefetch_related('partidas')
    )

    # Pre-calcular el monto neto de cada póliza (suma debe de partidas)
    def monto_poliza(p: PolizaContable) -> Decimal:
        tot = p.partidas.aggregate(t=Sum('debe'))['t'] or Decimal('0')
        return tot

    poliza_montos = {p.pk: monto_poliza(p) for p in polizas}
    polizas_usadas: set[int] = set()

    conciliados: list[dict] = []
    sugeridos:   list[dict] = []
    sin_match:   list       = []

    for mov in movimientos:
        monto_mov = mov.cargo if mov.cargo else mov.abono
        match_encontrado = None
        prioridad = None

        # ── Prioridad 1: referencia exacta ────────────────────────────────────
        if mov.referencia_banco:
            for p in polizas:
                if p.pk in polizas_usadas:
                    continue
                if mov.referencia_banco.strip().upper() == p.numero.strip().upper():
                    match_encontrado = p
                    prioridad = 'EXACTO'
                    break

        # ── Prioridad 2: monto + fecha ±1 día ─────────────────────────────────
        if not match_encontrado:
            for delta in range(0, 2):
                for signo in (1, -1):
                    fecha_cmp = mov.fecha + timedelta(days=delta * signo)
                    for p in polizas:
                        if p.pk in polizas_usadas:
                            continue
                        if p.fecha == fecha_cmp and poliza_montos[p.pk] == monto_mov:
                            match_encontrado = p
                            prioridad = 'ALTO'
                            break
                    if match_encontrado:
                        break
                if match_encontrado:
                    break

        # ── Prioridad 3: monto + fecha ±3 días (sugerido) ─────────────────────
        if not match_encontrado:
            for delta in range(2, 4):
                for signo in (1, -1):
                    fecha_cmp = mov.fecha + timedelta(days=delta * signo)
                    for p in polizas:
                        if p.pk in polizas_usadas:
                            continue
                        if p.fecha == fecha_cmp and poliza_montos[p.pk] == monto_mov:
                            match_encontrado = p
                            prioridad = 'SUGERIDO'
                            break
                    if match_encontrado:
                        break
                if match_encontrado:
                    break

        if match_encontrado:
            polizas_usadas.add(match_encontrado.pk)
            entrada = {'movimiento': mov, 'poliza': match_encontrado, 'prioridad': prioridad}

            if prioridad in ('EXACTO', 'ALTO'):
                # Conciliar automáticamente
                mov.poliza      = match_encontrado
                mov.conciliado  = True
                mov.save(update_fields=['poliza', 'conciliado'])
                conciliados.append(entrada)
            else:
                # Solo sugerir — el usuario confirma
                sugeridos.append(entrada)
        else:
            sin_match.append(mov)

    return {
        'conciliados': conciliados,
        'sugeridos':   sugeridos,
        'sin_match':   sin_match,
    }


def confirmar_match(movimiento_id: int, poliza_id: int) -> MovimientoBancario:
    """
    Confirma manualmente el vínculo entre un movimiento bancario y una póliza.
    Usado para los matches SUGERIDOS o asignación manual.
    """
    mov    = MovimientoBancario.objects.get(pk=movimiento_id)
    poliza = PolizaContable.objects.get(pk=poliza_id)
    mov.poliza     = poliza
    mov.conciliado = True
    mov.save(update_fields=['poliza', 'conciliado'])
    return mov
