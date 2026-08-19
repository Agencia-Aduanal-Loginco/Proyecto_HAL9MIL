"""Tests para el management command import_firebird — bandera --no-notificar.

El primer sync tras el deploy de esta feature encontraría TODAS las DODAs
abiertas de Transportes Kasu como "creadas" (created=True), porque la BD
local aún no tiene ninguna. Si procesar_dodas_nuevas corriera sobre ese
primer lote, dispararía un correo a cada capturista y un push a un
endpoint de BitacoraKasu que probablemente ni siquiera existe todavía.

import_firebird NO llama a procesar_dodas_nuevas (eso es una brecha de
paridad deliberadamente fuera de alcance — ver spec). La bandera
--no-notificar sólo controla si las DODAs recién creadas por este import
quedan marcadas como "ya atendidas" (notificado_en / modulacion_enviada_en
= now) para que reintentar_modulacion no las recoja después.

Estos tests mockean la conexión Firebird (connect / cursor) para no
depender de un servidor Firebird real; sólo se pobla la tabla SAAIO_DODA
vía el cursor falso, el resto de las queries (referencias, contenedores,
guías) devuelven listas vacías.
"""
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from referencias.management.commands import import_firebird as cmd_mod
from referencias.models import Doda, EnvioModulacion


class _FakeCursor:
    """Cursor Firebird falso: sólo responde con filas para la query de
    SAAIO_DODA/SAAIO_DODADO; el resto de fetch_* del comando reciben listas
    vacías (sin efecto sobre referencias/contenedores/guías)."""

    def __init__(self, doda_rows):
        self._doda_rows = doda_rows
        self._sql = ''

    def execute(self, sql, params=None):
        self._sql = sql

    def fetchall(self):
        sql_upper = self._sql.upper()
        if 'SAAIO_DODA' in sql_upper and 'SAAIO_DODADO' in sql_upper:
            return self._doda_rows
        return []


class _FakeConnection:
    def __init__(self, doda_rows):
        self._doda_rows = doda_rows

    def cursor(self):
        return _FakeCursor(self._doda_rows)

    def close(self):
        pass


def _doda_row(id_doda, num_doda):
    """Fila cruda tal como la devuelve fetch_dodas' query (sin referencias
    ligadas — num_refe/cons_id en NULL — y sin terminal resuelta)."""
    return (
        id_doda, num_doda, '3B74', 'CAPT01',
        None, None,   # FEC_DODAE, FEC_BAJA
        None, None,   # NUM_REFE, CONS_ID (SAAIO_DODADO)
        None, None,   # CVE_REFI, NOM_REFI (terminal)
    )


@patch.object(cmd_mod, 'connect')
class ImportFirebirdNoNotificarTests(TestCase):
    def test_no_notificar_marca_notificado_en_y_modulacion_enviada_en_sin_crear_envio(self, mock_connect):
        mock_connect.return_value = _FakeConnection([_doda_row(77001, 'DODA-77001')])

        call_command('import_firebird', '--no-notificar', '--patentes', '1656')

        doda = Doda.objects.get(id_doda=77001)
        self.assertIsNotNone(doda.notificado_en)
        self.assertIsNotNone(doda.modulacion_enviada_en)
        self.assertEqual(EnvioModulacion.objects.count(), 0)

    def test_sin_flag_deja_notificado_en_y_modulacion_enviada_en_en_null(self, mock_connect):
        mock_connect.return_value = _FakeConnection([_doda_row(77002, 'DODA-77002')])

        call_command('import_firebird', '--patentes', '1656')

        doda = Doda.objects.get(id_doda=77002)
        self.assertIsNone(doda.notificado_en)
        self.assertIsNone(doda.modulacion_enviada_en)
        self.assertEqual(EnvioModulacion.objects.count(), 0)

    def test_no_notificar_no_afecta_dodas_ya_existentes_actualizadas(self, mock_connect):
        """Sólo las DODAs recién CREADAS por este import deben marcarse;
        una DODA que ya existía (y por lo tanto no pasó por notificación
        nueva en este import) no debe verse tocada retroactivamente."""
        existente = Doda.objects.create(
            id_doda=77003, num_doda='DODA-77003', patente='1656', cve_caat='3B74',
        )
        mock_connect.return_value = _FakeConnection([_doda_row(77003, 'DODA-77003-ACTUALIZADA')])

        call_command('import_firebird', '--no-notificar', '--patentes', '1656')

        existente.refresh_from_db()
        self.assertIsNone(existente.notificado_en)
        self.assertIsNone(existente.modulacion_enviada_en)
