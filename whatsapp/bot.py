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
    cmd = text.strip().lower()

    if cmd == 'ayuda':
        return (
            "🤖 *HAL9MIL Bot* — Comandos:\n\n"
            "• `hoy` — Referencias pagadas hoy\n"
            "• `mes` — KPIs del mes en curso\n"
            "• `sync` — Estado de sincronización\n"
            "• `refs LCRR mayo` — Total por patente/mes\n"
            "• `ayuda` — Este menú"
        )

    if cmd == 'hoy':
        return _cmd_hoy()

    if cmd == 'mes':
        return _cmd_mes()

    if cmd == 'sync':
        return _cmd_sync()

    if cmd.startswith('refs '):
        return _cmd_refs(cmd[5:])

    return "Comando no reconocido. Escribe *ayuda* para ver los disponibles."


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
