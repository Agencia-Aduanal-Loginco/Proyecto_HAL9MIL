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


if __name__ == '__main__':
    unittest.main()
