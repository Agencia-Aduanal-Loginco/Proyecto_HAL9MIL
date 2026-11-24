"""Métricas de la glosa IA: precisión de Jev, uso/costo de IA y resumen semanal.

Precisión de Jev: de los documentos que Jev clasificó (clasificacion_ia.tipo_ia),
cuántos conservan ese tipo. Si el usuario lo cambió, la vista de cambio de tipo
guarda tipo_ia, así que el tipo final distinto cuenta como error de Jev.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation

from .models import DocumentoReferencia, GlosaAutomaticaResultado, GlosaEvento


def precision_jev(documentos):
    total = aciertos = 0
    confusiones = Counter()
    for tipo_final, clasif in documentos.values_list('tipo_documento', 'clasificacion_ia'):
        tipo_ia = (clasif or {}).get('tipo_ia')
        if not tipo_ia:
            continue
        total += 1
        if tipo_ia == tipo_final:
            aciertos += 1
        else:
            confusiones[(tipo_ia, tipo_final)] += 1
    return {
        'total': total,
        'aciertos': aciertos,
        'pct': round(aciertos * 100 / total, 1) if total else None,
        'confusiones': [
            {'jev': jev, 'final': final, 'total': n} for (jev, final), n in confusiones.most_common(5)
        ],
    }


def uso_ia(documentos):
    """Tokens y costo de Claude (extracción) y tokens de Jev (clasificación)."""
    claude_in = claude_out = jev_in = reutilizados = 0
    costo = Decimal('0')
    con_costo = False
    for datos, clasif in documentos.values_list('datos_extraidos', 'clasificacion_ia'):
        meta = (datos or {}).get('meta') or {}
        if meta.get('reutilizado_de'):
            reutilizados += 1
        uso = meta.get('uso') or {}
        claude_in += int(uso.get('input_tokens') or 0)
        claude_out += int(uso.get('output_tokens') or 0)
        if uso.get('costo_usd'):
            try:
                costo += Decimal(str(uso['costo_usd']))
                con_costo = True
            except InvalidOperation:
                pass
        jev_in += int(((clasif or {}).get('uso') or {}).get('input_tokens') or 0)
    return {
        'claude_input_tokens': claude_in,
        'claude_output_tokens': claude_out,
        'costo_usd': str(costo.quantize(Decimal('0.01'))) if con_costo else None,
        'jev_input_tokens': jev_in,
        'extracciones_reutilizadas': reutilizados,
    }


def top_discrepancias(resultados, n=5):
    conteo = Counter()
    for datos in resultados.values_list('datos_comparacion', flat=True):
        for d in (datos or {}).get('discrepancias') or []:
            if isinstance(d, dict) and d.get('codigo'):
                conteo[d['codigo']] += 1
    return [{'codigo': c, 'total': t} for c, t in conteo.most_common(n)]


def get_datos_glosa_ia_semana(inicio, fin):
    docs = DocumentoReferencia.objects.filter(creado_en__date__gte=inicio, creado_en__date__lte=fin)
    eventos = GlosaEvento.objects.filter(creado_en__date__gte=inicio, creado_en__date__lte=fin)
    resultados = GlosaAutomaticaResultado.objects.filter(
        actualizado_en__date__gte=inicio, actualizado_en__date__lte=fin,
    )
    return {
        'documentos_subidos': docs.count(),
        'expedientes': docs.values('referencia').distinct().count(),
        'aprobadas': eventos.filter(tipo='APROBACION').count(),
        'rechazadas': eventos.filter(tipo='RECHAZO').count(),
        'reaperturas': eventos.filter(tipo='REAPERTURA').count(),
        'con_discrepancias': resultados.filter(estado_revision='DISCREPANCIAS_DETECTADAS').count(),
        'top_discrepancias': top_discrepancias(resultados),
        'precision_jev': precision_jev(docs),
        'uso': uso_ia(docs),
    }
