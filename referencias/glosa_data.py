import re
from collections import Counter
from datetime import date

from referencias.models import GlosaRegistro

STOP_WORDS = {
    'de', 'la', 'el', 'en', 'y', 'a', 'se', 'que', 'con', 'por', 'del',
    'los', 'las', 'un', 'una', 'no', 'es', 'al', 'su', 'le', 'ya', 'si',
    'lo', 'mas', 'pero', 'como', 'para', 'son', 'fue', 'hay', 'tiene',
    'esta', 'ser', 'sus', 'han', 'sin', 'ni', 'me', 'nos', 'hasta',
    'desde', 'también', 'tambien', 'solo', 'sido', 'fue', 'era', 'han',
    'falta', 'favor', 'favor', 'ref', 'refe',
}


def _word_freq(texts: list) -> list:
    """Returns [(word, count)] sorted by frequency, filtering stop words."""
    all_text = ' '.join(t.lower() for t in texts)
    words = re.findall(r'\b[a-záéíóúñü]{3,}\b', all_text)
    freq = Counter(w for w in words if w not in STOP_WORDS)
    return freq.most_common(20)


def _bigram_freq(texts: list) -> list:
    """Returns [(bigram, count)] for bigrams appearing more than once."""
    bigrams = []
    for text in texts:
        words = [
            w for w in re.findall(r'\b[a-záéíóúñü]{3,}\b', text.lower())
            if w not in STOP_WORDS
        ]
        bigrams.extend(f'{words[i]} {words[i+1]}' for i in range(len(words) - 1))
    freq = Counter(bigrams)
    return [(bg, c) for bg, c in freq.most_common(15) if c > 1]


def analyze_notas(glosas_qs) -> dict:
    """
    Analyzes the nota field of a GlosaRegistro queryset.
    Returns word frequency, bigrams, and capturista error mapping.
    The queryset should be pre-selected with referencia.
    """
    notas_texto = []
    notas_detalle = []

    for g in glosas_qs:
        if not (g.nota and g.nota.strip()):
            continue
        nota = g.nota.strip()
        ref = g.referencia
        notas_texto.append(nota)
        notas_detalle.append({
            'nota': nota,
            'capturista': ref.nombre_capturista or '—',
            'patente': ref.prefijo or ref.patente,
            'ref': ref.num_refe,
            'usuario': (
                g.usuario_entrada.get_full_name() or g.usuario_entrada.username
                if g.usuario_entrada else '—'
            ),
        })

    capturista_count = Counter(n['capturista'] for n in notas_detalle)

    return {
        'notas_count':            len(notas_texto),
        'top_palabras':           _word_freq(notas_texto),
        'top_bigramas':           _bigram_freq(notas_texto),
        'top_capturistas_notas':  capturista_count.most_common(10),
        'notas_muestra':          notas_detalle[:20],
    }


def get_datos_glosa_semana(inicio: date, fin: date) -> dict:
    """
    Full glosa statistics for a weekly period.
    Used by the automated weekly report.
    """
    glosas = list(
        GlosaRegistro.objects
        .filter(
            fecha_entrada__date__gte=inicio,
            fecha_entrada__date__lte=fin,
        )
        .select_related('referencia', 'usuario_entrada', 'usuario_conclusion')
    )

    total = len(glosas)
    concluidos = sum(1 for g in glosas if g.concluida)
    en_proceso = total - concluidos

    tiempos_entrada = []
    tiempos_proceso = []

    for g in glosas:
        ref = g.referencia
        ref_date = ref.fecha_arribo or ref.fecha_validacion
        if ref_date:
            d = (g.fecha_entrada.date() - ref_date).days
            if 0 <= d <= 365:
                tiempos_entrada.append(d)
        if g.concluida:
            d2 = (g.fecha_conclusion - g.fecha_entrada).total_seconds() / 86400
            if 0 <= d2 <= 365:
                tiempos_proceso.append(d2)

    avg_tiempo_entrada = (
        round(sum(tiempos_entrada) / len(tiempos_entrada), 1)
        if tiempos_entrada else None
    )
    avg_tiempo_proceso = (
        round(sum(tiempos_proceso) / len(tiempos_proceso), 1)
        if tiempos_proceso else None
    )

    # Note analysis
    nota_analysis = analyze_notas(glosas)

    # Per user
    usuario_map: dict = {}
    for g in glosas:
        u = g.usuario_entrada
        if not u:
            continue
        uname = u.username
        if uname not in usuario_map:
            usuario_map[uname] = {
                'nombre':     u.get_full_name() or uname,
                'registradas': 0,
                'concluidas':  0,
            }
        usuario_map[uname]['registradas'] += 1
        if g.concluida:
            usuario_map[uname]['concluidas'] += 1

    por_usuario = sorted(
        [
            {**v, 'pendientes': v['registradas'] - v['concluidas']}
            for v in usuario_map.values()
        ],
        key=lambda x: -x['registradas'],
    )

    return {
        'periodo_inicio':          inicio,
        'periodo_fin':             fin,
        'total':                   total,
        'concluidos':              concluidos,
        'en_proceso':              en_proceso,
        'avg_tiempo_entrada':      avg_tiempo_entrada,
        'avg_tiempo_proceso':      avg_tiempo_proceso,
        'por_usuario':             por_usuario,
        **nota_analysis,
    }
