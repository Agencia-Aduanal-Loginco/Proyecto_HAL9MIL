from django.urls import path
from . import views

app_name = 'finanzas'

urlpatterns = [
    # Cobranza de honorarios
    path('cobranza/', views.cobranza_list, name='cobranza_list'),
    path('cobranza/<str:cve_cliente>/enviar/', views.cobranza_enviar, name='cobranza_enviar'),
    path('', views.dashboard_financiero, name='dashboard'),
    path('anticipos/', views.anticipos_list, name='anticipos_list'),
    path('gastos/', views.gastos_list, name='gastos_list'),
    path('polizas/', views.polizas_list, name='polizas_list'),
    path('polizas/<int:pk>/', views.poliza_detalle, name='poliza_detalle'),
    # Fase 4 — Cobranza y facturas
    path('referencias-por-facturar/', views.referencias_por_facturar, name='referencias_por_facturar'),
    path('facturas/', views.facturas_list, name='facturas_list'),
    path('facturas/<int:pk>/', views.factura_detalle, name='factura_detalle'),
    path('facturas/<int:pk>/timbrar/', views.factura_timbrar, name='factura_timbrar'),
    path('facturas/<int:pk>/xml/', views.factura_descargar_xml, name='factura_xml'),
    # Fase 6 — Pagos / Complemento de pago
    path('pagos/', views.pagos_list, name='pagos_list'),
    path('pagos/nuevo/', views.pago_registrar, name='pago_registrar'),
    path('pagos/<int:pk>/', views.pago_detalle, name='pago_detalle'),
    path('pagos/<int:pk>/timbrar/', views.pago_timbrar, name='pago_timbrar'),
    path('pagos/<int:pk>/xml/', views.pago_descargar_xml, name='pago_xml'),
    # Fase 7 — Balanza y exportación Contabilidad Electrónica SAT
    path('balanza/', views.balanza_view, name='balanza'),
    path('balanza/exportar-xml/', views.balanza_exportar_xml, name='balanza_exportar_xml'),
    path('catalogo-cuentas/exportar-xml/', views.catalogo_cuentas_exportar, name='catalogo_xml'),
    path('polizas/exportar-xml/', views.polizas_exportar_xml, name='polizas_exportar_xml'),
    # Fase 10 — Comisiones
    path('comisiones/', views.comisiones_reporte, name='comisiones_reporte'),
    path('comisiones/exportar/', views.comisiones_exportar_csv, name='comisiones_csv'),
    # Fase 9 — Cierre mensual
    path('cierre/', views.cierre_list, name='cierre_list'),
    path('cierre/ejecutar/', views.cierre_ejecutar, name='cierre_ejecutar'),
    path('cierre/<int:pk>/exportar/', views.cierre_exportar_paquete, name='cierre_exportar'),
    # Fase 8 — Conciliación bancaria
    path('conciliacion/', views.conciliacion_view, name='conciliacion'),
    path('conciliacion/auto/', views.conciliacion_auto, name='conciliacion_auto'),
    path('conciliacion/confirmar/<int:movimiento_id>/', views.confirmar_conciliacion, name='confirmar_conciliacion'),
    # Carga masiva de XMLs de proveedor
    path('xml/carga-masiva/', views.carga_masiva_xml, name='carga_masiva_xml'),
    path('xml/pendientes/', views.xml_pendientes, name='xml_pendientes'),
    path('xml-proveedor/<int:pk>/pdf/', views.xml_proveedor_ver_pdf, name='xml_proveedor_ver_pdf'),
    # Rutas por referencia — <path:> porque num_refe contiene "/"  (ej. LCLF0331/26)
    path('referencias/<path:num_refe>/estado/', views.referencia_estado_financiero, name='referencia_estado'),
    path('referencias/<path:num_refe>/anticipo/', views.anticipo_crear, name='anticipo_crear'),
    path('referencias/<path:num_refe>/gasto/', views.gasto_crear, name='gasto_crear'),
    path('referencias/<path:num_refe>/xml-proveedor/', views.subir_xml_proveedor, name='subir_xml'),
    path('referencias/<path:num_refe>/factura/', views.factura_crear, name='factura_crear'),
]
