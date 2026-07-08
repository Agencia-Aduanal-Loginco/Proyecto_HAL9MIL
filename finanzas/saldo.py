from decimal import Decimal
from django.db.models import Sum


def saldo_referencia(referencia) -> dict:
    """
    Retorna el estado financiero de una referencia:
      - total_anticipos: dinero recibido del importador
      - total_gastos:    gastos cargados a la referencia
      - saldo:           total_anticipos - total_gastos
        positivo → remanente a favor del importador
        negativo → monto pendiente de cobro
    """
    total_anticipos = (
        referencia.anticipos.aggregate(total=Sum('monto'))['total']
        or Decimal('0')
    )
    total_gastos = (
        referencia.gastos_finanzas.aggregate(total=Sum('monto'))['total']
        or Decimal('0')
    )
    return {
        'total_anticipos': total_anticipos,
        'total_gastos': total_gastos,
        'saldo': total_anticipos - total_gastos,
    }
