import io
import pymupdf as fitz
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings
from glosa.services_anotacion import generar_preforma_anotada


def _ref(num_refe, patente='1656'):
    return SimpleNamespace(num_refe=num_refe, patente=patente)


class PreformaAnotacionTest(SimpleTestCase):
    def test_generar_preforma_anotada_con_discrepancias(self):
        # Crear PDF de prueba con texto conocido
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "FACTURA COMERCIAL NO. 12345")
        page.insert_text((50, 80), "VALOR TOTAL: 5000.00 USD")
        pdf_bytes = doc.tobytes()
        doc.close()

        discrepancias = [
            {
                "codigo": "VALOR_NO_COINCIDE",
                "mensaje": "El valor total no coincide con la factura.",
                "campo": "valor_total",
                "valor_encontrado": "5000.00",
                "valor_esperado": "5500.00",
                "en_preforma": True
            },
            {
                "codigo": "FACTURA_DIFERENTE",
                "mensaje": "Numero de factura no coincide.",
                "campo": "num_factura",
                "valor_encontrado": "99999",
                "valor_esperado": "12345",
                "en_preforma": False
            }
        ]

        anotado_bytes = generar_preforma_anotada(pdf_bytes, discrepancias, _ref("LCRR3940/26"))

        # Verificar que retorna bytes de PDF válidos
        self.assertIsInstance(anotado_bytes, bytes)
        self.assertTrue(len(anotado_bytes) > 0)

        # Abrir PDF anotado y verificar estructura
        doc_anotado = fitz.open(stream=anotado_bytes, filetype="pdf")
        # Debe tener 2 páginas (1 de resumen + 1 original)
        self.assertEqual(len(doc_anotado), 2)

        # Verificar que la primera página (resumen) contiene el texto de referencia y la discrepancia activa
        texto_resumen = doc_anotado[0].get_text()
        self.assertIn("LCRR3940/26", texto_resumen)
        self.assertIn("VALOR_NO_COINCIDE", texto_resumen)
        # La discrepancia con en_preforma=False no debe aparecer en el resumen de pre-forma
        self.assertNotIn("FACTURA_DIFERENTE", texto_resumen)

        doc_anotado.close()

    def test_marca_solo_numero_y_respeta_ocurrencia(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "JUEGO DE CHUMACERAS")
        page.insert_text((50, 120), "JUEGO DE CHUMACERAS")
        pdf_bytes = doc.tobytes()
        doc.close()

        discrepancias = [
            {"codigo": "PARTIDA_CANTIDAD_FACTURA", "mensaje": "Partida 001: cantidad", "valor_encontrado": "9700",
             "texto_busqueda": "JUEGO DE CHUMACERAS", "ocurrencia_busqueda": 0, "en_preforma": True},
            {"codigo": "PARTIDA_IMPORTE_FACTURA", "mensaje": "Partida 001: importe", "valor_encontrado": "672525",
             "texto_busqueda": "JUEGO DE CHUMACERAS", "ocurrencia_busqueda": 0, "en_preforma": True},
            {"codigo": "PARTIDA_SIN_CERTIFICADO_NOM", "mensaje": "Partida 003: NOM", "valor_encontrado": "NOM",
             "texto_busqueda": "JUEGO DE CHUMACERAS", "ocurrencia_busqueda": 1, "en_preforma": True},
        ]
        anotado = fitz.open(stream=generar_preforma_anotada(pdf_bytes, discrepancias, _ref("LCRR6280/26")), filetype="pdf")
        texto = anotado[1].get_text()
        anotado.close()
        # Sin etiquetas largas sobre la pre-forma, solo números.
        self.assertNotIn("PARTIDA_CANTIDAD_FACTURA", texto)
        # 1 y 2 van a la primera aparición; 3 solo a la segunda.
        self.assertEqual(texto.split("\n")[2:4], ["1,2", "3"])

    @override_settings(GLOSA_NOMBRE_AGENCIA='Loginco Corporativo')
    def test_encabezado_con_agencia_y_patente_de_la_referencia(self):
        doc = fitz.open()
        doc.new_page().insert_text((50, 50), "PEDIMENTO")
        pdf_bytes = doc.tobytes()
        doc.close()
        for patente in ('1627', '1656', '1927'):
            anotado = fitz.open(stream=generar_preforma_anotada(pdf_bytes, [], _ref("LCLF0001/26", patente)), filetype="pdf")
            resumen = anotado[0].get_text()
            anotado.close()
            self.assertIn(f"Loginco Corporativo - Patente {patente}", resumen)
            self.assertNotIn("Reiki", resumen)
            self.assertNotIn("3772", resumen)
