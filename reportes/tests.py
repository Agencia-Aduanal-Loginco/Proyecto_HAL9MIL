import calendar
from datetime import date, datetime
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.utils import timezone

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError

from referencias.models import CuentaGastos, Referencia
from reportes.data import NOMBRES_MESES, _proyeccion_mes, get_datos_mes
from reportes.jobs import _wa_ia_modulo, enviar_reporte_mensual
from reportes.models import Destinatario, HistorialReporte


@override_settings(
    TWILIO_CONTENT_SID_IA_HAL9MIL='HX1495d5e097d913edca34109f4336e012'
)
class WaIaModuloTests(TestCase):
    def setUp(self):
        Destinatario.objects.create(
            nombre='Prueba',
            email='prueba@example.com',
            activo=True,
            whatsapp='5217535342088',
            recibe_wa_ia_referencias=True,
        )

    @patch('whatsapp.client.send_template')
    def test_variable_ia_no_contiene_saltos_de_linea(self, mock_send):
        # Salida típica de Claude: párrafos y viñetas separados por '\n'.
        texto_ia = (
            "**Reporte Ejecutivo**\n\n"
            "Durante la semana se validaron 68 operaciones (LCLF 32, LCRR 28, LCMJ 8).\n\n"
            "Recomendaciones:\n"
            "- Reforzar el equipo de validación.\n"
            "- Priorizar LCMJ para reducir el acumulado."
        )

        _wa_ia_modulo(texto_ia, 'referencias', '01/06 – 07/06/2026')

        self.assertTrue(mock_send.called, 'send_template no fue invocado')
        (_numero, _sid, variables), _kwargs = mock_send.call_args
        self.assertNotIn('\n', variables['1'])
        self.assertNotIn('\t', variables['1'])
        self.assertNotIn('  ', variables['1'])
        # Twilio limita cada variable; el código apunta a <= 400 chars.
        self.assertLessEqual(len(variables['1']), 400)


@patch('reportes.jobs._wa_ia_modulo')
@patch('reportes.jobs._wa_mensual')
@patch('reportes.jobs.analizar_mensual', return_value='analisis de prueba')
class ReenvioReporteMensualTests(TestCase):
    """Reenvío manual del reporte mensual por consola, incluso si el periodo ya
    se envió. El reenvío forzado es solo correo y reemplaza el historial."""

    def _periodo(self):
        today = date.today()
        mes = 12 if today.month == 1 else today.month - 1
        year = today.year - 1 if today.month == 1 else today.year
        inicio = date(year, mes, 1)
        fin = date(year, mes, calendar.monthrange(year, mes)[1])
        return inicio, fin

    def _historial_previo(self):
        inicio, fin = self._periodo()
        return HistorialReporte.objects.create(
            tipo='mensual', periodo_inicio=inicio, periodo_fin=fin,
            destinatarios='[]', exitoso=True,
        )

    def test_sin_force_no_reenvia_si_el_periodo_ya_se_envio(self, *mocks):
        self._historial_previo()

        enviar_reporte_mensual()

        self.assertEqual(len(mail.outbox), 0)

    def test_con_force_reenvia_aunque_el_periodo_ya_se_envio(self, *mocks):
        self._historial_previo()

        enviar_reporte_mensual(force=True)

        self.assertEqual(len(mail.outbox), 1)

    def test_con_force_no_manda_whatsapp(self, _ia, wa_mensual, wa_ia_modulo):
        self._historial_previo()

        enviar_reporte_mensual(force=True)

        self.assertFalse(wa_mensual.called)
        self.assertFalse(wa_ia_modulo.called)

    def test_sin_force_si_manda_whatsapp(self, _ia, wa_mensual, wa_ia_modulo):
        enviar_reporte_mensual()

        self.assertTrue(wa_mensual.called)
        self.assertTrue(wa_ia_modulo.called)

    def test_con_force_reemplaza_el_historial_previo(self, *mocks):
        previo = self._historial_previo()
        inicio, _fin = self._periodo()

        enviar_reporte_mensual(force=True)

        exitosos = HistorialReporte.objects.filter(
            tipo='mensual', periodo_inicio=inicio, exitoso=True,
        )
        self.assertEqual(exitosos.count(), 1)
        self.assertNotEqual(exitosos.first().id, previo.id)

    def test_solo_a_envia_unicamente_a_esos_correos(self, *mocks):
        enviar_reporte_mensual(force=True, solo_a=['prueba@example.com'])

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['prueba@example.com'])

    def test_solo_a_no_registra_historial(self, *mocks):
        enviar_reporte_mensual(force=True, solo_a=['prueba@example.com'])

        self.assertEqual(HistorialReporte.objects.count(), 0)

    def test_solo_a_no_borra_el_historial_previo(self, *mocks):
        previo = self._historial_previo()

        enviar_reporte_mensual(force=True, solo_a=['prueba@example.com'])

        self.assertTrue(HistorialReporte.objects.filter(id=previo.id).exists())


class ComandoEnviarReporteTests(TestCase):
    @patch('reportes.management.commands.enviar_reporte.enviar_reporte_mensual')
    def test_force_y_solo_a_se_pasan_al_job(self, job):
        call_command('enviar_reporte', 'mensual', '--force', '--solo-a', 'a@b.com')

        job.assert_called_once_with(force=True, solo_a=['a@b.com'])

    @patch('reportes.management.commands.enviar_reporte.enviar_reporte_mensual')
    def test_sin_flags_invoca_el_job_sin_forzar(self, job):
        call_command('enviar_reporte', 'mensual')

        job.assert_called_once_with(force=False, solo_a=None)

    def test_force_con_semanal_es_error(self):
        with self.assertRaises(CommandError):
            call_command('enviar_reporte', 'semanal', '--force')


class ProyeccionMesTests(TestCase):
    def test_sin_historico_2024_2025_regresa_cero_sin_dividir_entre_cero(self):
        self.assertEqual(_proyeccion_mes(5), 0)


class ProcesoCompletoMesTests(TestCase):
    """Apartado "Proceso completo" del reporte mensual: elaboradas, pagadas y
    cuentas de gastos finalizadas dentro del mes."""

    YEAR = 2026
    MONTH = 6

    _contador = 0

    def _ref(self, **kwargs):
        ProcesoCompletoMesTests._contador += 1
        datos = {
            'num_refe': f'REF{ProcesoCompletoMesTests._contador:04d}',
            'patente': '3604',
            'prefijo': 'LC',
        }
        datos.update(kwargs)
        return Referencia.objects.create(**datos)

    def _cg(self, referencia, fecha_finalizacion):
        return CuentaGastos.objects.create(
            referencia=referencia,
            fecha_finalizacion=timezone.make_aware(fecha_finalizacion),
        )

    def _proceso_completo(self):
        return get_datos_mes(self.YEAR, self.MONTH)['proceso_completo']

    def test_elaboradas_cuenta_referencias_con_fecha_captura_en_el_mes(self):
        self._ref(fecha_captura=date(2026, 6, 10))
        self._ref(fecha_captura=date(2026, 5, 31))

        self.assertEqual(self._proceso_completo()['elaboradas'], 1)

    def test_elaboradas_excluye_rectificaciones(self):
        self._ref(fecha_captura=date(2026, 6, 10))
        self._ref(fecha_captura=date(2026, 6, 11), es_rectificacion=True)

        self.assertEqual(self._proceso_completo()['elaboradas'], 1)

    def test_pagadas_requiere_num_operacion_y_linea_captura(self):
        self._ref(fecha_pago=date(2026, 6, 15), num_operacion='OP1', linea_captura='LC1')
        self._ref(fecha_pago=date(2026, 6, 16), linea_captura='LC2')
        self._ref(fecha_pago=date(2026, 6, 17), num_operacion='OP3')

        self.assertEqual(self._proceso_completo()['pagadas'], 1)

    def test_pagadas_excluye_pagos_fuera_del_mes(self):
        self._ref(fecha_pago=date(2026, 6, 15), num_operacion='OP1', linea_captura='LC1')
        self._ref(fecha_pago=date(2026, 7, 1), num_operacion='OP2', linea_captura='LC2')

        self.assertEqual(self._proceso_completo()['pagadas'], 1)

    def test_cg_finalizadas_cuenta_por_fecha_finalizacion_en_el_mes(self):
        self._cg(self._ref(fecha_pago=date(2026, 6, 20)), datetime(2026, 6, 25, 12, 0))
        self._cg(self._ref(fecha_pago=date(2026, 6, 21)), datetime(2026, 7, 2, 12, 0))

        self.assertEqual(self._proceso_completo()['cg_finalizadas'], 1)

    def _render(self):
        datos = get_datos_mes(self.YEAR, self.MONTH)
        return render_to_string('reportes/mensual.html', {
            'datos': datos,
            'analisis_ia': '',
            'nombre_mes': datos['nombre_mes'],
            'nombres_meses': NOMBRES_MESES,
        })

    def test_correo_mensual_muestra_apartado_proceso_completo(self):
        self._cg(self._ref(fecha_captura=date(2026, 6, 2), fecha_pago=date(2026, 6, 15),
                           num_operacion='OP1', linea_captura='LC1'),
                 datetime(2026, 6, 25, 12, 0))

        html = self._render()

        self.assertIn('Proceso completo', html)
        self.assertIn('referencias capturadas', html)
        self.assertIn('con operación y línea de captura', html)
        self.assertIn('finalizadas en el mes', html)

    def test_apartado_proceso_completo_va_antes_del_resto_del_cuerpo(self):
        html = self._render()

        self.assertLess(
            html.index('Proceso completo'),
            html.index('Desempeño vs Proyección'),
            'El apartado debe ir al inicio del cuerpo del correo',
        )
