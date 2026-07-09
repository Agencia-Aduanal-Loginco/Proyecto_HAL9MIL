"""Servicio de carga masiva de XMLs de proveedor.

Recibe ZIPs o archivos sueltos, empareja XML↔PDF por nombre, parsea cada
CFDI, extrae datos aduanales, liga con la Referencia y genera el gasto.
Ver spec: docs/superpowers/specs/2026-07-09-carga-masiva-xml-design.md
"""
import os
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

from django.core.files.base import ContentFile

from .cfdi_parser import parsear_cfdi_root
from .extractores import buscar_referencia, extraer_datos_aduanales
from .models import GastoReferencia, XMLProveedor
from .polizas import generar_poliza_gasto


@dataclass
class ResultadoArchivo:
    nombre: str
    estado: str                 # ASIGNADO | PENDIENTE | DUPLICADO | ERROR
    referencia: object = None   # referencias.Referencia | None
    detalle: str = ''


def crear_gasto_desde_xml(xml_obj, usuario, tipo='MANIOBRAS'):
    """Crea el GastoReferencia + póliza a partir de un XMLProveedor ya ligado
    a una referencia, y marca el XML como procesado."""
    gasto = GastoReferencia.objects.create(
        referencia=xml_obj.referencia,
        tipo=tipo,
        concepto=xml_obj.concepto_principal or f'Factura {xml_obj.rfc_emisor}',
        fecha=xml_obj.fecha_emision.date(),
        monto=xml_obj.total,
        moneda=xml_obj.moneda,
        proveedor=xml_obj.nombre_emisor,
        xml_proveedor=xml_obj,
        registrado_por=usuario,
    )
    poliza = generar_poliza_gasto(gasto)
    gasto.poliza = poliza
    gasto.save(update_fields=['poliza'])
    xml_obj.procesado = True
    xml_obj.save(update_fields=['procesado'])
    return gasto


def expandir_subidas(uploaded_files):
    """Convierte lo subido (ZIPs y/o archivos sueltos) en [(nombre, bytes)].
    Propaga zipfile.BadZipFile si un ZIP es inválido."""
    resultado = []
    for f in uploaded_files:
        if f.name.lower().endswith('.zip'):
            with zipfile.ZipFile(f) as zf:
                for info in zf.infolist():
                    if not info.is_dir():
                        resultado.append((info.filename, zf.read(info)))
        else:
            resultado.append((f.name, f.read()))
    return resultado


def _recolectar(files):
    """Empareja XMLs con su PDF por nombre base (mismo stem). Ignora el resto
    (CSV anexo, PDFs sin XML, etc.)."""
    xmls, pdfs = {}, {}
    for nombre, data in files:
        base = os.path.basename(nombre)
        stem, ext = os.path.splitext(base)
        ext = ext.lower()
        if ext == '.xml':
            xmls[stem] = (base, data)
        elif ext == '.pdf':
            pdfs[stem] = data
    return [
        {'nombre': base, 'stem': stem, 'xml': data, 'pdf': pdfs.get(stem)}
        for stem, (base, data) in sorted(xmls.items())
    ]


def procesar_lote(files, usuario):
    """Procesa [(nombre, bytes)] y devuelve un ResultadoArchivo por XML."""
    return [_procesar_uno(item, usuario) for item in _recolectar(files)]


def _procesar_uno(item, usuario):
    nombre = item['nombre']
    try:
        root = ET.fromstring(item['xml'])
        datos = parsear_cfdi_root(root)
    except (ET.ParseError, ValueError) as e:
        return ResultadoArchivo(nombre, 'ERROR', detalle=str(e))

    if XMLProveedor.objects.filter(uuid_fiscal=datos['uuid']).exists():
        return ResultadoArchivo(
            nombre, 'DUPLICADO', detalle=f'UUID {datos["uuid"]} ya registrado'
        )

    referencia, motivo = buscar_referencia(extraer_datos_aduanales(root))

    xml_obj = XMLProveedor(
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
        estado_asignacion='ASIGNADO' if referencia else 'PENDIENTE',
        motivo_pendiente='' if referencia else motivo,
    )
    xml_obj.xml_file.save(nombre, ContentFile(item['xml']), save=False)
    if item['pdf']:
        xml_obj.pdf_file.save(
            item['stem'] + '.pdf', ContentFile(item['pdf']), save=False
        )
    xml_obj.save()

    if referencia is None:
        return ResultadoArchivo(nombre, 'PENDIENTE', detalle=motivo)
    # Solo los comprobantes de Ingreso generan gasto (E = nota de crédito)
    if datos['tipo'] == 'I':
        crear_gasto_desde_xml(xml_obj, usuario)
    return ResultadoArchivo(nombre, 'ASIGNADO', referencia=referencia)
