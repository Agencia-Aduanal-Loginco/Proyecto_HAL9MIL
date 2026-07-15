import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, InvalidOperation

NS_CFDI4  = 'http://www.sat.gob.mx/cfd/4'
NS_CFDI3  = 'http://www.sat.gob.mx/cfd/3'
NS_TFD    = 'http://www.sat.gob.mx/TimbreFiscalDigital'
NS_PAGO20 = 'http://www.sat.gob.mx/Pagos20'
NS_PAGO10 = 'http://www.sat.gob.mx/Pagos'


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value or '0')
    except InvalidOperation:
        return Decimal('0')


def _detectar_ns(root) -> str:
    """Detecta el namespace (CFDI 3.3 o 4.0) del elemento raíz."""
    if NS_CFDI4 in root.tag:
        return NS_CFDI4
    if NS_CFDI3 in root.tag:
        return NS_CFDI3
    raise ValueError('El archivo no es un CFDI válido (namespace no reconocido)')


def parsear_cfdi(xml_path: str) -> dict:
    """
    Parsea CFDI 3.3 o 4.0 desde una ruta de archivo.
    Lanza ValueError si el XML no es un CFDI timbrado válido.
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as e:
        raise ValueError(f'XML malformado: {e}')
    return parsear_cfdi_root(root)


def parsear_cfdi_root(root) -> dict:
    """
    Igual que parsear_cfdi pero recibe el elemento raíz ya parseado.
    Retorna dict con:
        uuid, fecha (datetime), rfc_emisor, nombre_emisor, rfc_receptor,
        subtotal, iva, total (Decimal), moneda, tipo, concepto_principal
    """
    ns = _detectar_ns(root)

    nsmap = {'cfdi': ns, 'tfd': NS_TFD}

    emisor  = root.find('cfdi:Emisor', nsmap)
    receptor = root.find('cfdi:Receptor', nsmap)
    if emisor is None or receptor is None:
        raise ValueError('CFDI incompleto: falta nodo Emisor o Receptor')

    tfd = root.find('cfdi:Complemento/tfd:TimbreFiscalDigital', nsmap)
    if tfd is None:
        raise ValueError('CFDI sin TimbreFiscalDigital (no está timbrado)')

    uuid = tfd.get('UUID', '').strip()
    if not uuid:
        raise ValueError('CFDI sin UUID en el timbre fiscal')

    # Fecha de emisión
    fecha_str = root.get('Fecha', '')
    try:
        fecha = datetime.fromisoformat(fecha_str)
    except (ValueError, AttributeError):
        raise ValueError(f'Fecha de CFDI inválida: {fecha_str!r}')

    # Importes
    subtotal = _decimal(root.get('SubTotal', '0'))
    total    = _decimal(root.get('Total', '0'))
    moneda   = root.get('Moneda', 'MXN')
    tipo     = root.get('TipoDeComprobante', 'I')

    # IVA trasladado (impuesto 002)
    iva = Decimal('0')
    traslado = root.find(
        'cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado[@Impuesto="002"]', nsmap
    )
    if traslado is not None:
        iva = _decimal(traslado.get('Importe', '0'))

    # Primer concepto
    concepto_node = root.find('cfdi:Conceptos/cfdi:Concepto', nsmap)
    concepto = ''
    if concepto_node is not None:
        concepto = (concepto_node.get('Descripcion') or '')[:300]

    return {
        'uuid':             uuid,
        'fecha':            fecha,
        'rfc_emisor':       emisor.get('Rfc', ''),
        'nombre_emisor':    (emisor.get('Nombre') or '')[:200],
        'rfc_receptor':     receptor.get('Rfc', ''),
        'subtotal':         subtotal,
        'iva':              iva,
        'total':            total,
        'moneda':           moneda,
        'tipo':             tipo,
        'concepto_principal': concepto,
    }


def parsear_complemento_pago(root) -> list:
    """
    Extrae los DoctoRelacionado de un Complemento de Pago (CFDI tipo P).
    Retorna lista de dicts: {'uuid_factura', 'imp_pagado' (Decimal), 'moneda_pago'}.
    Soporta pago20 (CFDI 4.0) y pago10 (CFDI 3.3). Lanza ValueError si no
    encuentra el nodo Pagos o ningún DoctoRelacionado.
    """
    ns = _detectar_ns(root)
    nsmap = {'cfdi': ns, 'pago20': NS_PAGO20, 'pago10': NS_PAGO10}

    pagos = root.find('cfdi:Complemento/pago20:Pagos', nsmap)
    prefijo = 'pago20'
    if pagos is None:
        pagos = root.find('cfdi:Complemento/pago10:Pagos', nsmap)
        prefijo = 'pago10'
    if pagos is None:
        raise ValueError('Complemento de pago sin nodo Pagos')

    doctos = []
    for pago in pagos.findall(f'{prefijo}:Pago', nsmap):
        for docto in pago.findall(f'{prefijo}:DoctoRelacionado', nsmap):
            doctos.append({
                'uuid_factura': (docto.get('IdDocumento') or '').strip(),
                'imp_pagado': _decimal(docto.get('ImpPagado', '0')),
                'moneda_pago': docto.get('MonedaDR', 'MXN'),
            })
    if not doctos:
        raise ValueError('Complemento de pago sin DoctoRelacionado')
    return doctos
