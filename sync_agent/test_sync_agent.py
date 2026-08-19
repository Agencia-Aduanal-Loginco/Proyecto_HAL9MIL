#!/usr/bin/env python3
"""
test_sync_agent.py — Pruebas standalone para sync_agent.py (sin Django, sin Firebird).

sync_agent.py corre fuera del proyecto Django (servidores Windows con solo Firebird
instalado), así que sus pruebas no pueden depender de `manage.py test` ni de una
conexión real a Firebird. Este archivo prueba en aislamiento la lógica que sí se
puede ejercitar con datos en memoria: la conversión de tipos de fetch_embar() y la
serializabilidad JSON del payload que arma build_payload().

Uso:
    python sync_agent/test_sync_agent.py
    python -m unittest sync_agent.test_sync_agent -v
"""
import decimal
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sync_agent as sa  # noqa: E402


class _FakeCursor:
    """Cursor falso que imita el contrato mínimo usado por _fetch_rows: un solo
    execute() sin filtro (refs_filter=None) seguido de fetchall()."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self._rows


class FetchContenedoresTipoMapeoTests(unittest.TestCase):
    """Bug real reproducido en producción: SAAIO_CONTEN.CVE_CONT es VARCHAR(2)
    en CASA.GDB (Firebird) — fdb siempre lo entrega como str. CVE_CONT_TIPO
    tenía claves int, así que CVE_CONT_TIPO.get(cve_cont, '') nunca
    matcheaba: el 100% de los contenedores en producción (28,772/28,772)
    quedaban con tipo=''. Este test fija el contrato: fetch_contenedores()
    debe mapear correctamente cuando CVE_CONT llega como string (el caso
    real), y debe seguir siendo tolerante a un valor no mapeado."""

    def test_cve_cont_como_string_mapea_tipo_correcto(self):
        cur = _FakeCursor([
            ('REF001', 'CONT001', '3'),   # 40HC
            ('REF001', 'CONT002', '1'),   # 20DC
        ])
        result = sa.fetch_contenedores(cur)
        tipos = {c['num_cont']: c['tipo'] for c in result['REF001']}
        self.assertEqual(tipos['CONT001'], '40HC')
        self.assertEqual(tipos['CONT002'], '20DC')

    def test_cve_cont_no_mapeado_cae_a_vacio_sin_reventar(self):
        cur = _FakeCursor([('REF001', 'CONT099', '77')])
        result = sa.fetch_contenedores(cur)
        self.assertEqual(result['REF001'][0]['tipo'], '')

    def test_cve_cont_none_cae_a_vacio(self):
        cur = _FakeCursor([('REF001', 'CONT099', None)])
        result = sa.fetch_contenedores(cur)
        self.assertEqual(result['REF001'][0]['tipo'], '')


class FetchEmbarDecimalTests(unittest.TestCase):
    """El driver fdb instalado devuelve decimal.Decimal para columnas NUMERIC/DECIMAL
    con escala (como PES_BRUT). fetch_embar() debe castear a float, no pasar el
    Decimal crudo, porque json.dumps() no sabe serializar Decimal."""

    def test_peso_bruto_decimal_is_cast_to_float(self):
        cur = _FakeCursor([
            ('REF001', None, decimal.Decimal('12345.678')),
        ])
        result = sa.fetch_embar(cur)
        self.assertIn('REF001', result)
        peso = result['REF001']['peso_bruto']
        self.assertIsInstance(peso, float)
        self.assertNotIsInstance(peso, decimal.Decimal)
        self.assertAlmostEqual(peso, 12345.678)

    def test_peso_bruto_none_stays_none(self):
        cur = _FakeCursor([
            ('REF002', None, None),
        ])
        result = sa.fetch_embar(cur)
        self.assertIsNone(result['REF002']['peso_bruto'])


class BuildPayloadJsonSerializableTests(unittest.TestCase):
    """Reproduce el bug del hallazgo crítico: antes del fix, un peso_bruto Decimal
    sin castear hacía que json.dumps() (invocado por requests vía json=payload en
    send_payload()) reventara con TypeError para el envío COMPLETO, no solo para
    el campo peso_bruto."""

    def test_payload_with_peso_bruto_is_json_serializable(self):
        cur = _FakeCursor([
            ('REF001', None, decimal.Decimal('998.500')),
        ])
        embar = sa.fetch_embar(cur)

        payload = sa.build_payload(
            clientes={}, capturistas={}, embar=embar, pedimentos={},
            all_refs={'REF001'}, pedime2={}, contenedores={}, guias={},
            partidas_count={}, proces={}, regval={},
        )

        # Esto es exactamente lo que hace `requests` internamente cuando se le
        # pasa json=payload en send_payload().
        serialized = json.dumps(payload)
        self.assertIn('998.5', serialized)

        roundtrip = json.loads(serialized)
        self.assertEqual(roundtrip['referencias'][0]['peso_bruto'], 998.5)

    def test_payload_shape_still_json_serializable_directly(self):
        """Verificación adicional shaped como el hallazgo: un dict con la forma de
        una entrada de refs_list y un Decimal ya convertido a float debe serializar
        sin problema (documenta el contrato esperado de peso_bruto en el payload)."""
        entry = {
            'num_refe': 'REF001',
            'peso_bruto': float(decimal.Decimal('12345.678')),
        }
        json.dumps(entry)  # no debe lanzar TypeError


class DodasSurviveEmptyAllRefsTests(unittest.TestCase):
    """Hallazgo importante: build_payload() debe poder mandar un bloque 'dodas' no
    vacío incluso cuando all_refs está vacío (referencias/contenedores/guias vacíos),
    que es la forma de payload que main() ahora envía cuando no hay refs con
    pedimentos pero sí hay DODAs pendientes."""

    def test_build_payload_with_empty_all_refs_and_dodas(self):
        dodas = [{
            'id_doda': 1, 'num_doda': 'D-1', 'patente': '1627',
            'cve_caat': '3B74', 'cve_capt': 'USR', 'terminal_cve': 'T1',
            'terminal_nombre': 'Terminal 1', 'fecha_doda': '2026-08-01T00:00:00',
            'fecha_baja': None, 'referencias': [],
        }]
        payload = sa.build_payload(
            clientes={}, capturistas={}, embar={}, pedimentos={},
            all_refs=set(), pedime2={}, contenedores={}, guias={},
            partidas_count={}, proces={}, regval={}, dodas=dodas,
        )
        self.assertEqual(payload['referencias'], [])
        self.assertEqual(payload['contenedores'], [])
        self.assertEqual(payload['guias'], [])
        self.assertEqual(payload['dodas'], dodas)
        # También debe ser serializable de punta a punta.
        json.dumps(payload)


class ChunkListTests(unittest.TestCase):
    """sa._chunk_list() es el helper genérico que usa enviar_dodas_en_lotes()
    para trocear la lista completa de DODAs en tandas propias."""

    def test_chunk_list_exact_multiple(self):
        items = list(range(6))
        self.assertEqual(sa._chunk_list(items, 2), [[0, 1], [2, 3], [4, 5]])

    def test_chunk_list_remainder(self):
        items = list(range(5))
        self.assertEqual(sa._chunk_list(items, 2), [[0, 1], [2, 3], [4]])

    def test_chunk_list_size_larger_than_list(self):
        items = [1, 2, 3]
        self.assertEqual(sa._chunk_list(items, 100), [[1, 2, 3]])

    def test_chunk_list_empty(self):
        self.assertEqual(sa._chunk_list([], 10), [])


class DodasEnviadasAparteDeLosLotesDeRefsTests(unittest.TestCase):
    """Hallazgo crítico (real, reproducido en producción): las DODAs se
    mandaban TODAS juntas en un solo payload dentro del último lote de refs
    (`chunk_dodas = dodas if idx == n_chunks else []`). En la primera
    sincronización de una patente esto significa miles de DODAs (7032 en el
    caso observado) en un solo POST — Django procesa cada DODA nueva de
    forma síncrona dentro de la misma petición HTTP (PDF + correo SendGrid +
    push por contenedor a BitacoraKasu), lo que agota el timeout (120s) y
    hace fallar el sync completo después de MAX_RETRIES.

    La arquitectura correcta es: los lotes de refs/contenedores/guías NO
    llevan 'dodas' (build_payload() sin el argumento dodas → lista vacía), y
    las DODAs se mandan en tandas propias, separadas, DESPUÉS de que todos
    los lotes de refs ya se enviaron — para que Django siempre tenga la
    Referencia/Contenedor de la DODA ya comprometida en la BD.

    Este test reproduce ese mismo orden con las funciones reales de
    producción (build_payload + _chunk_list), sin llamar a main() (requiere
    Firebird)."""

    def _payloads_de_refs(self, all_refs, chunk_size):
        refs_sorted = sorted(all_refs)
        chunks = [refs_sorted[i:i + chunk_size] for i in range(0, len(refs_sorted), chunk_size)]
        return [
            sa.build_payload(
                clientes={}, capturistas={}, embar={}, pedimentos={},
                all_refs=set(chunk), pedime2={}, contenedores={}, guias={},
                partidas_count={}, proces={}, regval={},
            )
            for chunk in chunks
        ]

    def _payloads_de_dodas(self, dodas, doda_chunk_size, no_notificar=False):
        lotes = sa._chunk_list(dodas, doda_chunk_size)
        return [
            sa.build_payload(
                {}, {}, {}, {}, set(), {}, {}, {}, {}, {}, {},
                dodas=lote, no_notificar=no_notificar,
            )
            for lote in lotes
        ]

    def test_lotes_de_refs_nunca_llevan_dodas(self):
        all_refs = {f'REF{i:04d}' for i in range(5)}
        payloads = self._payloads_de_refs(all_refs, chunk_size=2)

        self.assertEqual(len(payloads), 3)
        for payload in payloads:
            self.assertEqual(payload['dodas'], [])

    def test_dodas_se_troceen_en_tandas_propias(self):
        dodas = [{'id_doda': i, 'num_doda': f'D-{i}', 'referencias': []} for i in range(7)]
        payloads = self._payloads_de_dodas(dodas, doda_chunk_size=3)

        self.assertEqual(len(payloads), 3)
        self.assertEqual(len(payloads[0]['dodas']), 3)
        self.assertEqual(len(payloads[1]['dodas']), 3)
        self.assertEqual(len(payloads[2]['dodas']), 1)
        # ningún payload de DODAs debería mandar 7032 de golpe otra vez
        for payload in payloads:
            self.assertLessEqual(len(payload['dodas']), 3)

    def test_no_notificar_viaja_en_cada_payload_de_dodas(self):
        dodas = [{'id_doda': 1, 'num_doda': 'D-1', 'referencias': []}]
        payloads = self._payloads_de_dodas(dodas, doda_chunk_size=1, no_notificar=True)

        self.assertEqual(len(payloads), 1)
        self.assertTrue(payloads[0]['no_notificar'])

    def test_no_notificar_default_false(self):
        payload = sa.build_payload(
            clientes={}, capturistas={}, embar={}, pedimentos={},
            all_refs=set(), pedime2={}, contenedores={}, guias={},
            partidas_count={}, proces={}, regval={},
        )
        self.assertFalse(payload['no_notificar'])


if __name__ == '__main__':
    unittest.main()
