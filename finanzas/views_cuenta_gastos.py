"""Vistas del flujo de cierre y envío de la cuenta de gastos al cliente."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from core.permisos import modulo_required
from referencias.models import Referencia

from .cuenta_gastos_envio import enviar_cuenta_gastos
from .models import CierreCuentaGastos


def _redirect_estado(num_refe):
    return redirect('finanzas:referencia_estado', num_refe=num_refe)


@modulo_required('Finanzas')
def cerrar_cg(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    if request.method != 'POST':
        return _redirect_estado(num_refe)

    cierre = CierreCuentaGastos.objects.filter(referencia=referencia).first()
    if cierre and cierre.activa:
        messages.info(request, 'La cuenta de gastos ya está cerrada.')
        return _redirect_estado(num_refe)

    nota = request.POST.get('nota', '').strip()[:300]
    if cierre:
        cierre.cerrada_por = request.user
        cierre.cerrada_en = timezone.now()
        cierre.nota = nota
        cierre.reabierta_por = None
        cierre.reabierta_en = None
        cierre.save()
    else:
        CierreCuentaGastos.objects.create(
            referencia=referencia, cerrada_por=request.user, nota=nota,
        )
    messages.success(
        request,
        'Cuenta de gastos cerrada. Ya no se pueden registrar anticipos ni gastos.',
    )
    return _redirect_estado(num_refe)


@login_required
def reabrir_cg(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    if request.method != 'POST':
        return _redirect_estado(num_refe)
    if not request.user.is_superuser:
        messages.error(request, 'Solo un superusuario puede reabrir la cuenta de gastos.')
        return _redirect_estado(num_refe)

    cierre = CierreCuentaGastos.activo_para(referencia)
    if cierre:
        cierre.reabierta_por = request.user
        cierre.reabierta_en = timezone.now()
        cierre.save()
        messages.success(request, 'Cuenta de gastos reabierta.')
    return _redirect_estado(num_refe)


@modulo_required('Finanzas')
def enviar_cg(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    if request.method != 'POST':
        return _redirect_estado(num_refe)
    if not CierreCuentaGastos.activo_para(referencia):
        messages.error(request, 'La cuenta de gastos debe cerrarse antes de enviarse.')
        return _redirect_estado(num_refe)

    destinatario = request.POST.get('destinatario', '').strip()
    if not destinatario:
        messages.error(request, 'Captura el correo del destinatario.')
        return _redirect_estado(num_refe)
    cc = request.POST.get('cc', '').strip()

    es_reenvio = referencia.notificaciones_cg.exists()
    notif = enviar_cuenta_gastos(
        referencia, destinatario, cc, request.user, es_reenvio=es_reenvio,
    )
    if notif.estado == 'ERROR':
        messages.error(request, f'No se pudo enviar la cuenta de gastos: {notif.error_msg}')
    else:
        messages.success(request, f'Cuenta de gastos enviada a {destinatario}.')
    return _redirect_estado(num_refe)
