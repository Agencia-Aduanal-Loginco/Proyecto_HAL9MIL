"""Extracción de datos aduanales (patente, pedimento, contenedor, BL) de las
addendas de los CFDI de proveedores de terminal portuaria.

Cada proveedor se identifica por el RFC del emisor y tiene su propio extractor
registrado en _EXTRACTORES. Ver spec:
docs/superpowers/specs/2026-07-09-carga-masiva-xml-design.md
"""
import re
from dataclasses import dataclass

NS_CFDI4 = 'http://www.sat.gob.mx/cfd/4'
NS_CFDI3 = 'http://www.sat.gob.mx/cfd/3'

RFC_LCT = 'LCT030408U39'
RFC_APM = 'ATL120106DC6'

# Contenedor ISO 6346: 4 letras + 7 dígitos, como prefijo "XXXX9999999-"
RE_CONTENEDOR = re.compile(r'^([A-Z]{4}\d{7})-')


@dataclass
class DatosAduanales:
    patente: str = ''
    pedimento: str = ''
    contenedor: str = ''
    bl: str = ''


def _ns(root) -> str:
    return NS_CFDI4 if NS_CFDI4 in root.tag else NS_CFDI3


def _rfc_emisor(root) -> str:
    emisor = root.find(f'{{{_ns(root)}}}Emisor')
    return emisor.get('Rfc', '') if emisor is not None else ''


def extraer_datos_aduanales(root):
    """Devuelve DatosAduanales según el proveedor, o None si el RFC del
    emisor no está soportado."""
    extractor = _EXTRACTORES.get(_rfc_emisor(root))
    if extractor is None:
        return None
    return extractor(root)


def _extraer_lct(root) -> DatosAduanales:
    # Addenda Diverza: <dvz:datosExtra atributo="LeyendaEspecialNN" valor="..."/>
    # Se busca por local-name para no depender del URI del namespace dvz.
    leyendas = {}
    for el in root.iter():
        if el.tag.endswith('}datosExtra') or el.tag == 'datosExtra':
            leyendas[el.get('atributo', '')] = el.get('valor') or ''
    pedimento = leyendas.get('LeyendaEspecial16', '').strip()
    if '-' in pedimento:
        pedimento = pedimento.split('-')[-1].strip()
    return DatosAduanales(
        patente=leyendas.get('LeyendaEspecial15', '').strip(),
        pedimento=pedimento,
        contenedor=leyendas.get('LeyendaEspecial25', '').replace(' ', ''),
        bl=leyendas.get('LeyendaEspecial20', '').strip(),
    )


_EXTRACTORES = {RFC_LCT: _extraer_lct}
