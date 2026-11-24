"""Extracción de datos de documentos del expediente con Claude.

Jev (services_jev_ia) solo elige entre opciones; no puede leer valores libres
como un nombre de buque o un importe. Para eso se usa Claude:

- El PDF se manda completo como bloque `document` (Claude lee la capa de texto
  y la imagen de cada página, así que funciona con escaneados).
- La respuesta se restringe con JSON outputs (`output_config.format`, tipo
  `json_schema`) a un esquema fijo por tipo de documento. No se usa
  `tool_choice` forzado: modelos como claude-sonnet-5-5 lo rechazan con 400.
- Lo que se puede verificar se verifica en código: dígito verificador ISO 6346
  de contenedores, pesos positivos, importes como Decimal.

Resultado guardado en DocumentoReferencia.datos_extraidos:
    {<campos del documento>..., 'meta': {'tipo', 'estado', 'modelo',
     'advertencias', 'error', 'uso'}}
`meta.uso` trae tokens de entrada/salida y, si estan configurados los precios
(IA_COSTO_ENTRADA_USD_MTOK / IA_COSTO_SALIDA_USD_MTOK), el costo en USD.
Los campos van al primer nivel para que el resto del código lea
`datos_extraidos['buque']`, `['viaje']`, etc.
"""
import base64
import json
import logging
import os
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings

from .services_jev_ia import _imagen_a_png, _leer_bytes

logger = logging.getLogger(__name__)

MODELO_DEFAULT = 'claude-sonnet-5-5'
MAX_TOKENS = 8192
# La pre-forma trae muchas partidas con ~20 campos cada una.
# Arriba de ~21000 el SDK exige streaming en llamadas sin stream.
MAX_TOKENS_POR_TIPO = {'PREFORMA': 16000}
TIMEOUT = 90
MAX_CARACTERES_TEXTO = 100_000

ESTADO_OK = 'OK'
ESTADO_REVISION = 'REVISION'
ESTADO_SIN_IA = 'SIN_IA'
ESTADO_ERROR = 'ERROR'
ESTADO_NO_APLICA = 'NO_APLICA'


def _nullable(tipo, descripcion):
    return {'type': [tipo, 'null'], 'description': descripcion}


ESQUEMAS = {
    'BL': {
        'descripcion': 'Datos leídos de un Bill of Lading / Sea Waybill.',
        'propiedades': {
            'numero_bl': _nullable('string', 'Número del B/L (B/L No., Waybill No.).'),
            'naviera': _nullable('string', 'Naviera o transportista que emite el B/L.'),
            'buque': _nullable('string', 'Nombre del buque (Vessel), sin el viaje.'),
            'viaje': _nullable('string', 'Número de viaje (Voyage No.).'),
            'puerto_carga': _nullable('string', 'Puerto de carga (Port of Loading).'),
            'puerto_descarga': _nullable('string', 'Puerto de descarga (Port of Discharge).'),
            'shipper': _nullable('string', 'Embarcador (Shipper).'),
            'consignatario': _nullable('string', 'Consignatario (Consignee).'),
            'peso_bruto_kg': _nullable('number', 'Peso bruto total en kilogramos. Convierte si viene en otra unidad.'),
            'bultos': _nullable('integer', 'Número total de bultos / paquetes.'),
            'contenedores': {
                'type': 'array',
                'description': (
                    'Un elemento por cada renglón de contenedor del documento. Vacío si es carga suelta. '
                    'Si el mismo contenedor aparece en varias hojas o waybills con distintos '
                    'cartones y pesos, regresa un elemento por cada aparición, sin sumarlas.'
                ),
                'items': {
                    'type': 'object',
                    'properties': {
                        'numero': {'type': 'string', 'description': '4 letras + 7 dígitos, p. ej. MSKU1234565.'},
                        'sello': _nullable('string', 'Número de sello (Seal).'),
                        'tipo': _nullable('string', "Tipo/tamaño, p. ej. 40'HC, 20'GP."),
                        'bultos': _nullable('integer', 'Bultos / cartones de este renglón del contenedor.'),
                        'peso_bruto_kg': _nullable('number', 'Peso bruto de este renglón del contenedor en kg.'),
                    },
                    'required': ['numero', 'sello', 'tipo', 'bultos', 'peso_bruto_kg'],
                },
            },
        },
    },
    'FACTURA': {
        'descripcion': 'Datos leídos de una factura comercial o CFDI del proveedor.',
        'propiedades': {
            'numero_factura': _nullable('string', 'Número o folio de la factura.'),
            'uuid_cfdi': _nullable('string', 'Folio fiscal UUID si es CFDI.'),
            'fecha': _nullable('string', 'Fecha de la factura (AAAA-MM-DD).'),
            'proveedor': _nullable('string', 'Nombre del proveedor / emisor.'),
            'comprador': _nullable('string', 'Nombre del comprador / receptor.'),
            'incoterm': _nullable('string', 'Incoterm (FOB, CIF, EXW, etc.).'),
            'moneda': _nullable('string', 'Moneda ISO 4217 (USD, MXN, EUR, etc.).'),
            'valor_total': _nullable('number', 'Valor total de la factura.'),
            'partidas': {
                'type': 'array',
                'description': 'Una entrada por renglón de mercancía.',
                'items': {
                    'type': 'object',
                    'properties': {
                        'descripcion': {'type': 'string'},
                        'cantidad': _nullable('number', 'Cantidad facturada.'),
                        'unidad': _nullable('string', 'Unidad de medida.'),
                        'precio_unitario': _nullable('number', 'Costo unitario.'),
                        'importe': _nullable('number', 'Importe del renglón.'),
                    },
                    'required': ['descripcion', 'cantidad', 'unidad', 'precio_unitario', 'importe'],
                },
            },
        },
    },
    'LISTA_EMPAQUE': {
        'descripcion': 'Datos leídos de una lista de empaque (packing list).',
        'propiedades': {
            'numero_factura_ref': _nullable('string', 'Número de factura al que hace referencia.'),
            'bultos': _nullable('integer', 'Total de bultos / cajas / cartones / tarimas.'),
            'peso_bruto_kg': _nullable('number', 'Peso bruto total en kg.'),
            'peso_neto_kg': _nullable('number', 'Peso neto total en kg.'),
            'contenedores': {
                'type': 'array',
                'description': 'Números de contenedor mencionados.',
                'items': {'type': 'string'},
            },
            'partidas': {
                'type': 'array',
                'description': 'Detalle de mercancía por empaque / partida.',
                'items': {
                    'type': 'object',
                    'properties': {
                        'codigo_producto': _nullable('string', 'Código o SKU del producto.'),
                        'numero_parte': _nullable('string', 'Número de parte.'),
                        'codigo_truper': _nullable('string', 'Código Truper.'),
                        'descripcion': {'type': 'string', 'description': 'Descripción de la mercancía.'},
                        'cantidad': _nullable('number', 'Cantidad empacada.'),
                        'unidad': _nullable('string', 'Unidad de medida.'),
                        'cartones': _nullable('integer', 'Número de cajas o cartones.'),
                        'peso_bruto_kg': _nullable('number', 'Peso bruto de la partida en kg.'),
                        'peso_neto_kg': _nullable('number', 'Peso neto de la partida en kg.'),
                    },
                    'required': ['codigo_producto', 'numero_parte', 'descripcion', 'cantidad', 'unidad', 'cartones', 'peso_bruto_kg', 'peso_neto_kg'],
                },
            },
        },
    },
    'CARTA_TECNICA': {
        'descripcion': 'Datos y metadatos leídos de una carta técnica o especificación.',
        'propiedades': {
            'proveedor': {'type': 'string'},
            'comprador': {'type': 'string'},
            'incoterm': {'type': 'string'},
            'numero_factura': {'type': 'string'},
            'contenedores': {'type': 'array', 'items': {'type': 'string'}},
            'peso_bruto_kg': {'type': 'string'},
            'descripcion_mercancia': {'type': 'string'},
        },
    },
    'CERTIFICADOS': {
        'descripcion': 'Datos leídos de un certificado de origen, calidad o análisis.',
        'propiedades': {
            'numero_certificado': {'type': 'string'},
            'fecha_emision': {'type': 'string'},
            'fecha_vigencia': {'type': 'string'},
            'pais_origen': {'type': 'string'},
            'importador_nombre': {'type': 'string'},
            'importador_rfc': {'type': 'string'},
            'importador_domicilio': {'type': 'string'},
            'proveedor': {'type': 'string'},
            'modelos': {'type': 'array', 'items': {'type': 'string'}},
            'normas': {
                'type': 'array',
                'description': 'Normas Oficiales Mexicanas que certifica, p. ej. NOM-003-SCFI-2014.',
                'items': {'type': 'string'},
            },
            'contenedores': {'type': 'array', 'items': {'type': 'string'}},
        },
    },
    'PERMISOS': {
        'descripcion': 'Datos y metadatos leídos de un permiso, regulación o NOM.',
        'propiedades': {
            'proveedor': {'type': 'string'},
            'comprador': {'type': 'string'},
            'incoterm': {'type': 'string'},
            'numero_factura': {'type': 'string'},
            'contenedores': {'type': 'array', 'items': {'type': 'string'}},
            'numero_permiso': {'type': 'string'},
            'fecha_vigencia': {'type': 'string', 'description': 'Fecha de vencimiento del permiso (AAAA-MM-DD).'},
            'fracciones': {
                'type': 'array',
                'description': 'Fracciones arancelarias que ampara, 8 dígitos sin puntos.',
                'items': {'type': 'string'},
            },
            'modelos': {'type': 'array', 'items': {'type': 'string'}},
            'cantidad_autorizada': {'type': 'string', 'description': 'Cantidad autorizada, número sin separadores de miles.'},
        },
    },
    'OTROS': {
        'descripcion': 'Datos leídos de otros documentos del expediente.',
        'propiedades': {
            'proveedor': {'type': 'string'},
            'comprador': {'type': 'string'},
            'incoterm': {'type': 'string'},
            'numero_factura': {'type': 'string'},
            'contenedores': {'type': 'array', 'items': {'type': 'string'}},
            'peso_bruto_kg': {'type': 'string'},
        },
    },
    'PREFORMA': {
        'descripcion': 'Datos leídos de un borrador de pre-forma de pedimento.',
        'propiedades': {
            'num_refe': {'type': 'string'},
            'num_pedimento': {'type': 'string'},
            'clave_pedimento': {'type': 'string'},
            'peso_bruto_kg': {'type': 'string'},
            'bultos': {'type': 'string'},
            'proveedor_nombre': {'type': 'string'},
            'numero_factura': {'type': 'string'},
            'fecha_factura': {'type': 'string'},
            'incoterm': {'type': 'string'},
            'moneda': {'type': 'string'},
            'valor_moneda_factura': {'type': 'string'},
            'valor_dolares': {'type': 'string'},
            'guias': {'type': 'array', 'items': {'type': 'string'}},
            'contenedores': {'type': 'array', 'items': {'type': 'string'}},
            'documentos_ed': {'type': 'array', 'items': {'type': 'string'}},
            'certificados': {'type': 'array', 'items': {'type': 'string'}},
            'observaciones': {'type': 'string'},
            # Todo como string (vacío si no aparece): JSON outputs limita las
            # uniones con null a 16 por esquema y una partida tiene más campos.
            # Los números se convierten a Decimal en validar_y_normalizar.
            'partidas': {
                'type': 'array',
                'description': (
                    'Una entrada por partida del bloque PARTIDAS. Cada partida ocupa varios '
                    'renglones: SEC/FRACCION/SUBD/VINC/MET VAL/UMC/CANTIDAD UMC/UMT/CANTIDAD UMT/'
                    'P. V/C/P. O/D; luego DESCRIPCION; luego VAL ADU/USD, IMP. PRECIO PAG., '
                    'PRECIO UNIT., VAL. AGREG.; luego MARCA, MODELO, CODIGO PRODUCTO; luego '
                    'el bloque CLAVE / NUM. PERMISO O NOM; identificadores y contribuciones. '
                    'Usa cadena vacía si el dato no aparece.'
                ),
                'items': {
                    'type': 'object',
                    'properties': {
                        'sec': {'type': 'string', 'description': 'Columna SEC, tal cual (p. ej. "001").'},
                        'fraccion': {'type': 'string', 'description': 'Columna FRACCION, 8 dígitos sin puntos.'},
                        'nico': {'type': 'string', 'description': 'Columna SUBD/NÚM. IDENTIFICACIÓN COMERCIAL, 2 dígitos.'},
                        'vinculacion': {'type': 'string', 'description': 'Columna VINC.'},
                        'metodo_valoracion': {'type': 'string', 'description': 'Columna MET VAL.'},
                        'umc': {'type': 'string', 'description': 'Columna UMC: clave de la unidad de medida comercial.'},
                        'cantidad_umc': {'type': 'string', 'description': 'Columna CANTIDAD UMC, número sin separadores de miles.'},
                        'umt': {'type': 'string', 'description': 'Columna UMT: clave de la unidad de medida de la tarifa.'},
                        'cantidad_umt': {'type': 'string', 'description': 'Columna CANTIDAD UMT, número sin separadores de miles.'},
                        'pais_vendedor': {'type': 'string', 'description': 'Columna P. V/C (país vendedor/comprador), clave de 3 letras.'},
                        'pais_origen': {'type': 'string', 'description': 'Columna P. O/D (país origen/destino), clave de 3 letras.'},
                        'descripcion': {'type': 'string', 'description': 'Renglón DESCRIPCION.'},
                        'valor_aduana': {'type': 'string', 'description': 'Columna VAL ADU/USD tal cual (en importación es valor en aduana en pesos). No conviertas moneda.'},
                        'precio_pagado': {'type': 'string', 'description': 'Columna IMP. PRECIO PAG.'},
                        'precio_unitario': {'type': 'string', 'description': 'Columna PRECIO UNIT.'},
                        'valor_agregado': {'type': 'string', 'description': 'Columna VAL. AGREG.'},
                        'marca': {'type': 'string', 'description': 'Columna MARCA.'},
                        'modelo': {'type': 'string', 'description': 'Columna MODELO.'},
                        'codigo_producto': {'type': 'string', 'description': 'Columna CODIGO PRODUCTO.'},
                        'observaciones': {
                            'type': 'string',
                            'description': (
                                'Texto del bloque OBSERVACIONES A NIVEL PARTIDA de esta partida, tal cual '
                                '(p. ej. "MODELO: 115117(100200248) 142053(100238887)"). Vacío si no hay.'
                            ),
                        },
                        'identificadores': {
                            'type': 'array',
                            'description': 'Renglones IDENTIF. con sus complementos 1, 2 y 3.',
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'clave': {'type': 'string', 'description': 'Clave del identificador, p. ej. XP.'},
                                    'complemento1': {'type': 'string'},
                                    'complemento2': {'type': 'string'},
                                    'complemento3': {'type': 'string'},
                                },
                            },
                        },
                        'permisos': {
                            'type': 'array',
                            'description': (
                                'Renglones del bloque CLAVE / NUM. PERMISO O NOM / FIRMA DESCARGO / '
                                'VAL. COM. DLS / CANTIDAD UMT/C de la partida. Vacío si no hay.'
                            ),
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'clave': {'type': 'string', 'description': 'Columna CLAVE, p. ej. C1.'},
                                    'numero': {'type': 'string', 'description': 'Columna NUM. PERMISO O NOM, tal cual.'},
                                    'firma_descargo': {'type': 'string', 'description': 'Columna FIRMA DESCARGO.'},
                                    'valor_comercial_dls': {'type': 'string', 'description': 'Columna VAL. COM. DLS, número sin separadores de miles.'},
                                    'cantidad_umt': {'type': 'string', 'description': 'Columna CANTIDAD UMT/C, número sin separadores de miles.'},
                                },
                            },
                        },
                        'contribuciones': {
                            'type': 'array',
                            'description': 'Columnas CON., TASA, T.T., F.P., IMPORTE de la partida.',
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'clave': {'type': 'string', 'description': 'Columna CON., p. ej. IGI, IVA.'},
                                    'tasa': {'type': 'string', 'description': 'Columna TASA.'},
                                    'tipo_tasa': {'type': 'string', 'description': 'Columna T.T.'},
                                    'forma_pago': {'type': 'string', 'description': 'Columna F.P.'},
                                    'importe': {'type': 'string', 'description': 'Columna IMPORTE.'},
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}

TIPOS_SOPORTADOS = tuple(ESQUEMAS)

INSTRUCCIONES = (
    'Eres capturista de una agencia aduanal mexicana. Lee el documento adjunto '
    '(puede estar en español o en inglés) y regresa sus datos en el '
    'formato JSON indicado. Copia los valores tal como aparecen en el documento. Si un '
    'dato no aparece o no es legible, usa null: nunca lo deduzcas ni lo '
    'inventes. Los números van sin separadores de miles ni símbolos de moneda.'
)


class ExtraccionError(Exception):
    pass


# ---------------------------------------------------------------------------
# Validaciones en código
# ---------------------------------------------------------------------------

_VALORES_ISO6346 = {}
_v = 10
for _letra in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
    if _v % 11 == 0:
        _v += 1
    _VALORES_ISO6346[_letra] = _v
    _v += 1

_RE_CONTENEDOR = re.compile(r'^[A-Z]{3}[UJZ]\d{7}$')


def normalizar_contenedor(numero):
    return re.sub(r'[^A-Z0-9]', '', (numero or '').upper())


def contenedor_valido(numero):
    """Valida formato y dígito verificador ISO 6346."""
    numero = normalizar_contenedor(numero)
    if not _RE_CONTENEDOR.match(numero):
        return False
    total = 0
    for i, c in enumerate(numero[:10]):
        valor = _VALORES_ISO6346[c] if c.isalpha() else int(c)
        total += valor * (2 ** i)
    return (total % 11) % 10 == int(numero[10])


def _decimal(valor):
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def _texto_decimal(valor):
    d = _decimal(valor)
    return str(d) if d is not None else None


def _decimal_sin_miles(valor):
    """Decimal en texto quitando separadores de miles; None si viene vacío."""
    if valor is None:
        return None
    return _texto_decimal(str(valor).replace(',', '').strip() or None)


_CAMPOS_DECIMALES_PARTIDA = (
    'cantidad_umc', 'cantidad_umt', 'valor_aduana', 'precio_pagado',
    'precio_unitario', 'valor_agregado',
)


def _normalizar_partidas_preforma(partidas_crudas, advertencias):
    partidas = []
    for p in partidas_crudas or []:
        p = {k: (v.strip() if isinstance(v, str) else v) for k, v in dict(p).items()}
        sec = p.get('sec') or '?'

        for campo in _CAMPOS_DECIMALES_PARTIDA:
            p[campo] = _decimal_sin_miles(p.get(campo))

        fraccion = re.sub(r'\D', '', p.get('fraccion') or '')
        p['fraccion'] = fraccion
        if not re.fullmatch(r'\d{8}', fraccion):
            advertencias.append(f'Partida {sec}: fracción "{fraccion}" no tiene 8 dígitos')
        nico = re.sub(r'\D', '', p.get('nico') or '')
        p['nico'] = nico
        if nico and not re.fullmatch(r'\d{2}', nico):
            advertencias.append(f'Partida {sec}: NICO "{nico}" no tiene 2 dígitos')
        for campo in ('pais_vendedor', 'pais_origen'):
            p[campo] = (p.get(campo) or '').upper()

        p['identificadores'] = [
            {k: (v or '').strip().upper() for k, v in dict(i).items()}
            for i in p.get('identificadores') or []
            if (i.get('clave') or '').strip()
        ]
        permisos = []
        for x in p.get('permisos') or []:
            x = dict(x)
            x['clave'] = (x.get('clave') or '').strip().upper()
            x['numero'] = re.sub(r'\s+', '', (x.get('numero') or '')).upper()
            x['firma_descargo'] = (x.get('firma_descargo') or '').strip()
            x['valor_comercial_dls'] = _decimal_sin_miles(x.get('valor_comercial_dls'))
            x['cantidad_umt'] = _decimal_sin_miles(x.get('cantidad_umt'))
            if x['clave'] or x['numero']:
                permisos.append(x)
        p['permisos'] = permisos
        contribuciones = []
        for c in p.get('contribuciones') or []:
            c = dict(c)
            c['clave'] = (c.get('clave') or '').strip().upper()
            c['tasa'] = _decimal_sin_miles(c.get('tasa'))
            c['importe'] = _decimal_sin_miles(c.get('importe'))
            if c['clave']:
                contribuciones.append(c)
        p['contribuciones'] = contribuciones

        p['observaciones'] = (p.get('observaciones') or '').strip()
        p['modelos_observaciones'] = modelos_de_observaciones(p['observaciones'])

        cant_umt = _decimal(p['cantidad_umt'])
        if cant_umt is not None and cant_umt <= 0:
            advertencias.append(f'Partida {sec}: cantidad UMT no es positiva ({cant_umt})')

        # IMP. PRECIO PAG. / CANTIDAD UMC debe dar PRECIO UNIT. (1 % de tolerancia
        # por redondeo del precio unitario a 5 decimales).
        pagado, cant_umc, unitario = (_decimal(p[c]) for c in ('precio_pagado', 'cantidad_umc', 'precio_unitario'))
        if pagado is not None and unitario is not None and cant_umc:
            calculado = pagado / cant_umc
            if abs(calculado - unitario) > max(Decimal('0.00001'), unitario * Decimal('0.01')):
                advertencias.append(
                    f'Partida {sec}: precio pagado / cantidad UMC ({calculado:.5f}) '
                    f'no coincide con precio unitario ({unitario})'
                )
        partidas.append(p)
    return partidas


_RE_MODELOS_OBS = re.compile(r'MODELOS?\s*:\s*(.*)', re.IGNORECASE | re.DOTALL)


def modelos_de_observaciones(texto):
    """Modelos declarados en OBSERVACIONES A NIVEL PARTIDA.

    En Loginco la columna MODELO de la partida suele ir vacía y los modelos se
    capturan como "MODELO: 115117(100200248) 142053(100238887)". Se quita lo que
    va entre paréntesis (código interno) y los espacios dentro de un modelo
    ("X G E - 6 4 4 7" -> "XGE-6447"). Los modelos se separan por salto de
    línea, tabulador, coma o punto y coma; si el texto no trae esos separadores
    y cada token es corto, se separan por espacio.
    """
    m = _RE_MODELOS_OBS.search(texto or '')
    if not m:
        return []
    cuerpo = re.sub(r'\([^)]*\)', ' ', m.group(1))
    trozos = [t for t in re.split(r'[\n\t,;]+', cuerpo) if t.strip()]
    if len(trozos) == 1 and not re.search(r'\s[A-Z0-9]\s', ' ' + trozos[0].upper() + ' '):
        trozos = trozos[0].split()
    modelos = []
    for t in trozos:
        modelo = re.sub(r'\s+', '', t).upper()
        if modelo and modelo not in modelos:
            modelos.append(modelo)
    return modelos


def _fusionar_contenedores_bl(crudos, advertencias):
    """
    Agrupa los renglones del mismo contenedor sumando bultos y peso bruto.
    Si a alguna aparición le falta el dato, el total de ese campo queda en None
    (no se inventa una suma parcial) y se pide revisión.
    """
    por_numero = {}
    for cont in crudos:
        cont = dict(cont)
        numero = normalizar_contenedor(cont.get('numero'))
        peso = _decimal(cont.get('peso_bruto_kg'))
        bultos = cont.get('bultos')
        bultos = int(bultos) if bultos not in (None, '') else None
        actual = por_numero.get(numero)
        if actual is None:
            cont.update(numero=numero, peso_bruto_kg=peso, bultos=bultos, _sellos=[], _apariciones=0)
            por_numero[numero] = actual = cont
        else:
            actual['peso_bruto_kg'] = (
                actual['peso_bruto_kg'] + peso
                if actual['peso_bruto_kg'] is not None and peso is not None else None
            )
            actual['bultos'] = (
                actual['bultos'] + bultos
                if actual['bultos'] is not None and bultos is not None else None
            )
            if not actual.get('tipo') and cont.get('tipo'):
                actual['tipo'] = cont['tipo']
        actual['_apariciones'] += 1
        sello = cont.get('sello')
        if sello and sello not in actual['_sellos']:
            actual['_sellos'].append(sello)

    contenedores = []
    for numero, cont in por_numero.items():
        apariciones = cont.pop('_apariciones')
        sellos = cont.pop('_sellos')
        cont['sello'] = ' / '.join(sellos) if sellos else None
        if apariciones > 1:
            cont['apariciones'] = apariciones
            if cont['peso_bruto_kg'] is None or cont['bultos'] is None:
                advertencias.append(
                    f'Contenedor {numero} aparece {apariciones} veces y a alguna aparición '
                    f'le falta bultos o peso: no se pudo sumar'
                )
        cont['peso_bruto_kg'] = _texto_decimal(cont['peso_bruto_kg'])
        contenedores.append(cont)
    return contenedores


def validar_y_normalizar(tipo, datos):
    """Normaliza los datos y regresa (datos, advertencias)."""
    datos = dict(datos)
    advertencias = []

    for campo in ('peso_bruto_kg', 'peso_neto_kg'):
        if campo in datos:
            peso = _decimal(datos[campo])
            if peso is not None and peso <= 0:
                advertencias.append(f'{campo} no es positivo: {peso}')
            datos[campo] = _texto_decimal(datos[campo])

    if tipo == 'BL':
        contenedores = _fusionar_contenedores_bl(datos.get('contenedores') or [], advertencias)
        suma_pesos = Decimal('0')
        con_peso = True
        for cont in contenedores:
            if not contenedor_valido(cont['numero']):
                advertencias.append(f"Contenedor {cont['numero']} no pasa el dígito verificador ISO 6346")
            peso = _decimal(cont.get('peso_bruto_kg'))
            if peso is None:
                con_peso = False
            else:
                suma_pesos += peso
        datos['contenedores'] = contenedores

        # Un contenedor repetido indica varias hojas/waybills en el mismo PDF: el
        # total impreso suele ser el de una sola hoja, así que se usa la suma.
        if any(c.get('apariciones', 1) > 1 for c in contenedores):
            if con_peso:
                datos['peso_bruto_kg'] = _texto_decimal(suma_pesos)
            bultos = [c.get('bultos') for c in contenedores]
            if all(b is not None for b in bultos):
                datos['bultos'] = sum(bultos)

        total = _decimal(datos.get('peso_bruto_kg'))
        if contenedores and con_peso and total is not None:
            if abs(suma_pesos - total) > max(Decimal('1'), total * Decimal('0.005')):
                advertencias.append(
                    f'La suma de pesos por contenedor ({suma_pesos}) no coincide con el peso bruto total ({total})'
                )
        if not datos.get('buque') or not datos.get('viaje'):
            advertencias.append('No se encontró buque o viaje')

    elif tipo == 'FACTURA':
        suma = Decimal('0')
        completos = True
        partidas = []
        for p in datos.get('partidas') or []:
            p = dict(p)
            for campo in ('cantidad', 'precio_unitario', 'importe'):
                p[campo] = _texto_decimal(p.get(campo))
            if p['importe'] is None:
                completos = False
            else:
                suma += Decimal(p['importe'])
            partidas.append(p)
        datos['partidas'] = partidas

        for campo in ('subtotal', 'impuestos', 'valor_total'):
            if campo in datos:
                datos[campo] = _texto_decimal(datos[campo])

        total = _decimal(datos.get('valor_total'))
        datos['valor_total'] = _texto_decimal(datos.get('valor_total'))
        if total is None:
            advertencias.append('No se encontró el valor total')
        elif partidas and completos and abs(suma - total) > Decimal('0.05'):
            advertencias.append(f'La suma de partidas ({suma}) no coincide con el valor total ({total})')
        if datos.get('moneda'):
            datos['moneda'] = datos['moneda'].strip().upper()

    elif tipo == 'LISTA_EMPAQUE':
        contenedores = []
        for numero in datos.get('contenedores') or []:
            numero = normalizar_contenedor(numero)
            if not contenedor_valido(numero):
                advertencias.append(f'Contenedor {numero} no pasa el dígito verificador ISO 6346')
            contenedores.append(numero)
        datos['contenedores'] = contenedores

        partidas = []
        for p in datos.get('partidas') or []:
            p = dict(p)
            for campo in ('cantidad', 'peso_bruto_kg', 'peso_neto_kg'):
                p[campo] = _texto_decimal(p.get(campo))
            partidas.append(p)
        datos['partidas'] = partidas

        for campo in ('peso_bruto_kg', 'peso_neto_kg'):
            if campo in datos:
                datos[campo] = _texto_decimal(datos[campo])

        bruto, neto = _decimal(datos.get('peso_bruto_kg')), _decimal(datos.get('peso_neto_kg'))
        if bruto is not None and neto is not None and neto > bruto:
            advertencias.append('El peso neto es mayor que el bruto')

    elif tipo in ('CARTA_TECNICA', 'CERTIFICADOS', 'PERMISOS', 'OTROS'):
        contenedores = []
        for c in datos.get('contenedores') or []:
            num = c.get('numero') if isinstance(c, dict) else c
            numero = normalizar_contenedor(num)
            if numero and not contenedor_valido(numero):
                advertencias.append(f'Contenedor {numero} no pasa el dígito verificador ISO 6346')
            if numero:
                contenedores.append(numero)
        datos['contenedores'] = contenedores
        if datos.get('proveedor'):
            datos['proveedor'] = datos['proveedor'].strip()
        if datos.get('incoterm'):
            datos['incoterm'] = datos['incoterm'].strip().upper()
        if datos.get('numero_factura'):
            datos['numero_factura'] = datos['numero_factura'].strip()
        if datos.get('peso_bruto_kg'):
            datos['peso_bruto_kg'] = _texto_decimal(datos.get('peso_bruto_kg'))
        if 'normas' in datos:
            datos['normas'] = [n.strip().upper() for n in datos.get('normas') or [] if n and n.strip()]
        if 'fracciones' in datos:
            datos['fracciones'] = [re.sub(r'\D', '', f) for f in datos.get('fracciones') or [] if f and re.sub(r'\D', '', f)]
        if datos.get('cantidad_autorizada'):
            datos['cantidad_autorizada'] = _decimal_sin_miles(datos.get('cantidad_autorizada'))

    elif tipo == 'PREFORMA':
        datos['peso_bruto_kg'] = _texto_decimal(datos.get('peso_bruto_kg'))
        datos['valor_moneda_factura'] = _texto_decimal(datos.get('valor_moneda_factura'))
        datos['valor_dolares'] = _texto_decimal(datos.get('valor_dolares'))
        if datos.get('num_pedimento'):
            datos['num_pedimento'] = datos['num_pedimento'].strip()[-7:]
        conts = []
        for c in datos.get('contenedores') or []:
            parts = c.split('/')
            num = normalizar_contenedor(parts[0])
            conts.append(num)
        datos['contenedores'] = [c for c in conts if c]

        datos['partidas'] = _normalizar_partidas_preforma(datos.get('partidas'), advertencias)

        obs = datos.get('observaciones', '')
        subdivision = {'detectada': False, 'pedimento_relacionado': None}
        if 'SUBDIVIDE' in obs.upper():
            subdivision['detectada'] = True
            m = re.search(r'PEDIMENTO\s+(\d+)', obs.upper())
            if m:
                subdivision['pedimento_relacionado'] = m.group(1)
        datos['subdivision'] = subdivision

    return datos, advertencias


# ---------------------------------------------------------------------------
# Llamada a Claude
# ---------------------------------------------------------------------------

def ia_habilitada():
    return bool(getattr(settings, 'IA_HABILITADA', False) and getattr(settings, 'ANTHROPIC_API_KEY', ''))


def _modelo():
    return os.getenv('IA_MODELO_EXTRACCION') or MODELO_DEFAULT


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=TIMEOUT)


def _bloque_documento(datos):
    """Convierte los bytes del archivo en el bloque de contenido para Claude."""
    if datos[:5] == b'%PDF-':
        return {
            'type': 'document',
            'source': {
                'type': 'base64',
                'media_type': 'application/pdf',
                'data': base64.standard_b64encode(datos).decode('ascii'),
            },
        }
    try:
        png = _imagen_a_png(datos)
    except Exception:
        png = None
    if png:
        return {
            'type': 'image',
            'source': {
                'type': 'base64',
                'media_type': 'image/png',
                'data': base64.standard_b64encode(png).decode('ascii'),
            },
        }
    try:
        texto = datos.decode('utf-8')
    except UnicodeDecodeError:
        raise ExtraccionError('Formato de archivo no soportado para extracción')
    return {'type': 'text', 'text': f'<documento>\n{texto[:MAX_CARACTERES_TEXTO]}\n</documento>'}


def _cerrar_objetos(nodo):
    """JSON outputs exige additionalProperties: false y todas las claves en required."""
    if isinstance(nodo, dict):
        nodo = {k: _cerrar_objetos(v) for k, v in nodo.items()}
        if nodo.get('type') == 'object' and 'properties' in nodo:
            nodo['additionalProperties'] = False
            nodo['required'] = list(nodo['properties'])
        return nodo
    if isinstance(nodo, list):
        return [_cerrar_objetos(v) for v in nodo]
    return nodo


def esquema_json(tipo):
    return _cerrar_objetos({
        'type': 'object',
        'description': ESQUEMAS[tipo]['descripcion'],
        'properties': ESQUEMAS[tipo]['propiedades'],
    })


def _precio(nombre):
    valor = os.getenv(nombre)
    return _decimal(valor) if valor else None


def uso_y_costo(respuesta):
    """Tokens de la respuesta y costo en USD si hay precios configurados."""
    uso = getattr(respuesta, 'usage', None)
    entrada = int(getattr(uso, 'input_tokens', 0) or 0)
    salida = int(getattr(uso, 'output_tokens', 0) or 0)
    resultado = {'input_tokens': entrada, 'output_tokens': salida, 'costo_usd': None}
    precio_entrada = _precio('IA_COSTO_ENTRADA_USD_MTOK')
    precio_salida = _precio('IA_COSTO_SALIDA_USD_MTOK')
    if precio_entrada is not None and precio_salida is not None:
        costo = (Decimal(entrada) * precio_entrada + Decimal(salida) * precio_salida) / Decimal(1_000_000)
        resultado['costo_usd'] = str(costo.quantize(Decimal('0.000001')))
    return resultado


def llamar_claude(tipo, datos_archivo):
    contenido = [_bloque_documento(datos_archivo), {'type': 'text', 'text': INSTRUCCIONES}]
    # extra_body para no depender de la versión del SDK instalada: las
    # versiones viejas no aceptan output_config como argumento con nombre.
    output_config = {'format': {'type': 'json_schema', 'schema': esquema_json(tipo)}}
    try:
        respuesta = _client().messages.create(
            model=_modelo(),
            max_tokens=MAX_TOKENS_POR_TIPO.get(tipo, MAX_TOKENS),
            messages=[{'role': 'user', 'content': contenido}],
            extra_body={'output_config': output_config},
        )
    except Exception as e:
        logger.error('Extracción %s: error al llamar a Claude: %s', tipo, e)
        raise ExtraccionError(f'Error al llamar a Claude: {e}') from e

    stop_reason = getattr(respuesta, 'stop_reason', None)
    if stop_reason == 'max_tokens':
        logger.error('Extracción %s: respuesta cortada por max_tokens', tipo)
        raise ExtraccionError('El documento es muy largo: la respuesta se cortó (max_tokens)')
    if stop_reason == 'refusal':
        logger.error('Extracción %s: Claude rechazó la solicitud', tipo)
        raise ExtraccionError('Claude rechazó procesar el documento')

    texto = next(
        (b.text for b in respuesta.content if getattr(b, 'type', None) == 'text'),
        None,
    )
    if not texto:
        logger.error('Extracción %s: respuesta sin texto (stop_reason=%s)', tipo, stop_reason)
        raise ExtraccionError('Claude no regresó los datos estructurados')
    try:
        datos = json.loads(texto)
    except ValueError as e:
        logger.error('Extracción %s: JSON inválido: %s', tipo, texto[:300])
        raise ExtraccionError('Claude regresó un JSON inválido') from e
    if not isinstance(datos, dict):
        raise ExtraccionError('Claude regresó un JSON con forma inesperada')
    return datos, getattr(respuesta, 'model', _modelo()), uso_y_costo(respuesta)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def extraer_datos_documento(fuente, tipo):
    """Extrae los datos de un archivo según su tipo. Nunca lanza excepción."""
    if tipo not in ESQUEMAS:
        return {'meta': {'tipo': tipo, 'estado': ESTADO_NO_APLICA}}
    if not ia_habilitada():
        return {'meta': {'tipo': tipo, 'estado': ESTADO_SIN_IA}}

    datos_archivo = _leer_bytes(fuente)
    if not datos_archivo:
        return {'meta': {'tipo': tipo, 'estado': ESTADO_ERROR, 'error': 'Archivo vacío o ilegible'}}

    try:
        crudos, modelo, uso = llamar_claude(tipo, datos_archivo)
    except ExtraccionError as e:
        return {'meta': {'tipo': tipo, 'estado': ESTADO_ERROR, 'error': str(e)}}

    datos, advertencias = validar_y_normalizar(tipo, crudos)
    datos['meta'] = {
        'tipo': tipo,
        'estado': ESTADO_REVISION if advertencias else ESTADO_OK,
        'modelo': modelo,
        'advertencias': advertencias,
        'uso': uso,
    }
    return datos


def extraer_datos_bl(fuente):
    """Compatibilidad con el código existente."""
    return extraer_datos_documento(fuente, 'BL')


def extraccion_vigente(doc):
    """True si datos_extraidos ya corresponde al tipo actual del documento."""
    meta = (doc.datos_extraidos or {}).get('meta') or {}
    return meta.get('tipo') == doc.tipo_documento and meta.get('estado') in (ESTADO_OK, ESTADO_REVISION)
