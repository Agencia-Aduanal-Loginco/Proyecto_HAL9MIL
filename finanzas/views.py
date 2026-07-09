import os
import tempfile
from decimal import Decimal

from django.contrib import messages
from core.permisos import modulo_required
from django.core.paginator import Paginator
from django.db.models import OuterRef, Subquery, Sum
from django.shortcuts import get_object_or_404, redirect, render

from referencias.models import Referencia
from .cfdi_parser import parsear_cfdi
from .carga_xml import crear_gasto_desde_xml
from .balanza import calcular_balanza, totales_balanza
from .cierre import CierreError, ejecutar_cierre_mensual
from .comisiones import calcular_comisiones_mes
from .conciliacion import conciliar_automatico, confirmar_match
from .exportar_sat import (
    exportar_balanza_xml, exportar_catalogo_cuentas_xml, exportar_polizas_xml,
    nombre_archivo_balanza, nombre_archivo_catalogo, nombre_archivo_polizas,
)
from .forms import AnticipoForm, FacturaForm, GastoReferenciaForm, PagoForm
from .models import (
    Anticipo, CierreMensual, ComisionReferencia, ConceptoFactura, CuentaBancaria,
    DoctoRelacionado, Factura, GastoReferencia, MovimientoBancario, Pago,
    PolizaContable, XMLProveedor,
)
from .polizas import generar_poliza_anticipo, generar_poliza_gasto
from .saldo import saldo_referencia
from .utils import get_configuracion_fiscal


@modulo_required('Finanzas')
def dashboard_financiero(request):
    total_anticipos = Anticipo.objects.count()
    total_gastos = GastoReferencia.objects.count()
    total_polizas = PolizaContable.objects.count()
    total_facturas = Factura.objects.count()
    total_pagos = Pago.objects.count()
    pendientes_cobro = (
        Referencia.objects
        .filter(gastos_finanzas__isnull=False)
        .exclude(facturas__estado='TIMBRADA')
        .distinct()
        .count()
    )
    return render(request, 'finanzas/dashboard.html', {
        'total_anticipos': total_anticipos,
        'total_gastos': total_gastos,
        'total_polizas': total_polizas,
        'total_facturas': total_facturas,
        'total_pagos': total_pagos,
        'pendientes_cobro': pendientes_cobro,
    })


@modulo_required('Finanzas')
def referencia_estado_financiero(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    anticipos = referencia.anticipos.select_related('registrado_por').order_by('-fecha')
    gastos = referencia.gastos_finanzas.select_related('cuenta_gasto', 'registrado_por').order_by('-fecha')
    saldo = saldo_referencia(referencia)
    return render(request, 'finanzas/referencia_estado.html', {
        'referencia': referencia,
        'anticipos': anticipos,
        'gastos': gastos,
        'saldo': saldo,
    })


@modulo_required('Finanzas')
def anticipo_crear(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    if request.method == 'POST':
        form = AnticipoForm(request.POST)
        if form.is_valid():
            anticipo = form.save(commit=False)
            anticipo.referencia = referencia
            anticipo.registrado_por = request.user
            anticipo.save()
            poliza = generar_poliza_anticipo(anticipo)
            anticipo.poliza = poliza
            anticipo.save(update_fields=['poliza'])
            messages.success(request, f'Anticipo de ${anticipo.monto} registrado. Póliza {poliza.numero} generada.')
            return redirect('finanzas:referencia_estado', num_refe=num_refe)
    else:
        form = AnticipoForm(initial={'fecha': referencia.fecha_pago})
    return render(request, 'finanzas/anticipo_form.html', {
        'form': form,
        'referencia': referencia,
    })


@modulo_required('Finanzas')
def gasto_crear(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    if request.method == 'POST':
        form = GastoReferenciaForm(request.POST)
        if form.is_valid():
            gasto = form.save(commit=False)
            gasto.referencia = referencia
            gasto.registrado_por = request.user
            gasto.save()
            poliza = generar_poliza_gasto(gasto)
            gasto.poliza = poliza
            gasto.save(update_fields=['poliza'])
            messages.success(request, f'Gasto de ${gasto.monto} registrado. Póliza {poliza.numero} generada.')
            return redirect('finanzas:referencia_estado', num_refe=num_refe)
    else:
        form = GastoReferenciaForm(initial={'fecha': referencia.fecha_pago})
    return render(request, 'finanzas/gasto_form.html', {
        'form': form,
        'referencia': referencia,
    })


@modulo_required('Finanzas')
def anticipos_list(request):
    qs = Anticipo.objects.select_related('referencia', 'registrado_por').order_by('-fecha')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(referencia__num_refe__icontains=q)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'finanzas/anticipos_list.html', {'page': page, 'q': q})


@modulo_required('Finanzas')
def gastos_list(request):
    qs = GastoReferencia.objects.select_related('referencia', 'cuenta_gasto', 'registrado_por').order_by('-fecha')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(referencia__num_refe__icontains=q)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'finanzas/gastos_list.html', {'page': page, 'q': q})


@modulo_required('Finanzas')
def polizas_list(request):
    qs = PolizaContable.objects.select_related('referencia', 'creado_por').order_by('-fecha')
    tipo = request.GET.get('tipo', '')
    if tipo:
        qs = qs.filter(tipo=tipo)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'finanzas/polizas_list.html', {'page': page, 'tipo': tipo})


@modulo_required('Finanzas')
def poliza_detalle(request, pk):
    poliza = get_object_or_404(
        PolizaContable.objects.prefetch_related('partidas__cuenta'),
        pk=pk
    )
    return render(request, 'finanzas/poliza_detalle.html', {'poliza': poliza})


@modulo_required('Finanzas')
def subir_xml_proveedor(request, num_refe):
    referencia = get_object_or_404(Referencia, num_refe=num_refe)
    if request.method != 'POST':
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    xml_file = request.FILES.get('xml_file')
    if not xml_file:
        messages.error(request, 'No se seleccionó ningún archivo XML.')
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    if not xml_file.name.lower().endswith('.xml'):
        messages.error(request, 'El archivo debe tener extensión .xml')
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    # Parsear en archivo temporal antes de persistir
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.xml', delete=False) as tmp:
            for chunk in xml_file.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name
        datos = parsear_cfdi(tmp_path)
    except ValueError as e:
        messages.error(request, f'Error al leer el XML: {e}')
        return redirect('finanzas:referencia_estado', num_refe=num_refe)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Verificar UUID único
    if XMLProveedor.objects.filter(uuid_fiscal=datos['uuid']).exists():
        messages.error(request, f'Este XML ya fue registrado (UUID duplicado).')
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    # Guardar archivo y crear registro
    xml_file.seek(0)
    xml_obj = XMLProveedor.objects.create(
        referencia=referencia,
        uuid_fiscal=datos['uuid'],
        fecha_emision=datos['fecha'],
        rfc_emisor=datos['rfc_emisor'],
        nombre_emisor=datos['nombre_emisor'],
        rfc_receptor=datos['rfc_receptor'],
        subtotal=datos['subtotal'],
        iva=datos['iva'],
        total=datos['total'],
        moneda=datos['moneda'],
        tipo_comprobante=datos['tipo'],
        concepto_principal=datos['concepto_principal'],
        xml_file=xml_file,
        estado_asignacion='ASIGNADO',
    )

    # Crear GastoReferencia automático si el usuario lo solicitó y el XML es tipo Ingreso
    if request.POST.get('crear_gasto') == '1' and datos['tipo'] == 'I':
        gasto = crear_gasto_desde_xml(xml_obj, request.user, tipo='OTROS')
        messages.success(
            request,
            f'XML cargado · Gasto ${datos["total"]} registrado · Póliza {gasto.poliza.numero} generada.'
        )
    else:
        messages.success(request, f'XML cargado correctamente. UUID: {datos["uuid"]}')

    return redirect('finanzas:referencia_estado', num_refe=num_refe)


# ── Fase 4 ────────────────────────────────────────────────────────────────────

@modulo_required('Finanzas')
def cobranza_list(request):
    gastos_sub = (
        GastoReferencia.objects
        .filter(referencia=OuterRef('pk'))
        .values('referencia')
        .annotate(t=Sum('monto'))
        .values('t')
    )
    anticipos_sub = (
        Anticipo.objects
        .filter(referencia=OuterRef('pk'))
        .values('referencia')
        .annotate(t=Sum('monto'))
        .values('t')
    )
    refs = (
        Referencia.objects
        .filter(gastos_finanzas__isnull=False)
        .exclude(facturas__estado='TIMBRADA')
        .distinct()
        .annotate(
            total_gastos=Subquery(gastos_sub),
            total_anticipos=Subquery(anticipos_sub),
        )
        .order_by('-fecha_pago')
    )

    items = []
    for ref in refs:
        total_g = ref.total_gastos or Decimal('0')
        total_a = ref.total_anticipos or Decimal('0')
        items.append({
            'referencia': ref,
            'total_gastos': total_g,
            'total_anticipos': total_a,
            'saldo': total_a - total_g,
        })

    return render(request, 'finanzas/cobranza_list.html', {'items': items})


@modulo_required('Finanzas')
def facturas_list(request):
    qs = (
        Factura.objects
        .select_related('configuracion_fiscal')
        .prefetch_related('referencias')
        .order_by('-created_at')
    )
    estado = request.GET.get('estado', '')
    if estado:
        qs = qs.filter(estado=estado)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'finanzas/facturas_list.html', {
        'page': page,
        'estado': estado,
        'estados': Factura.ESTADO,
    })


@modulo_required('Finanzas')
def factura_crear(request, num_refe):
    from clientes.models import Cliente

    referencia = get_object_or_404(Referencia, num_refe=num_refe)

    try:
        config = get_configuracion_fiscal(referencia.patente)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('finanzas:referencia_estado', num_refe=num_refe)

    cliente = Cliente.objects.filter(cve_cliente=referencia.cve_cliente).first()
    saldo = saldo_referencia(referencia)
    monto_sugerido = abs(saldo['saldo']) if saldo['saldo'] < 0 else Decimal('0')

    if request.method == 'POST':
        form = FacturaForm(request.POST)
        if form.is_valid():
            factura = form.save(commit=False)
            factura.configuracion_fiscal = config
            factura.folio = Factura.siguiente_folio(factura.serie)
            valor = form.cleaned_data['valor_unitario']
            factura.subtotal = valor
            factura.iva = (valor * Decimal('0.16')).quantize(Decimal('0.01'))
            factura.save()
            factura.referencias.add(referencia)
            ConceptoFactura.objects.create(
                factura=factura,
                descripcion=form.cleaned_data['concepto_descripcion'],
                cantidad=Decimal('1'),
                valor_unitario=valor,
                tasa_iva=Decimal('0.16'),
            )
            messages.success(
                request,
                f'Factura borrador {factura.serie}{factura.folio} creada correctamente.'
            )
            return redirect('finanzas:factura_detalle', pk=factura.pk)
    else:
        form = FacturaForm(initial={
            'rfc_receptor': cliente.rfc if cliente else '',
            'nombre_receptor': referencia.nombre_cliente,
            'concepto_descripcion': f'Honorarios por despacho aduanal — Ref. {num_refe}',
            'valor_unitario': monto_sugerido if monto_sugerido else '',
        })

    return render(request, 'finanzas/factura_form.html', {
        'form': form,
        'referencia': referencia,
        'config': config,
        'saldo': saldo,
        'monto_sugerido': monto_sugerido,
    })


@modulo_required('Finanzas')
def factura_detalle(request, pk):
    factura = get_object_or_404(
        Factura.objects
        .select_related('configuracion_fiscal')
        .prefetch_related('conceptos', 'referencias'),
        pk=pk,
    )
    return render(request, 'finanzas/factura_detalle.html', {'factura': factura})


@modulo_required('Finanzas')
def factura_timbrar(request, pk):
    """POST-only. Genera XML CFDI 4.0, lo envía al PAC y persiste el resultado."""
    import uuid as uuid_lib
    from .cfdi_generator import generar_xml_cfdi40
    from .pac_client import PACConfigError, PACError, timbrar_cfdi

    if request.method != 'POST':
        return redirect('finanzas:factura_detalle', pk=pk)

    factura = get_object_or_404(
        Factura.objects.select_related('configuracion_fiscal').prefetch_related('conceptos'),
        pk=pk,
    )

    if factura.estado != 'BORRADOR':
        messages.error(
            request,
            f'La factura ya está en estado "{factura.get_estado_display()}". '
            f'Solo se pueden timbrar facturas en borrador.'
        )
        return redirect('finanzas:factura_detalle', pk=pk)

    try:
        xml = generar_xml_cfdi40(factura)
    except FileNotFoundError as e:
        messages.error(request, f'Archivo CSD no encontrado: {e}')
        return redirect('finanzas:factura_detalle', pk=pk)
    except ValueError as e:
        messages.error(request, f'Error de configuración CSD/entorno: {e}')
        return redirect('finanzas:factura_detalle', pk=pk)
    except Exception as e:
        messages.error(request, f'Error generando XML CFDI: {e}')
        return redirect('finanzas:factura_detalle', pk=pk)

    try:
        resultado = timbrar_cfdi(xml)
    except PACConfigError as e:
        messages.error(request, f'Configuración PAC incompleta: {e}')
        return redirect('finanzas:factura_detalle', pk=pk)
    except PACError as e:
        messages.error(request, f'PAC rechazó el timbrado [{e.code}]: {e}')
        return redirect('finanzas:factura_detalle', pk=pk)
    except Exception as e:
        messages.error(request, f'Error de red al contactar el PAC: {e}')
        return redirect('finanzas:factura_detalle', pk=pk)

    factura.uuid_fiscal = uuid_lib.UUID(resultado['uuid'])
    factura.xml_timbrado = resultado['xml_timbrado']
    factura.estado = 'TIMBRADA'
    factura.save(update_fields=['uuid_fiscal', 'xml_timbrado', 'estado'])

    messages.success(
        request,
        f'Factura {factura.serie}{factura.folio} timbrada. UUID: {factura.uuid_fiscal}'
    )
    return redirect('finanzas:factura_detalle', pk=pk)


@modulo_required('Finanzas')
def pagos_list(request):
    qs = (
        Pago.objects
        .prefetch_related('documentos__factura')
        .order_by('-fecha_pago', '-created_at')
    )
    estado = request.GET.get('estado', '')
    if estado:
        qs = qs.filter(estado=estado)
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'finanzas/pagos_list.html', {
        'page': page,
        'estado': estado,
        'estados': Pago.ESTADO,
    })


@modulo_required('Finanzas')
def pago_registrar(request):
    """Registra un Pago y su DoctoRelacionado contra una Factura TIMBRADA."""
    # Permitir pre-seleccionar factura desde factura_detalle vía GET param
    factura_id_inicial = request.GET.get('factura_id')

    if request.method == 'POST':
        form = PagoForm(request.POST)
        if form.is_valid():
            factura = form.cleaned_data['factura']

            # Calcular saldo anterior y parcialidad
            pagados_previos = (
                DoctoRelacionado.objects
                .filter(factura=factura)
                .aggregate(t=Sum('imp_pagado'))['t'] or Decimal('0')
            )
            imp_saldo_anterior = factura.total - pagados_previos
            num_parcialidad = DoctoRelacionado.objects.filter(factura=factura).count() + 1
            monto = form.cleaned_data['monto']

            if monto > imp_saldo_anterior:
                form.add_error(
                    'monto',
                    f'El monto (${monto}) supera el saldo pendiente (${imp_saldo_anterior}).'
                )
            else:
                pago = Pago.objects.create(
                    fecha_pago=form.cleaned_data['fecha_pago'],
                    monto=monto,
                    moneda=form.cleaned_data['moneda'],
                    forma_pago=form.cleaned_data['forma_pago'],
                    num_operacion=form.cleaned_data['num_operacion'],
                )
                DoctoRelacionado.objects.create(
                    pago=pago,
                    factura=factura,
                    num_parcialidad=num_parcialidad,
                    imp_saldo_anterior=imp_saldo_anterior,
                    imp_pagado=monto,
                    # imp_saldo_insoluto se calcula en save()
                    imp_saldo_insoluto=Decimal('0'),
                )
                messages.success(
                    request,
                    f'Pago de ${pago.monto} registrado. Parcialidad {num_parcialidad} de factura {factura.serie}{factura.folio}.'
                )
                return redirect('finanzas:pago_detalle', pk=pago.pk)
    else:
        initial = {}
        if factura_id_inicial:
            initial['factura'] = factura_id_inicial
        form = PagoForm(initial=initial)

    return render(request, 'finanzas/pago_form.html', {'form': form})


@modulo_required('Finanzas')
def pago_detalle(request, pk):
    pago = get_object_or_404(
        Pago.objects.prefetch_related('documentos__factura__configuracion_fiscal'),
        pk=pk,
    )
    return render(request, 'finanzas/pago_detalle.html', {'pago': pago})


@modulo_required('Finanzas')
def pago_timbrar(request, pk):
    """POST-only. Genera complemento de pago CFDI P y lo timbra ante el PAC."""
    import uuid as uuid_lib
    from .cfdi_generator import generar_xml_complemento_pago
    from .pac_client import PACConfigError, PACError, timbrar_cfdi

    if request.method != 'POST':
        return redirect('finanzas:pago_detalle', pk=pk)

    pago = get_object_or_404(
        Pago.objects.prefetch_related('documentos__factura__configuracion_fiscal'),
        pk=pk,
    )

    if pago.estado != 'PENDIENTE':
        messages.error(
            request,
            f'El pago ya está en estado "{pago.get_estado_display()}". Solo se pueden timbrar pagos Pendientes.'
        )
        return redirect('finanzas:pago_detalle', pk=pk)

    try:
        xml = generar_xml_complemento_pago(pago)
    except FileNotFoundError as e:
        messages.error(request, f'Archivo CSD no encontrado: {e}')
        return redirect('finanzas:pago_detalle', pk=pk)
    except ValueError as e:
        messages.error(request, f'Error de configuración: {e}')
        return redirect('finanzas:pago_detalle', pk=pk)
    except Exception as e:
        messages.error(request, f'Error generando complemento de pago: {e}')
        return redirect('finanzas:pago_detalle', pk=pk)

    try:
        resultado = timbrar_cfdi(xml)
    except PACConfigError as e:
        messages.error(request, f'Configuración PAC incompleta: {e}')
        return redirect('finanzas:pago_detalle', pk=pk)
    except PACError as e:
        messages.error(request, f'PAC rechazó el timbrado [{e.code}]: {e}')
        return redirect('finanzas:pago_detalle', pk=pk)
    except Exception as e:
        messages.error(request, f'Error de red al contactar el PAC: {e}')
        return redirect('finanzas:pago_detalle', pk=pk)

    pago.uuid_fiscal = uuid_lib.UUID(resultado['uuid'])
    pago.xml_timbrado = resultado['xml_timbrado']
    pago.estado = 'TIMBRADO'
    pago.save(update_fields=['uuid_fiscal', 'xml_timbrado', 'estado'])

    messages.success(request, f'Complemento de pago timbrado. UUID: {pago.uuid_fiscal}')
    return redirect('finanzas:pago_detalle', pk=pk)


@modulo_required('Finanzas')
def pago_descargar_xml(request, pk):
    """Descarga el XML timbrado del complemento de pago."""
    from django.http import HttpResponse

    pago = get_object_or_404(Pago, pk=pk, estado='TIMBRADO')
    if not pago.xml_timbrado:
        messages.error(request, 'Este pago no tiene XML timbrado.')
        return redirect('finanzas:pago_detalle', pk=pk)

    nombre_archivo = f'CompPago_{pago.uuid_fiscal}.xml'
    response = HttpResponse(pago.xml_timbrado, content_type='application/xml')
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


@modulo_required('Finanzas')
def factura_descargar_xml(request, pk):
    """Descarga el XML timbrado de una Factura TIMBRADA."""
    from django.http import HttpResponse

    factura = get_object_or_404(Factura, pk=pk, estado='TIMBRADA')
    if not factura.xml_timbrado:
        messages.error(request, 'Esta factura no tiene XML timbrado.')
        return redirect('finanzas:factura_detalle', pk=pk)

    nombre_archivo = f'CFDI_{factura.serie}{factura.folio}_{factura.uuid_fiscal}.xml'
    response = HttpResponse(factura.xml_timbrado, content_type='application/xml')
    response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
    return response


# ── Fase 7 — Balanza y exportación SAT ───────────────────────────────────────

@modulo_required('Finanzas')
def balanza_view(request):
    """Muestra la balanza de comprobación para el mes/año seleccionado."""
    from datetime import date
    from .models import ConfiguracionFiscal

    hoy = date.today()
    try:
        mes  = int(request.GET.get('mes',  hoy.month))
        anio = int(request.GET.get('anio', hoy.year))
    except (TypeError, ValueError):
        mes, anio = hoy.month, hoy.year

    mes  = max(1, min(12, mes))
    anio = max(2000, min(2100, anio))

    filas  = calcular_balanza(mes, anio)
    totals = totales_balanza(filas)
    configs = ConfiguracionFiscal.objects.filter(activa=True)

    return render(request, 'finanzas/balanza.html', {
        'filas':   filas,
        'totals':  totals,
        'mes':     mes,
        'anio':    anio,
        'configs': configs,
        'meses': [
            (1,'Enero'),(2,'Febrero'),(3,'Marzo'),(4,'Abril'),
            (5,'Mayo'),(6,'Junio'),(7,'Julio'),(8,'Agosto'),
            (9,'Septiembre'),(10,'Octubre'),(11,'Noviembre'),(12,'Diciembre'),
        ],
    })


@modulo_required('Finanzas')
def balanza_exportar_xml(request):
    """Descarga la balanza como XML SAT Contabilidad Electrónica v1.3."""
    from datetime import date
    from django.http import HttpResponse
    from .models import ConfiguracionFiscal
    from .utils import get_configuracion_fiscal

    hoy = date.today()
    try:
        mes        = int(request.GET.get('mes',    hoy.month))
        anio       = int(request.GET.get('anio',   hoy.year))
        patente    = request.GET.get('patente', '')
        tipo_envio = request.GET.get('tipo_envio', 'N')
    except (TypeError, ValueError):
        mes, anio = hoy.month, hoy.year
        patente, tipo_envio = '', 'N'

    if tipo_envio not in ('N', 'C', 'X'):
        tipo_envio = 'N'

    try:
        config = get_configuracion_fiscal(patente) if patente else ConfiguracionFiscal.objects.filter(activa=True).first()
        if not config:
            messages.error(request, 'No hay configuración fiscal activa.')
            return redirect('finanzas:balanza')
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('finanzas:balanza')

    xml = exportar_balanza_xml(mes, anio, config, tipo_envio)
    filename = nombre_archivo_balanza(mes, anio, config)
    response = HttpResponse(xml, content_type='application/xml')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@modulo_required('Finanzas')
def catalogo_cuentas_exportar(request):
    """Descarga el catálogo de cuentas como XML SAT Contabilidad Electrónica v1.3."""
    from datetime import date
    from django.http import HttpResponse
    from .models import ConfiguracionFiscal
    from .utils import get_configuracion_fiscal

    hoy = date.today()
    patente = request.GET.get('patente', '')
    try:
        mes  = int(request.GET.get('mes',  hoy.month))
        anio = int(request.GET.get('anio', hoy.year))
    except (TypeError, ValueError):
        mes, anio = hoy.month, hoy.year

    try:
        config = get_configuracion_fiscal(patente) if patente else ConfiguracionFiscal.objects.filter(activa=True).first()
        if not config:
            messages.error(request, 'No hay configuración fiscal activa.')
            return redirect('finanzas:balanza')
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('finanzas:balanza')

    xml = exportar_catalogo_cuentas_xml(config)
    filename = nombre_archivo_catalogo(anio, mes, config)
    response = HttpResponse(xml, content_type='application/xml')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@modulo_required('Finanzas')
def conciliacion_view(request):
    """Lista de movimientos bancarios del periodo con estado de conciliación."""
    from datetime import date

    hoy = date.today()
    try:
        mes       = int(request.GET.get('mes',  hoy.month))
        anio      = int(request.GET.get('anio', hoy.year))
        cuenta_id = int(request.GET.get('cuenta', 0))
    except (TypeError, ValueError):
        mes, anio, cuenta_id = hoy.month, hoy.year, 0

    cuentas = CuentaBancaria.objects.filter(activa=True)
    cuenta  = None
    movimientos = MovimientoBancario.objects.none()
    polizas_disponibles = []

    if cuenta_id:
        try:
            cuenta = CuentaBancaria.objects.get(pk=cuenta_id, activa=True)
            movimientos = (
                MovimientoBancario.objects
                .filter(cuenta=cuenta, mes=mes, anio=anio)
                .select_related('poliza')
                .order_by('fecha')
            )
            polizas_disponibles = (
                PolizaContable.objects
                .filter(mes=mes, anio=anio)
                .order_by('numero')
            )
        except CuentaBancaria.DoesNotExist:
            cuenta_id = 0

    stats = {
        'total':      movimientos.count(),
        'conciliados': movimientos.filter(conciliado=True).count(),
        'pendientes': movimientos.filter(conciliado=False).count(),
    }

    return render(request, 'finanzas/conciliacion.html', {
        'cuentas':             cuentas,
        'cuenta':              cuenta,
        'cuenta_id':           cuenta_id,
        'movimientos':         movimientos,
        'polizas_disponibles': polizas_disponibles,
        'mes':   mes,
        'anio':  anio,
        'stats': stats,
        'meses': [
            (1,'Enero'),(2,'Febrero'),(3,'Marzo'),(4,'Abril'),
            (5,'Mayo'),(6,'Junio'),(7,'Julio'),(8,'Agosto'),
            (9,'Septiembre'),(10,'Octubre'),(11,'Noviembre'),(12,'Diciembre'),
        ],
    })


@modulo_required('Finanzas')
def conciliacion_auto(request):
    """POST — ejecuta el motor automático para el periodo/cuenta indicado."""
    if request.method != 'POST':
        return redirect('finanzas:conciliacion')

    try:
        mes       = int(request.POST.get('mes',  0))
        anio      = int(request.POST.get('anio', 0))
        cuenta_id = int(request.POST.get('cuenta', 0))
    except (TypeError, ValueError):
        messages.error(request, 'Parámetros inválidos.')
        return redirect('finanzas:conciliacion')

    resultado = conciliar_automatico(mes, anio, cuenta_id)
    n_auto     = len(resultado['conciliados'])
    n_suger    = len(resultado['sugeridos'])
    n_sin      = len(resultado['sin_match'])

    messages.success(
        request,
        f'Conciliación automática: {n_auto} conciliados automáticamente, '
        f'{n_suger} sugeridos (requieren confirmación), {n_sin} sin match.'
    )
    return redirect(
        f"{request.build_absolute_uri('/')[:-1]}"
        f"{request.path_info.replace('/auto/', '/')}?mes={mes}&anio={anio}&cuenta={cuenta_id}"
    )


@modulo_required('Finanzas')
def confirmar_conciliacion(request, movimiento_id):
    """POST — confirma el vínculo entre un MovimientoBancario y una PolizaContable."""
    if request.method != 'POST':
        return redirect('finanzas:conciliacion')

    poliza_id = request.POST.get('poliza_id')
    if not poliza_id:
        messages.error(request, 'Debes seleccionar una póliza.')
        return redirect('finanzas:conciliacion')

    try:
        mov = confirmar_match(movimiento_id, int(poliza_id))
        messages.success(
            request,
            f'Movimiento del {mov.fecha} conciliado con póliza {mov.poliza.numero}.'
        )
    except Exception as e:
        messages.error(request, f'Error al conciliar: {e}')

    # Regresar a la vista con los mismos filtros
    cuenta_id = request.POST.get('cuenta_id', '')
    mes       = request.POST.get('mes', '')
    anio      = request.POST.get('anio', '')
    return redirect(f'/finanzas/conciliacion/?mes={mes}&anio={anio}&cuenta={cuenta_id}')


@modulo_required('Finanzas')
def comisiones_reporte(request):
    """Reporte de comisiones por referencia con filtro mes/año y botón de recalcular."""
    from datetime import date

    hoy = date.today()
    try:
        mes  = int(request.GET.get('mes',  hoy.month))
        anio = int(request.GET.get('anio', hoy.year))
    except (TypeError, ValueError):
        mes, anio = hoy.month, hoy.year

    if request.method == 'POST':
        # Recalcular
        try:
            mes_p  = int(request.POST.get('mes',  mes))
            anio_p = int(request.POST.get('anio', anio))
        except (TypeError, ValueError):
            mes_p, anio_p = mes, anio
        comisiones = calcular_comisiones_mes(mes_p, anio_p)
        messages.success(
            request,
            f'{len(comisiones)} comisiones calculadas para {mes_p:02d}/{anio_p}.'
        )
        return redirect(f'/finanzas/comisiones/?mes={mes_p}&anio={anio_p}')

    comisiones = (
        ComisionReferencia.objects
        .filter(mes=mes, anio=anio)
        .select_related('referencia', 'agente')
        .order_by('referencia__num_refe')
    )

    total_valor    = sum(c.valor_operacion for c in comisiones)
    total_comision = sum(c.monto_comision  for c in comisiones)

    return render(request, 'finanzas/comisiones.html', {
        'comisiones':     comisiones,
        'total_valor':    total_valor,
        'total_comision': total_comision,
        'mes':  mes,
        'anio': anio,
        'meses': [
            (1,'Enero'),(2,'Febrero'),(3,'Marzo'),(4,'Abril'),
            (5,'Mayo'),(6,'Junio'),(7,'Julio'),(8,'Agosto'),
            (9,'Septiembre'),(10,'Octubre'),(11,'Noviembre'),(12,'Diciembre'),
        ],
    })


@modulo_required('Finanzas')
def comisiones_exportar_csv(request):
    """Descarga CSV: referencia, cliente, agente, valor_op, tasa, comisión."""
    import csv
    from datetime import date
    from django.http import HttpResponse

    hoy = date.today()
    try:
        mes  = int(request.GET.get('mes',  hoy.month))
        anio = int(request.GET.get('anio', hoy.year))
    except (TypeError, ValueError):
        mes, anio = hoy.month, hoy.year

    comisiones = (
        ComisionReferencia.objects
        .filter(mes=mes, anio=anio)
        .select_related('referencia', 'agente')
        .order_by('referencia__num_refe')
    )

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="comisiones_{anio}{mes:02d}.csv"'
    response.write('﻿')  # BOM para Excel

    writer = csv.writer(response)
    writer.writerow(['Referencia', 'Cliente', 'Agente', 'Valor Operación', 'Tasa', 'Comisión', 'Mes', 'Año'])
    for c in comisiones:
        ref     = c.referencia
        cliente = getattr(ref, 'nombre_cliente', getattr(ref, 'cliente', ''))
        agente  = c.agente.get_full_name() if c.agente else ''
        writer.writerow([
            ref.num_refe,
            str(cliente),
            agente,
            str(c.valor_operacion),
            str(c.tasa_comision),
            str(c.monto_comision),
            c.mes,
            c.anio,
        ])

    return response


@modulo_required('Finanzas')
def cierre_list(request):
    """Lista de cierres mensuales realizados."""
    cierres = CierreMensual.objects.select_related('cerrado_por').order_by('-anio', '-mes')
    return render(request, 'finanzas/cierre_list.html', {'cierres': cierres})


@modulo_required('Finanzas')
def cierre_ejecutar(request):
    """GET: formulario de cierre. POST: ejecuta el cierre y muestra resultado."""
    from datetime import date
    from .models import ConfiguracionFiscal

    configs = ConfiguracionFiscal.objects.filter(activa=True)
    hoy = date.today()
    meses = [
        (1,'Enero'),(2,'Febrero'),(3,'Marzo'),(4,'Abril'),
        (5,'Mayo'),(6,'Junio'),(7,'Julio'),(8,'Agosto'),
        (9,'Septiembre'),(10,'Octubre'),(11,'Noviembre'),(12,'Diciembre'),
    ]

    if request.method == 'POST':
        try:
            mes     = int(request.POST.get('mes', 0))
            anio    = int(request.POST.get('anio', 0))
            patente = request.POST.get('patente', '').strip()
            obs     = request.POST.get('observaciones', '').strip()
        except (TypeError, ValueError):
            messages.error(request, 'Parámetros inválidos.')
            return render(request, 'finanzas/cierre_form.html', {
                'configs': configs, 'hoy': hoy, 'meses': meses,
            })

        if not patente:
            messages.error(request, 'Debes seleccionar una patente.')
            return render(request, 'finanzas/cierre_form.html', {
                'configs': configs, 'hoy': hoy, 'meses': meses,
            })

        # Verificar si ya existe cierre
        if CierreMensual.objects.filter(mes=mes, anio=anio, patente=patente).exists():
            messages.error(
                request,
                f'Ya existe un cierre para {mes:02d}/{anio} — Patente {patente}.'
            )
            return render(request, 'finanzas/cierre_form.html', {
                'configs': configs, 'hoy': hoy,
            })

        try:
            cierre = ejecutar_cierre_mensual(mes, anio, patente, request.user, obs)
            messages.success(
                request,
                f'Cierre {mes:02d}/{anio} — Patente {patente} ejecutado correctamente. '
                f'{cierre.total_polizas} pólizas congeladas.'
            )
            return redirect('finanzas:cierre_list')
        except CierreError as e:
            for problema in e.problemas:
                messages.error(request, problema)
        except Exception as e:
            messages.error(request, f'Error inesperado: {e}')

        return render(request, 'finanzas/cierre_form.html', {
            'configs': configs, 'hoy': hoy, 'meses': meses,
        })

    return render(request, 'finanzas/cierre_form.html', {
        'configs': configs, 'hoy': hoy, 'meses': meses,
    })


@modulo_required('Finanzas')
def cierre_exportar_paquete(request, pk):
    """Descarga un ZIP con los 5 XMLs SAT para el cierre mensual."""
    import io
    import zipfile
    from django.http import HttpResponse
    from .exportar_sat import (
        exportar_balanza_xml, exportar_catalogo_cuentas_xml, exportar_polizas_xml,
        nombre_archivo_balanza, nombre_archivo_catalogo, nombre_archivo_polizas,
    )
    from .models import ConfiguracionFiscal
    from .utils import get_configuracion_fiscal

    cierre = get_object_or_404(CierreMensual, pk=pk)
    try:
        config = get_configuracion_fiscal(cierre.patente)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('finanzas:cierre_list')

    mes, anio = cierre.mes, cierre.anio
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Catálogo de cuentas
        zf.writestr(
            nombre_archivo_catalogo(anio, mes, config),
            exportar_catalogo_cuentas_xml(config).encode('utf-8'),
        )
        # Balanza de comprobación
        zf.writestr(
            nombre_archivo_balanza(mes, anio, config),
            exportar_balanza_xml(mes, anio, config).encode('utf-8'),
        )
        # Pólizas: Diario, Ingreso, Egreso
        for tipo in ('D', 'H', 'E'):
            zf.writestr(
                nombre_archivo_polizas(mes, anio, tipo, config),
                exportar_polizas_xml(mes, anio, config, tipo).encode('utf-8'),
            )

    buf.seek(0)
    nombre_zip = f'ContabilidadE_{config.rfc}_{anio}{mes:02d}.zip'
    response = HttpResponse(buf.read(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{nombre_zip}"'
    return response


@modulo_required('Finanzas')
def polizas_exportar_xml(request):
    """Descarga las pólizas del periodo como XML SAT Contabilidad Electrónica v1.3."""
    from datetime import date
    from django.http import HttpResponse
    from .models import ConfiguracionFiscal
    from .utils import get_configuracion_fiscal

    hoy = date.today()
    try:
        mes     = int(request.GET.get('mes',  hoy.month))
        anio    = int(request.GET.get('anio', hoy.year))
        tipo    = request.GET.get('tipo', 'D')
        patente = request.GET.get('patente', '')
    except (TypeError, ValueError):
        mes, anio, tipo, patente = hoy.month, hoy.year, 'D', ''

    if tipo not in ('D', 'H', 'E'):
        tipo = 'D'

    try:
        config = get_configuracion_fiscal(patente) if patente else ConfiguracionFiscal.objects.filter(activa=True).first()
        if not config:
            messages.error(request, 'No hay configuración fiscal activa.')
            return redirect('finanzas:balanza')
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('finanzas:balanza')

    xml = exportar_polizas_xml(mes, anio, config, tipo)
    filename = nombre_archivo_polizas(mes, anio, tipo, config)
    response = HttpResponse(xml, content_type='application/xml')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
