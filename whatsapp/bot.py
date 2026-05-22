from django.db.models import Q
from django.utils import timezone
from referencias.models import Referencia, LogSync

PREFIJO_PATENTE = {'lclf': '1627', 'lcrr': '1656', 'lcmj': '1927'}
PATENTE_PREFIJO = {v: k.upper() for k, v in PREFIJO_PATENTE.items()}

MESES = {
    'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
    'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
    'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12,
}


def handle_command(text: str) -> str:
    cmd = text.strip()
    cmd_lower = cmd.lower()

    if cmd_lower == 'ayuda':
        return (
            "🤖 *HAL9MIL Bot* — Comandos:\n\n"
            "*Búsqueda*\n"
            "• `ref LCRR0881/26` — Buscar referencia\n"
            "• `ped 7000001` — Buscar por pedimento\n"
            "• `cont CMAU5662811` — Buscar por contenedor\n"
            "• `bl SEL0702970` — Buscar por guía BL\n\n"
            "*Estadísticas*\n"
            "• `hoy` — Referencias pagadas hoy\n"
            "• `mes` — KPIs del mes en curso\n"
            "• `sync` — Estado de sincronización\n"
            "• `refs LCRR mayo` — Total por patente/mes\n"
            "• `ayuda` — Este menú"
        )

    if cmd_lower == 'hoy':
        return _cmd_hoy()

    if cmd_lower == 'mes':
        return _cmd_mes()

    if cmd_lower == 'sync':
        return _cmd_sync()

    if cmd_lower.startswith('refs '):
        return _cmd_refs(cmd_lower[5:])

    if cmd_lower.startswith('ref '):
        return _cmd_ref(cmd[4:].strip())

    if cmd_lower.startswith('ped '):
        return _cmd_ped(cmd[4:].strip())

    if cmd_lower.startswith('cont '):
        return _cmd_cont(cmd[5:].strip())

    if cmd_lower.startswith('bl '):
        return _cmd_bl(cmd[3:].strip())

    return "Comando no reconocido. Escribe *ayuda* para ver los disponibles."


# ── Búsqueda ──────────────────────────────────────────────────────────────────

def _formato_ref(r) -> str:
    """Formatea una Referencia completa para respuesta de WhatsApp."""
    arribo = r.fecha_arribo.strftime('%d-%b-%Y') if r.fecha_arribo else '—'
    pago   = r.fecha_pago.strftime('%d-%b-%Y')   if r.fecha_pago   else 'Pendiente'

    conts = list(r.contenedores.values_list('num_cont', 'tipo'))
    guias = list(r.guias.values_list('numero_guia', 'tipo_guia'))

    lineas_cont = '\n'.join(
        f"  {c} · {t if t else 'N/D'}" for c, t in conts
    ) or '  Sin contenedores'

    tipo_guia_map = {'M': 'Master', 'H': 'House', '': 'N/D'}
    lineas_guia = '\n'.join(
        f"  {g} · {tipo_guia_map.get(t, t)}" for g, t in guias
    ) or '  Sin guías'

    return (
        f"📋 *{r.num_refe}*\n"
        f"{r.nombre_cliente}\n"
        f"Patente: {r.patente} ({r.prefijo}) · {r.clave_pedimento}\n\n"
        f"📅 Arribo: {arribo}\n"
        f"{'✅' if r.fecha_pago else '⏳'} Pago:   {pago}\n"
        f"Pedimento: {r.num_pedimento or '—'}\n\n"
        f"📦 Contenedores ({len(conts)}):\n{lineas_cont}\n\n"
        f"📄 Guías BL ({len(guias)}):\n{lineas_guia}"
    )


def _cmd_ref(query: str) -> str:
    query_upper = query.upper()
    refs = Referencia.objects.filter(
        num_refe__icontains=query_upper
    ).prefetch_related('contenedores', 'guias')[:5]

    if not refs:
        return f"❌ No se encontró ninguna referencia con *{query}*"

    if len(refs) == 1:
        return _formato_ref(refs[0])

    lineas = [f"Se encontraron {len(refs)} referencias con *{query}*:\n"]
    for r in refs:
        pago = r.fecha_pago.strftime('%d-%b-%Y') if r.fecha_pago else 'Sin pago'
        lineas.append(f"• {r.num_refe} — {r.nombre_cliente[:25]} ({pago})")
    lineas.append('\nEscribe `ref <número exacto>` para ver el detalle.')
    return '\n'.join(lineas)


def _cmd_ped(query: str) -> str:
    refs = Referencia.objects.filter(
        num_pedimento__icontains=query
    ).prefetch_related('contenedores', 'guias')[:3]

    if not refs:
        return f"❌ No se encontró ningún pedimento con *{query}*"

    if len(refs) == 1:
        return _formato_ref(refs[0])

    lineas = [f"Pedimento *{query}* encontrado en {len(refs)} referencias:\n"]
    for r in refs:
        lineas.append(f"• {r.num_refe} — {r.prefijo} ({r.clave_pedimento})")
    return '\n'.join(lineas)


def _cmd_cont(query: str) -> str:
    query_upper = query.upper()
    refs = Referencia.objects.filter(
        contenedores__num_cont__icontains=query_upper
    ).prefetch_related('contenedores', 'guias').distinct()[:3]

    if not refs:
        return f"❌ No se encontró ningún contenedor con *{query}*"

    if len(refs) == 1:
        return _formato_ref(refs[0])

    lineas = [f"Contenedor *{query}* encontrado en {len(refs)} referencias:\n"]
    for r in refs:
        pago = r.fecha_pago.strftime('%d-%b-%Y') if r.fecha_pago else 'Sin pago'
        lineas.append(f"• {r.num_refe} — {r.nombre_cliente[:25]} ({pago})")
    return '\n'.join(lineas)


def _cmd_bl(query: str) -> str:
    query_upper = query.upper()
    refs = Referencia.objects.filter(
        guias__numero_guia__icontains=query_upper
    ).prefetch_related('contenedores', 'guias').distinct()[:3]

    if not refs:
        return f"❌ No se encontró ninguna guía BL con *{query}*"

    if len(refs) == 1:
        return _formato_ref(refs[0])

    lineas = [f"Guía BL *{query}* encontrada en {len(refs)} referencias:\n"]
    for r in refs:
        pago = r.fecha_pago.strftime('%d-%b-%Y') if r.fecha_pago else 'Sin pago'
        lineas.append(f"• {r.num_refe} — {r.nombre_cliente[:25]} ({pago})")
    return '\n'.join(lineas)


# ── Estadísticas ──────────────────────────────────────────────────────────────

def _cmd_hoy():
    hoy = timezone.localdate()
    qs = Referencia.objects.filter(fecha_pago=hoy, es_rectificacion=False)
    total = qs.count()
    lineas = []
    for prefijo, patente in [('LCLF', '1627'), ('LCRR', '1656'), ('LCMJ', '1927')]:
        n = qs.filter(patente=patente).count()
        lineas.append(f"  {prefijo}: {n}")
    return (
        f"📊 *Pagadas hoy* ({hoy:%d-%b-%Y})\n\n"
        f"Total: *{total}*\n\n"
        "Por patente:\n" + "\n".join(lineas)
    )


def _cmd_mes():
    hoy = timezone.localdate()
    qs = Referencia.objects.filter(
        fecha_pago__year=hoy.year,
        fecha_pago__month=hoy.month,
        es_rectificacion=False,
    )
    total = qs.count()
    lineas = []
    for prefijo, patente in [('LCLF', '1627'), ('LCRR', '1656'), ('LCMJ', '1927')]:
        n = qs.filter(patente=patente).count()
        lineas.append(f"  {prefijo}: {n}")
    nombre_mes = hoy.strftime('%B %Y')
    return (
        f"📅 *{nombre_mes}* — HAL9MIL\n\n"
        f"Pagadas: *{total}*\n\n"
        "Por patente:\n" + "\n".join(lineas)
    )


def _cmd_sync():
    lineas = []
    for prefijo, patente in [('LCLF', '1627'), ('LCRR', '1656'), ('LCMJ', '1927')]:
        ultimo = LogSync.objects.filter(patente=patente).order_by('-timestamp').first()
        if ultimo:
            icono = '✅' if ultimo.exitoso else '❌'
            lineas.append(
                f"{icono} {prefijo}: {ultimo.timestamp:%d-%b %H:%M} ({ultimo.referencias} refs)"
            )
        else:
            lineas.append(f"⚪ {prefijo}: Sin datos")
    return "🔄 *Estado de sincronización*\n\n" + "\n".join(lineas)


def _cmd_refs(args: str):
    parts = args.lower().split()
    patente = None
    mes = None
    año = timezone.localdate().year

    for part in parts:
        if part in PREFIJO_PATENTE:
            patente = PREFIJO_PATENTE[part]
        elif part in MESES:
            mes = MESES[part]
        elif part.isdigit() and len(part) == 4:
            año = int(part)

    if not patente or not mes:
        return "Uso: `refs LCRR mayo` o `refs LCLF marzo 2025`"

    total = Referencia.objects.filter(
        patente=patente,
        fecha_pago__year=año,
        fecha_pago__month=mes,
        es_rectificacion=False,
    ).count()

    nombre_mes = [k for k, v in MESES.items() if v == mes][0].capitalize()
    prefijo = PATENTE_PREFIJO[patente]
    return f"📦 *{prefijo} — {nombre_mes} {año}*\n\nReferencias pagadas: *{total}*"
