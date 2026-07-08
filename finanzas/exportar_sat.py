"""
Exportadores XML Contabilidad Electrónica SAT v1.3

Tres documentos requeridos por el SAT:
  1. Catálogo de cuentas   — CatalogoCuentas_1_3.xsd
  2. Balanza de comprobación — BalanzaComprobacion_1_3.xsd
  3. Pólizas del periodo    — PolizasPeriodo_1_3.xsd

Referencia: Anexo 24 del SAT, versión 1.3 (vigente desde enero 2015).
"""
from lxml import etree

from .balanza import calcular_balanza
from .models import CuentaContable, PolizaContable

# ── Namespaces SAT Contabilidad Electrónica v1.3 ─────────────────────────────

_NS_CAT  = 'http://www.sat.gob.mx/esquemas/ContabilidadE/1_3/CatalogoCuentas'
_NS_BCE  = 'http://www.sat.gob.mx/esquemas/ContabilidadE/1_3/BalanzaComprobacion'
_NS_PLZ  = 'http://www.sat.gob.mx/esquemas/ContabilidadE/1_3/PolizasPeriodo'
_NS_XSI  = 'http://www.w3.org/2001/XMLSchema-instance'

_SL_CAT = (
    f'{_NS_CAT} '
    'http://www.sat.gob.mx/sitio_internet/esquemas/ContabilidadE/1_3/'
    'CatalogoCuentas_1_3.xsd'
)
_SL_BCE = (
    f'{_NS_BCE} '
    'http://www.sat.gob.mx/sitio_internet/esquemas/ContabilidadE/1_3/'
    'BalanzaComprobacion_1_3.xsd'
)
_SL_PLZ = (
    f'{_NS_PLZ} '
    'http://www.sat.gob.mx/sitio_internet/esquemas/ContabilidadE/1_3/'
    'PolizasPeriodo_1_3.xsd'
)


def _mes2str(mes: int) -> str:
    return str(mes).zfill(2)


def _d2(value) -> str:
    return f'{value:.2f}'


def _xml_bytes(root: etree._Element) -> str:
    return etree.tostring(
        root,
        xml_declaration=True,
        encoding='UTF-8',
        pretty_print=True,
    ).decode('utf-8')


# ── 1. Catálogo de Cuentas ───────────────────────────────────────────────────

def exportar_catalogo_cuentas_xml(config) -> str:
    """
    Genera el XML del Catálogo de Cuentas para la patente/RFC indicada.

    config: instancia de ConfiguracionFiscal
    """
    nsmap = {None: _NS_CAT, 'xsi': _NS_XSI}
    root = etree.Element(f'{{{_NS_CAT}}}Catalogo', nsmap=nsmap)
    root.set(f'{{{_NS_XSI}}}schemaLocation', _SL_CAT)
    root.set('Version', '1.3')
    root.set('RFC', config.rfc)
    root.set('Sello', '')
    root.set('NoCertificado', '')
    root.set('Certificado', '')

    cuentas = CuentaContable.objects.all().order_by('numero')
    for c in cuentas:
        el = etree.SubElement(root, f'{{{_NS_CAT}}}Ctas')
        el.set('NumCta', c.numero)
        el.set('Desc', c.nombre)
        # SubCtaDe = número de la cuenta padre, vacío si es raíz
        el.set('SubCtaDe', c.padre.numero if c.padre else '')
        el.set('Nivel', str(c.nivel))
        el.set('Natur', c.naturaleza)
        if c.codigo_agrupador_sat:
            el.set('CodAgrup', c.codigo_agrupador_sat)

    return _xml_bytes(root)


# ── 2. Balanza de Comprobación ───────────────────────────────────────────────

def exportar_balanza_xml(mes: int, anio: int, config, tipo_envio: str = 'N') -> str:
    """
    Genera el XML de Balanza de Comprobación.

    tipo_envio: 'N'=Normal, 'C'=Complementaria, 'X'=Complementaria por cierre
    config    : instancia de ConfiguracionFiscal
    """
    nsmap = {None: _NS_BCE, 'xsi': _NS_XSI}
    root = etree.Element(f'{{{_NS_BCE}}}Balanza', nsmap=nsmap)
    root.set(f'{{{_NS_XSI}}}schemaLocation', _SL_BCE)
    root.set('Version', '1.3')
    root.set('RFC', config.rfc)
    root.set('Mes', _mes2str(mes))
    root.set('Anio', str(anio))
    root.set('TipoEnvio', tipo_envio)
    root.set('Sello', '')
    root.set('NoCertificado', '')
    root.set('Certificado', '')

    filas = calcular_balanza(mes, anio)
    for f in filas:
        el = etree.SubElement(root, f'{{{_NS_BCE}}}Ctas')
        el.set('NumCta', f['numero'])
        el.set('SaldoIni', _d2(f['saldo_inicial']))
        el.set('Debe', _d2(f['debe']))
        el.set('Haber', _d2(f['haber']))
        el.set('SaldoFin', _d2(f['saldo_final']))

    return _xml_bytes(root)


# ── 3. Pólizas del Periodo ───────────────────────────────────────────────────

# Tipo de póliza SAT → código para el nombre del archivo y atributo TipoSol
_TIPO_SAT = {'D': 'D', 'H': 'H', 'E': 'E'}


def exportar_polizas_xml(mes: int, anio: int, config, tipo: str) -> str:
    """
    Genera el XML de Pólizas del Periodo para el tipo indicado.

    tipo   : 'D'=Diario, 'H'=Ingreso/Haber, 'E'=Egreso
    config : instancia de ConfiguracionFiscal
    """
    if tipo not in _TIPO_SAT:
        raise ValueError(f'Tipo de póliza inválido: {tipo!r}. Debe ser D, H o E.')

    nsmap = {None: _NS_PLZ, 'xsi': _NS_XSI}
    root = etree.Element(f'{{{_NS_PLZ}}}Polizas', nsmap=nsmap)
    root.set(f'{{{_NS_XSI}}}schemaLocation', _SL_PLZ)
    root.set('Version', '1.3')
    root.set('RFC', config.rfc)
    root.set('Mes', _mes2str(mes))
    root.set('Anio', str(anio))
    root.set('TipoSolicitud', tipo)
    root.set('Sello', '')
    root.set('NoCertificado', '')
    root.set('Certificado', '')

    polizas = (
        PolizaContable.objects
        .filter(mes=mes, anio=anio, tipo=tipo)
        .prefetch_related('partidas__cuenta')
        .order_by('numero')
    )

    for p in polizas:
        p_el = etree.SubElement(root, f'{{{_NS_PLZ}}}Poliza')
        p_el.set('NumUnIdenPol', str(p.numero))
        p_el.set('Fecha', str(p.fecha))
        p_el.set('Concepto', p.concepto[:300])

        for partida in p.partidas.all():
            t_el = etree.SubElement(p_el, f'{{{_NS_PLZ}}}Transaccion')
            t_el.set('NumCta', partida.cuenta.numero)
            t_el.set('DesCta', partida.cuenta.nombre[:200])
            t_el.set('Concepto', partida.concepto[:200])
            t_el.set('Debe', _d2(partida.debe))
            t_el.set('Haber', _d2(partida.haber))

    return _xml_bytes(root)


def nombre_archivo_balanza(mes: int, anio: int, config) -> str:
    """Nombre oficial SAT: {RFC}{ANIO}{MES}BN.xml"""
    return f'{config.rfc}{anio}{_mes2str(mes)}BN.xml'


def nombre_archivo_catalogo(anio: int, mes: int, config) -> str:
    """Nombre oficial SAT: {RFC}{ANIO}{MES}CT.xml"""
    return f'{config.rfc}{anio}{_mes2str(mes)}CT.xml'


def nombre_archivo_polizas(mes: int, anio: int, tipo: str, config) -> str:
    """Nombre oficial SAT: {RFC}{ANIO}{MES}{TIPO}D.xml (Diario=D, Haber=H, Egreso=E)"""
    return f'{config.rfc}{anio}{_mes2str(mes)}{tipo}D.xml'
