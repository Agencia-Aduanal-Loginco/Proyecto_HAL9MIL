"""Clasificación de documentos del expediente con Jev (TypeSafe System One).

Contrato de la API (https://docs.typesafe.ai/api/system-one):
    POST https://api.typesafe.ai/v1/systemone
    Authorization: Bearer <API_KEY>
    body: {"model": ..., "state": ..., "questions": {...}, "assets": {...}}
    respuesta: {"model": ..., "answers": {<id>: {...}}, "usage": {...}}

Jev solo acepta texto e imágenes PNG/JPEG. Los PDF se mandan como texto
(PyMuPDF); si no tienen capa de texto (escaneados) se rasteriza la primera
página a PNG y se manda como asset con el header de imágenes.
"""
import base64
import io
import logging
import os
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import pymupdf as fitz
import requests

logger = logging.getLogger(__name__)

JEV_API_URL_DEFAULT = 'https://api.typesafe.ai/v1/systemone'
JEV_MODEL_DEFAULT = 'jev-latest'
JEV_TIMEOUT = 30

# Texto mínimo para considerar que el PDF tiene capa de texto útil.
MIN_CARACTERES_TEXTO = 200
# Tope de texto enviado como estado. El encabezado del documento basta para
# clasificar y un estado grande con detalle irrelevante baja la precisión.
MAX_CARACTERES_TEXTO = 12000

# Límites de imagen de la API: cada lado entre 280 y 1568 px,
# total de pixeles entre 78,400 y 2,458,624.
IMG_LADO_MAX = 1568
IMG_LADO_MIN = 280
IMG_PIXELES_MAX = 2_458_624

# Umbrales de decisión (patrón "companion noul" + confidence routing).
NOUL_SI = 0.7
NOUL_NO = 0.3
PROB_MIN = 0.6
CONFIANZA_MIN = 0.4

# Estados de la clasificación guardados en DocumentoReferencia.clasificacion_ia
ESTADO_OK = 'OK'
ESTADO_REVISION = 'REVISION'
ESTADO_SIN_IA = 'SIN_IA'
ESTADO_ERROR = 'ERROR'
ESTADO_MANUAL = 'MANUAL'

# Opciones del Choice. OTROS no va aquí: compite por probabilidad con las
# opciones reales. El caso "ninguna aplica" lo resuelve la pregunta Noul.
CRITERIOS_TIPO = {
    'FACTURA': {
        'que_es': 'Factura comercial del proveedor / Commercial invoice.',
        'senales': 'Número de factura, vendedor y comprador, descripción de '
                   'mercancía con cantidades, precio unitario y valor total, '
                   'Incoterm, moneda. Incluye CFDI y "proforma invoice".',
        'no_es': 'Lista de empaque (pesos y bultos sin precios) ni conocimiento de embarque.',
    },
    'BL': {
        'que_es': 'Conocimiento de embarque marítimo / Bill of Lading, Sea Waybill.',
        'senales': 'Shipper, consignee, notify party, buque (vessel) y viaje, '
                   'puerto de carga y descarga, números de contenedor y sello, '
                   'naviera emisora.',
        'no_es': 'Factura ni lista de empaque aunque mencionen el buque o contenedor.',
    },
    'LISTA_EMPAQUE': {
        'que_es': 'Lista de empaque / Packing list.',
        'senales': 'Detalle por bulto o tarima: cantidades, peso bruto y neto, '
                   'dimensiones, marcas. Puede citar el número de factura pero '
                   'no trae precios.',
        'no_es': 'Factura comercial (trae precios y valor total).',
    },
    'PREFORMA': {
        'que_es': 'Pre-forma, borrador o impreso de pedimento aduanal (formato CASA) / Pedimento.',
        'senales': 'Texto "PEDIMENTO", "ANEXO DEL PEDIMENTO", "REF:", "BORRADOR SIN VALIDEZ", '
                   '"CUADRO DE LIQUIDACION", "NUM. PEDIMENTO", clave de pedimento, aduana, '
                   'bloque PARTIDAS con fracciones, pago electrónico o línea de captura.',
        'no_es': 'Factura, B/L o lista de empaque definitiva sin estructura de pedimento.',
    },
    'CARTA_TECNICA': {
        'que_es': 'Carta técnica o ficha técnica del producto / Technical letter, technical data sheet.',
        'senales': 'Describe composición, material, función, uso o proceso de '
                   'fabricación de la mercancía para su clasificación arancelaria.',
        'no_es': 'Certificado emitido por una autoridad o un organismo.',
    },
    'PERMISOS': {
        'que_es': 'Permiso, autorización o regulación no arancelaria / Import permit.',
        'senales': 'Emitido por autoridad (SE, SENASICA, COFEPRIS, SEMARNAT, '
                   'SEDENA, etc.): permiso previo, aviso automático, '
                   'autorización sanitaria, NOM, número de folio de permiso.',
        'no_es': 'Certificado de origen.',
    },
    'CERTIFICADOS': {
        'que_es': 'Certificado de origen, calidad, análisis o fitosanitario / Certificate.',
        'senales': 'Certifica origen (T-MEC/USMCA, certificado de origen), '
                   'calidad, análisis de laboratorio o sanidad. Tiene firma y '
                   'declaración del certificador.',
        'no_es': 'Permiso de importación emitido por autoridad mexicana.',
    },
}

INSTRUCCIONES_TIPO = (
    'El texto o imagen en `documento` es un documento del expediente de una '
    'importación o exportación en una agencia aduanal mexicana. Puede estar en '
    'español o en inglés. ¿Qué tipo de documento es?'
)

INSTRUCCIONES_CONOCIDO = (
    '¿El documento en `documento` es una factura comercial, un conocimiento de '
    'embarque (Bill of Lading), una lista de empaque, una carta técnica, un '
    'permiso de importación o un certificado (origen, calidad, análisis)?'
)

CRITERIOS_CONOCIDO = {
    'true': 'Es claramente uno de esos seis tipos de documento.',
    'false': 'Es otro documento: pedimento, cuenta de gastos, correo, '
             'cotización de flete, contrato, identificación u otro.',
}


class JevIAError(Exception):
    """Error al consultar la API de Jev."""


@dataclass
class Clasificacion:
    tipo: str
    confianza: Decimal | None
    detalle: dict = field(default_factory=dict)

    @property
    def requiere_revision(self):
        return self.detalle.get('estado') != ESTADO_OK


@dataclass
class ContenidoDocumento:
    texto: str = ''
    imagen_png: bytes | None = None


# ---------------------------------------------------------------------------
# Lectura del archivo
# ---------------------------------------------------------------------------

def _leer_bytes(fuente):
    if hasattr(fuente, 'read'):
        if hasattr(fuente, 'open') and getattr(fuente, 'closed', False):
            fuente.open('rb')
        try:
            fuente.seek(0)
        except Exception:
            pass
        datos = fuente.read()
        try:
            fuente.seek(0)
        except Exception:
            pass
        return datos
    if hasattr(fuente, 'path'):
        with open(fuente.path, 'rb') as f:
            return f.read()
    if isinstance(fuente, (bytes, bytearray)):
        return bytes(fuente)
    if isinstance(fuente, str) and os.path.exists(fuente):
        with open(fuente, 'rb') as f:
            return f.read()
    return b''


def _pixmap_a_png(pagina):
    """Rasteriza una página respetando los límites de imagen de la API."""
    ancho, alto = pagina.rect.width, pagina.rect.height
    if not ancho or not alto:
        return None
    zoom = IMG_LADO_MAX / max(ancho, alto)
    # Si el lado corto queda bajo el mínimo (páginas muy alargadas) no se manda.
    if min(ancho, alto) * zoom < IMG_LADO_MIN:
        return None
    if ancho * alto * zoom * zoom > IMG_PIXELES_MAX:
        zoom = (IMG_PIXELES_MAX / (ancho * alto)) ** 0.5
    pix = pagina.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes('png')


def _imagen_a_png(datos):
    """Normaliza una imagen subida (jpg/png/etc.) a PNG dentro de los límites."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(datos))
    img = ImageOps.exif_transpose(img).convert('RGB')
    img.thumbnail((IMG_LADO_MAX, IMG_LADO_MAX))
    if min(img.size) < IMG_LADO_MIN:
        return None
    salida = io.BytesIO()
    img.save(salida, format='PNG')
    return salida.getvalue()


def leer_documento(fuente):
    """Regresa texto (si lo hay) y, para escaneados o imágenes, un PNG de la página 1."""
    datos = _leer_bytes(fuente)
    if not datos:
        return ContenidoDocumento()

    if datos[:5] == b'%PDF-':
        try:
            doc = fitz.open(stream=datos, filetype='pdf')
        except Exception:
            logger.warning('PDF ilegible para clasificación')
            return ContenidoDocumento()
        with doc:
            texto = ''.join(p.get_text() for p in doc)
            if len(texto.strip()) >= MIN_CARACTERES_TEXTO or doc.page_count == 0:
                return ContenidoDocumento(texto=texto)
            return ContenidoDocumento(texto=texto, imagen_png=_pixmap_a_png(doc[0]))

    try:
        return ContenidoDocumento(imagen_png=_imagen_a_png(datos))
    except Exception:
        pass

    # XML de CFDI, TXT u otros formatos de texto.
    try:
        return ContenidoDocumento(texto=datos.decode('utf-8'))
    except UnicodeDecodeError:
        return ContenidoDocumento()


def extraer_texto_pdf(pdf_source):
    """Compatibilidad: solo el texto del documento."""
    return leer_documento(pdf_source).texto


# ---------------------------------------------------------------------------
# Cliente Jev
# ---------------------------------------------------------------------------

def _api_key():
    return os.getenv('JEV_AI_API_KEY') or os.getenv('TYPESAFE_API_KEY') or ''


def jev_habilitado():
    return bool(_api_key())


def consultar_jev_ia(state, preguntas, assets=None):
    """POST /v1/systemone. Regresa el JSON de respuesta o lanza JevIAError."""
    api_key = _api_key()
    if not api_key:
        raise JevIAError('JEV_AI_API_KEY no configurada')

    url = os.getenv('JEV_API_URL') or JEV_API_URL_DEFAULT
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': os.getenv('JEV_MODEL') or JEV_MODEL_DEFAULT,
        'state': state,
        'questions': preguntas,
    }
    if assets:
        payload['assets'] = assets
        headers['TypeSafe-Danger-Zone-Images'] = 'true'

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=JEV_TIMEOUT)
    except requests.RequestException as e:
        logger.error('Jev: error de conexión con %s: %s', url, e)
        raise JevIAError(f'Error de conexión con Jev: {e}') from e

    if resp.status_code != 200:
        try:
            detalle = resp.json().get('detail', resp.text)
        except ValueError:
            detalle = resp.text
        logger.error('Jev: HTTP %s en %s: %s', resp.status_code, url, str(detalle)[:500])
        raise JevIAError(f'Jev respondió HTTP {resp.status_code}: {str(detalle)[:300]}')

    try:
        data = resp.json()
    except ValueError as e:
        raise JevIAError('Jev regresó una respuesta que no es JSON') from e
    if not isinstance(data.get('answers'), dict):
        raise JevIAError('Respuesta de Jev sin "answers"')
    return data


def _preguntas_clasificacion():
    return {
        'tipo_documento': {
            'type': 'choice',
            'instructions': INSTRUCCIONES_TIPO,
            'criteria': CRITERIOS_TIPO,
        },
        'es_documento_conocido': {
            'type': 'noul',
            'instructions': INSTRUCCIONES_CONOCIDO,
            'criteria': CRITERIOS_CONOCIDO,
        },
    }


def _decidir(data):
    """Aplica umbrales a las respuestas de Jev y arma la Clasificacion."""
    respuestas = data['answers']
    tipo_resp = respuestas.get('tipo_documento') or {}
    noul_resp = respuestas.get('es_documento_conocido') or {}

    eleccion = tipo_resp.get('choice')
    probabilidades = tipo_resp.get('probabilities') or {}
    confianza = tipo_resp.get('confidence')
    noul = noul_resp.get('noul')

    if eleccion not in CRITERIOS_TIPO or noul is None:
        raise JevIAError('Respuesta de Jev incompleta')

    prob = float(probabilidades.get(eleccion, 0))
    detalle = {
        'modelo': data.get('model'),
        'uso': data.get('usage') or {},
        'sugerencia': eleccion,
        'probabilidades': probabilidades,
        'confidence': confianza,
        'noul_conocido': noul,
    }

    if noul < NOUL_NO:
        detalle['estado'] = ESTADO_OK
        return Clasificacion('OTROS', _dec(1 - noul), detalle)

    seguro = (
        noul > NOUL_SI
        and prob >= PROB_MIN
        and (confianza is None or confianza >= CONFIANZA_MIN)
    )
    detalle['estado'] = ESTADO_OK if seguro else ESTADO_REVISION
    return Clasificacion(eleccion, _dec(prob), detalle)


def _dec(valor):
    return Decimal(str(round(float(valor), 4)))


# ---------------------------------------------------------------------------
# Respaldo sin IA
# ---------------------------------------------------------------------------

# Orden importa: los documentos más específicos primero. Una lista de empaque
# suele decir "Invoice No.", así que va antes que FACTURA.
PALABRAS_CLAVE = [
    ('PREFORMA', ['BORRADOR SIN VALIDEZ', 'PREFORMA', 'PRE-FORMA', 'CUADRO DE LIQUIDACION',
                  'CUADRO DE LIQUIDACIÓN', 'ANEXO DEL PEDIMENTO']),
    ('BL', ['BILL OF LADING', 'SEA WAYBILL', 'WAYBILL', 'CONOCIMIENTO DE EMBARQUE']),
    ('LISTA_EMPAQUE', ['PACKING LIST', 'LISTA DE EMPAQUE']),
    ('CERTIFICADOS', ['CERTIFICATE OF ORIGIN', 'CERTIFICADO DE ORIGEN',
                      'CERTIFICATE OF ANALYSIS', 'CERTIFICADO DE ANALISIS',
                      'CERTIFICADO DE ANÁLISIS', 'PHYTOSANITARY', 'FITOSANITARIO']),
    ('PERMISOS', ['PERMISO PREVIO', 'AVISO AUTOMATICO', 'AVISO AUTOMÁTICO',
                  'IMPORT PERMIT', 'COFEPRIS', 'SENASICA', 'SEMARNAT']),
    ('CARTA_TECNICA', ['CARTA TECNICA', 'CARTA TÉCNICA', 'FICHA TECNICA',
                       'FICHA TÉCNICA', 'TECHNICAL DATA SHEET', 'TECHNICAL LETTER']),
    ('FACTURA', ['COMMERCIAL INVOICE', 'INVOICE', 'FACTURA', 'CFDI:COMPROBANTE']),
]


def clasificar_por_palabras_clave(texto):
    texto = (texto or '').upper()
    for tipo, palabras in PALABRAS_CLAVE:
        if any(p in texto for p in palabras):
            return tipo
    return 'OTROS'


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def clasificar_documento(fuente):
    """Clasifica un archivo del expediente.

    Con Jev configurado la confianza es la probabilidad de la opción elegida.
    Sin Jev, o si Jev falla, se usa palabras clave como sugerencia con
    confianza None y estado SIN_IA / ERROR para que el usuario confirme.
    """
    contenido = leer_documento(fuente)
    texto = contenido.texto.strip()
    sugerencia = clasificar_por_palabras_clave(texto)

    if not jev_habilitado():
        return Clasificacion(sugerencia, None, {
            'estado': ESTADO_SIN_IA, 'sugerencia': sugerencia, 'fuente': 'palabras_clave',
        })

    assets = None
    if len(texto) >= MIN_CARACTERES_TEXTO or (texto and not contenido.imagen_png):
        state = {'documento': texto[:MAX_CARACTERES_TEXTO]}
        entrada = 'texto'
    elif contenido.imagen_png:
        ref = f'img-{uuid.uuid4()}'
        state = {'documento': ref}
        assets = {ref: {
            'type': 'image',
            'format': 'png',
            'data_base64': base64.b64encode(contenido.imagen_png).decode('ascii'),
        }}
        entrada = 'imagen'
    else:
        return Clasificacion('OTROS', None, {
            'estado': ESTADO_ERROR, 'error': 'No se pudo leer texto ni imagen del archivo',
        })

    try:
        data = consultar_jev_ia(state, _preguntas_clasificacion(), assets=assets)
        resultado = _decidir(data)
    except JevIAError as e:
        return Clasificacion(sugerencia, None, {
            'estado': ESTADO_ERROR, 'error': str(e),
            'sugerencia': sugerencia, 'fuente': 'palabras_clave',
        })

    resultado.detalle['entrada'] = entrada
    return resultado


def extraer_datos_bl(pdf_source):
    """Compatibilidad: la extracción vive en services_extraccion (Claude)."""
    from .services_extraccion import extraer_datos_documento
    return extraer_datos_documento(pdf_source, 'BL')


def comparar_preforma_documentos(referencia_obj, documentos):
    """Compara datos de la pre-forma y documentos del expediente delegando en services_cruce."""
    from .services_cruce import ejecutar_cruce
    res = ejecutar_cruce(referencia_obj)
    res['tiene_bl'] = any(d.tipo_documento == 'BL' for d in documentos)
    res['tiene_factura'] = any(d.tipo_documento == 'FACTURA' for d in documentos)
    return res
