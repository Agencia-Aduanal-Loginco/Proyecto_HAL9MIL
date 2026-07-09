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


def _extraer_apm(root) -> DatosAduanales:
    # Addenda Edicom: <customized><APMTLZC><PEDIMENTO>... (con namespace default)
    campos = {}
    for el in root.iter():
        if el.tag.endswith('}APMTLZC') or el.tag == 'APMTLZC':
            for hijo in el:
                local = hijo.tag.split('}')[-1]
                campos[local] = (hijo.text or '').strip()
            break
    patente = campos.get('AGENTEADUANAL', '').split('/')[0].strip()
    # El contenedor viene como prefijo en la Descripcion de cada concepto
    contenedor = ''
    for concepto in root.iter(f'{{{_ns(root)}}}Concepto'):
        m = RE_CONTENEDOR.match(concepto.get('Descripcion') or '')
        if m:
            contenedor = m.group(1)
            break
    return DatosAduanales(
        patente=patente,
        pedimento=campos.get('PEDIMENTO', ''),
        contenedor=contenedor,
        bl=campos.get('CONOCIMIENTO', ''),
    )


_EXTRACTORES = {RFC_LCT: _extraer_lct, RFC_APM: _extraer_apm}


def buscar_referencia(datos):
    """Cascada de coincidencia XML → Referencia (ver spec).

    1. (patente, num_pedimento) debe dar exactamente una referencia.
    2. El contenedor, si existe en la BD, debe apuntar a esa misma
       referencia; si la contradice, queda pendiente.
    3. Sin patente/pedimento utilizables: el contenedor liga solo si da
       exactamente una referencia.

    Devuelve (referencia | None, motivo). motivo es '' cuando hay match.
    """
    from referencias.models import Contenedor, Referencia

    if datos is None:
        return None, 'proveedor no soportado'

    if datos.patente and datos.pedimento:
        candidatas = list(Referencia.objects.filter(
            patente=datos.patente, num_pedimento=datos.pedimento,
        )[:2])
        if len(candidatas) > 1:
            return None, (f'varias referencias para patente {datos.patente} '
                          f'/ pedimento {datos.pedimento}')
        if not candidatas:
            return None, (f'sin referencia para patente {datos.patente} '
                          f'/ pedimento {datos.pedimento}')
        candidata = candidatas[0]
        if datos.contenedor:
            refs_cont = set(
                Contenedor.objects.filter(num_cont=datos.contenedor)
                .values_list('referencia_id', flat=True)
            )
            if refs_cont and candidata.id not in refs_cont:
                return None, (f'contenedor {datos.contenedor} contradice '
                              f'patente {datos.patente} / pedimento {datos.pedimento}')
        return candidata, ''

    if datos.contenedor:
        ref_ids = list(
            Contenedor.objects.filter(num_cont=datos.contenedor)
            .values_list('referencia_id', flat=True).distinct()
        )
        if len(ref_ids) == 1:
            return Referencia.objects.get(pk=ref_ids[0]), ''
        if len(ref_ids) > 1:
            return None, (f'contenedor {datos.contenedor} aparece en '
                          f'varias referencias')
        return None, f'sin referencia para contenedor {datos.contenedor}'

    return None, 'sin datos aduanales en el XML'
