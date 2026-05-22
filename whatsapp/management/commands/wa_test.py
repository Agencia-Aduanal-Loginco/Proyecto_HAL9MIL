from django.core.management.base import BaseCommand
from whatsapp import client


class Command(BaseCommand):
    help = 'Prueba la conexión con OpenWA enviando un mensaje'

    def add_arguments(self, parser):
        parser.add_argument('--numero', default='', help='Número destino sin código país (ej. 7535342088)')
        parser.add_argument('--mensaje', default='🤖 HAL9MIL — Prueba de conexión OK', help='Mensaje a enviar')

    def handle(self, *args, **options):
        numero = options['numero']
        chat_id = f"521{numero}@c.us" if numero else None

        if chat_id:
            resultado = client.send_text(chat_id, options['mensaje'])
        else:
            resultado = client.send_to_admin(options['mensaje'])

        if resultado:
            self.stdout.write(self.style.SUCCESS(f"Enviado: {resultado}"))
        else:
            self.stdout.write(self.style.ERROR('Error al enviar. Revisa WA_API_URL y WA_API_KEY en .env'))
