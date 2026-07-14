"""Envío de la cuenta de gastos de una referencia al cliente.

Correo con balanza anticipos vs. gastos y ZIP de CFDIs, vía SendGrid Web API
(custom_args para correlación con el Event Webhook). Patrón hermano de
finanzas/cobranza.py, que sigue usando SMTP.
"""
import io
import logging
import zipfile
from datetime import date

logger = logging.getLogger(__name__)

LIMITE_ZIP_BYTES = 20 * 1024 * 1024  # SendGrid admite 30 MB por mensaje


def destinatarios_cliente(cliente):
    """(to, cc) para la cuenta de gastos, con fallback a los correos de cobranza."""
    if cliente is None:
        return '', ''
    to = cliente.email_cuenta_gastos or cliente.email_cobranza
    cc = cliente.email_cuenta_gastos_cc or cliente.email_cobranza_cc
    return to, cc


def construir_zip_cuenta_gastos(referencia):
    """Empaqueta xml_file + pdf_file de cada XMLProveedor de la referencia.

    Retorna (nombre_zip, bytes). Lanza ValueError si la referencia no tiene
    CFDIs o si el ZIP excede LIMITE_ZIP_BYTES.
    """
    xmls = list(referencia.xmls_proveedor.all())
    if not xmls:
        raise ValueError('La referencia no tiene CFDIs para adjuntar.')

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for xml in xmls:
            with xml.xml_file.open('rb') as f:
                zf.writestr(f'CFDI_{xml.uuid_fiscal}.xml', f.read())
            if xml.pdf_file:
                with xml.pdf_file.open('rb') as f:
                    zf.writestr(f'CFDI_{xml.uuid_fiscal}.pdf', f.read())

    data = buffer.getvalue()
    if len(data) > LIMITE_ZIP_BYTES:
        raise ValueError(
            f'El ZIP pesa {len(data) / 1024 / 1024:.1f} MB y excede el límite '
            f'de {LIMITE_ZIP_BYTES // 1024 // 1024} MB para envío por correo.'
        )
    nombre = f"CG_{referencia.num_refe.replace('/', '-')}_{date.today():%Y%m%d}.zip"
    return nombre, data


def contexto_balanza(referencia):
    """Contexto compartido por el email y la vista previa en pantalla."""
    from .saldo import saldo_referencia
    return {
        'referencia': referencia,
        'anticipos': referencia.anticipos.order_by('fecha'),
        'gastos': referencia.gastos_finanzas.order_by('fecha'),
        'saldo': saldo_referencia(referencia),
    }
