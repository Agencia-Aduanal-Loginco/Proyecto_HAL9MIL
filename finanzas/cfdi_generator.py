"""
Generador XML CFDI 4.0 con sello CSD.

Implementa:
  - Construcción del árbol XML con lxml (namespace cfdi 4.0)
  - Cadena original según Anexo 20 SAT (sin XSLT, implementación directa)
  - Firma SHA-256/RSA con la llave privada del CSD (cryptography)

Limitaciones de alcance:
  - Una sola moneda (MXN, sin TipoCambio)
  - Sin descuentos a nivel comprobante ni concepto
  - Sin CfdiRelacionados ni InformacionGlobal
  - Sin retenciones (solo IVA trasladado)
  - Exportación 01 (no aplica) — uso doméstico
"""

import base64
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

CFDI_NS = 'http://www.sat.gob.mx/cfd/4'
XSI_NS  = 'http://www.w3.org/2001/XMLSchema-instance'
SCHEMA_LOCATION = (
    'http://www.sat.gob.mx/cfd/4 '
    'http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd'
)


# ── Utilidades de certificado ─────────────────────────────────────────────────

def _cargar_certificado(cert_path: str) -> tuple[str, str]:
    """
    Carga el .cer (DER) del CSD.
    Retorna (cert_b64, no_certificado_20_digits).
    """
    with open(cert_path, 'rb') as f:
        cert_data = f.read()
    cert = x509.load_der_x509_certificate(cert_data)
    cert_b64 = base64.b64encode(cert_data).decode('ascii')
    no_certificado = str(cert.serial_number).zfill(20)
    return cert_b64, no_certificado


def _cargar_llave(key_path: str, password: str):
    """
    Carga el .key (DER PKCS#8 encrypted) del CSD.
    Retorna el objeto RSAPrivateKey de cryptography.
    """
    with open(key_path, 'rb') as f:
        key_data = f.read()
    return serialization.load_der_private_key(key_data, password=password.encode('utf-8'))


# ── Formateo numérico ─────────────────────────────────────────────────────────

def _d2(value) -> str:
    """Decimal a string con 2 decimales (SAT importes)."""
    return str(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def _d6(value) -> str:
    """Decimal a string con 6 decimales (SAT cantidades y tasas)."""
    return str(Decimal(str(value)).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP))


# ── Cadena original (Anexo 20 CFDI 4.0) ──────────────────────────────────────

def _attrs(*values) -> str:
    """Une valores no vacíos con '|'."""
    return '|'.join(str(v) for v in values if v not in (None, '', 'None'))


def _cadena_original(root: etree._Element) -> str:
    """
    Construye la cadena original del Comprobante siguiendo el orden del
    Anexo 20 SAT para CFDI 4.0. No usa XSLT — implementación directa.

    Orden de secciones (cada una delimitada por ||):
      Comprobante attrs → Emisor → Receptor →
      por cada Concepto: Concepto attrs → ImpuestosConcepto/Traslados →
      Impuestos (attrs) → Impuestos/Traslados
    """
    ns = {'c': CFDI_NS}
    g = root.get  # shorthand

    segments: list[str] = []

    # ── Comprobante ───────────────────────────────────────────────────────────
    segments.append(_attrs(
        g('Version'),
        g('Serie'),
        g('Folio'),
        g('Fecha'),
        g('FormaPago'),
        g('NoCertificado'),
        g('Certificado'),
        g('CondicionesDePago'),   # optional
        g('SubTotal'),
        g('Descuento'),           # optional
        g('Moneda'),
        g('TipoCambio'),          # optional (1.0 omitido para MXN)
        g('Total'),
        g('TipoDeComprobante'),
        g('Exportacion'),
        g('MetodoPago'),
        g('LugarExpedicion'),
        g('Confirmacion'),        # optional
    ))

    # ── Emisor ────────────────────────────────────────────────────────────────
    e = root.find('c:Emisor', ns)
    segments.append(_attrs(
        e.get('Rfc'),
        e.get('Nombre'),
        e.get('FacAtrAdquirente'),  # optional
        e.get('RegimenFiscal'),
    ))

    # ── Receptor ──────────────────────────────────────────────────────────────
    r = root.find('c:Receptor', ns)
    segments.append(_attrs(
        r.get('Rfc'),
        r.get('Nombre'),
        r.get('DomicilioFiscalReceptor'),
        r.get('ResidenciaFiscal'),   # optional
        r.get('NumRegIdTrib'),       # optional
        r.get('RegimenFiscalReceptor'),
        r.get('UsoCFDI'),
    ))

    # ── Conceptos ─────────────────────────────────────────────────────────────
    for concepto in root.findall('c:Conceptos/c:Concepto', ns):
        cg = concepto.get
        segments.append(_attrs(
            cg('ClaveProdServ'),
            cg('NoIdentificacion'),  # optional
            cg('Cantidad'),
            cg('ClaveUnidad'),
            cg('Unidad'),            # optional
            cg('Descripcion'),
            cg('ValorUnitario'),
            cg('Importe'),
            cg('Descuento'),         # optional
            cg('ObjetoImp'),
        ))
        # ImpuestosConcepto / Traslados
        for t in concepto.findall('c:Impuestos/c:Traslados/c:Traslado', ns):
            segments.append(_attrs(
                t.get('Base'), t.get('Impuesto'),
                t.get('TipoFactor'), t.get('TasaOCuota'), t.get('Importe'),
            ))
        # ImpuestosConcepto / Retenciones
        for t in concepto.findall('c:Impuestos/c:Retenciones/c:Retencion', ns):
            segments.append(_attrs(
                t.get('Base'), t.get('Impuesto'),
                t.get('TipoFactor'), t.get('TasaOCuota'), t.get('Importe'),
            ))

    # ── Impuestos globales ────────────────────────────────────────────────────
    imp = root.find('c:Impuestos', ns)
    if imp is not None:
        # Atributos del nodo Impuestos primero
        segments.append(_attrs(
            imp.get('TotalImpuestosRetenidos'),   # optional
            imp.get('TotalImpuestosTrasladados'),  # optional
        ))
        # Retenciones globales
        for t in imp.findall('c:Retenciones/c:Retencion', ns):
            segments.append(_attrs(
                t.get('Base'), t.get('Impuesto'),
                t.get('TipoFactor'), t.get('TasaOCuota'), t.get('Importe'),
            ))
        # Traslados globales
        for t in imp.findall('c:Traslados/c:Traslado', ns):
            segments.append(_attrs(
                t.get('Base'), t.get('Impuesto'),
                t.get('TipoFactor'), t.get('TasaOCuota'), t.get('Importe'),
            ))

    # Filtrar segmentos vacíos que resultan de nodos sin atributos opcionales
    non_empty = [s for s in segments if s]
    return '||' + '||'.join(non_empty) + '||'


# ── Constructor del árbol XML ─────────────────────────────────────────────────

def _build_xml(factura, cert_b64: str, no_certificado: str, fecha: str) -> etree._Element:
    """Construye el árbol lxml del Comprobante con Sello vacío (para cadena original)."""
    nsmap = {None: CFDI_NS, 'xsi': XSI_NS}
    root = etree.Element(f'{{{CFDI_NS}}}Comprobante', nsmap=nsmap)
    root.set(f'{{{XSI_NS}}}schemaLocation', SCHEMA_LOCATION)

    root.set('Version', '4.0')
    if factura.serie:
        root.set('Serie', factura.serie)
    root.set('Folio', str(factura.folio))
    root.set('Fecha', fecha)
    root.set('Sello', '')          # se llenará después de firmar
    root.set('FormaPago', factura.forma_pago)
    root.set('NoCertificado', no_certificado)
    root.set('Certificado', cert_b64)
    root.set('SubTotal', _d2(factura.subtotal))
    root.set('Moneda', factura.moneda)
    root.set('Total', _d2(factura.total))
    root.set('TipoDeComprobante', 'I')
    root.set('Exportacion', '01')
    root.set('MetodoPago', factura.metodo_pago)
    root.set('LugarExpedicion', factura.configuracion_fiscal.codigo_postal)

    # Emisor
    cfg = factura.configuracion_fiscal
    em = etree.SubElement(root, f'{{{CFDI_NS}}}Emisor')
    em.set('Rfc', cfg.rfc)
    em.set('Nombre', cfg.razon_social)
    em.set('RegimenFiscal', cfg.regimen_fiscal)

    # Receptor
    re = etree.SubElement(root, f'{{{CFDI_NS}}}Receptor')
    re.set('Rfc', factura.rfc_receptor)
    re.set('Nombre', factura.nombre_receptor)
    re.set('DomicilioFiscalReceptor', factura.domicilio_fiscal_receptor)
    re.set('RegimenFiscalReceptor', factura.regimen_fiscal_receptor)
    re.set('UsoCFDI', factura.uso_cfdi)

    # Conceptos
    conceptos_el = etree.SubElement(root, f'{{{CFDI_NS}}}Conceptos')
    total_iva = Decimal('0')
    last_tasa = Decimal('0.16')

    for concepto in factura.conceptos.all():
        c_el = etree.SubElement(conceptos_el, f'{{{CFDI_NS}}}Concepto')
        c_el.set('ClaveProdServ', concepto.clave_prod_serv)
        c_el.set('Cantidad', _d6(concepto.cantidad))
        c_el.set('ClaveUnidad', concepto.clave_unidad)
        c_el.set('Descripcion', concepto.descripcion)
        c_el.set('ValorUnitario', _d2(concepto.valor_unitario))
        c_el.set('Importe', _d2(concepto.importe))
        c_el.set('ObjetoImp', concepto.objeto_imp)

        if concepto.objeto_imp == '02':
            last_tasa = concepto.tasa_iva
            iva_c = (concepto.importe * concepto.tasa_iva).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            total_iva += iva_c
            imp_c = etree.SubElement(c_el, f'{{{CFDI_NS}}}Impuestos')
            tras_c = etree.SubElement(imp_c, f'{{{CFDI_NS}}}Traslados')
            t_c = etree.SubElement(tras_c, f'{{{CFDI_NS}}}Traslado')
            t_c.set('Base', _d2(concepto.importe))
            t_c.set('Impuesto', '002')
            t_c.set('TipoFactor', 'Tasa')
            t_c.set('TasaOCuota', _d6(concepto.tasa_iva))
            t_c.set('Importe', _d2(iva_c))

    # Impuestos globales
    if total_iva > 0:
        imp_el = etree.SubElement(root, f'{{{CFDI_NS}}}Impuestos')
        imp_el.set('TotalImpuestosTrasladados', _d2(total_iva))
        tras_el = etree.SubElement(imp_el, f'{{{CFDI_NS}}}Traslados')
        t_el = etree.SubElement(tras_el, f'{{{CFDI_NS}}}Traslado')
        t_el.set('Base', _d2(factura.subtotal))
        t_el.set('Impuesto', '002')
        t_el.set('TipoFactor', 'Tasa')
        t_el.set('TasaOCuota', _d6(last_tasa))
        t_el.set('Importe', _d2(total_iva))

    return root


# ── Punto de entrada público ──────────────────────────────────────────────────

def generar_xml_cfdi40(factura) -> str:
    """
    Genera el XML CFDI 4.0 sellado con el CSD de la patente correspondiente.

    - Lee el .cer y .key de ConfiguracionFiscal.cert_path / key_path.
    - Lee la contraseña del .key desde la variable de entorno (nunca de BD).
    - Calcula la cadena original y aplica firma SHA-256/RSA.
    - Retorna el XML como str UTF-8 listo para enviar al PAC.

    Raises:
        FileNotFoundError: si cert_path o key_path no existen.
        ValueError: si la contraseña de la llave es incorrecta o falta la env var.
        Exception: cualquier error de cryptography al cargar el CSD.
    """
    config = factura.configuracion_fiscal
    key_password = config.get_key_password()

    cert_b64, no_certificado = _cargar_certificado(config.cert_path)
    private_key = _cargar_llave(config.key_path, key_password)

    fecha = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    root = _build_xml(factura, cert_b64, no_certificado, fecha)

    # Cadena original (Sello="" en este momento)
    cadena = _cadena_original(root)

    # Firma SHA-256 con RSA PKCS#1 v1.5
    firma = private_key.sign(
        cadena.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    root.set('Sello', base64.b64encode(firma).decode('ascii'))

    return etree.tostring(
        root,
        xml_declaration=True,
        encoding='UTF-8',
        pretty_print=False,
    ).decode('utf-8')


# ── Complemento de Pago (Pagos 2.0) ──────────────────────────────────────────

PAGOS20_NS = 'http://www.sat.gob.mx/Pagos20'
PAGOS20_SCHEMA_LOCATION = (
    'http://www.sat.gob.mx/cfd/4 '
    'http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd '
    'http://www.sat.gob.mx/Pagos20 '
    'http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos20.xsd'
)


def _cadena_original_complemento_pago(root: etree._Element) -> str:
    """
    Cadena original para CFDI 4.0 tipo P con complemento Pagos20.

    Secciones:
      Comprobante → Emisor → Receptor → Conceptos →
      pago20:Pagos Version →
      pago20:Totales attrs →
      por cada pago20:Pago:
        Pago attrs →
        por cada pago20:DoctoRelacionado:
          DoctoRelacionado attrs →
          por cada pago20:TrasladoDR →
        por cada pago20:TrasladoP
    """
    ns_c  = {'c':  CFDI_NS}
    ns_p  = {'p':  PAGOS20_NS}
    ns_cp = {'c': CFDI_NS, 'p': PAGOS20_NS}

    segments: list[str] = []

    # ── Comprobante (mismo orden que tipo I, sin FormaPago/MetodoPago) ────────
    g = root.get
    segments.append(_attrs(
        g('Version'),
        g('Serie'),
        g('Folio'),
        g('Fecha'),
        g('NoCertificado'),
        g('Certificado'),
        g('SubTotal'),
        g('Moneda'),
        g('Total'),
        g('TipoDeComprobante'),
        g('Exportacion'),
        g('LugarExpedicion'),
    ))

    # ── Emisor ────────────────────────────────────────────────────────────────
    e = root.find('c:Emisor', ns_c)
    segments.append(_attrs(e.get('Rfc'), e.get('Nombre'), e.get('RegimenFiscal')))

    # ── Receptor ──────────────────────────────────────────────────────────────
    r = root.find('c:Receptor', ns_c)
    segments.append(_attrs(
        r.get('Rfc'), r.get('Nombre'),
        r.get('DomicilioFiscalReceptor'),
        r.get('RegimenFiscalReceptor'),
        r.get('UsoCFDI'),
    ))

    # ── Concepto genérico (valor 0) ───────────────────────────────────────────
    for c in root.findall('c:Conceptos/c:Concepto', ns_c):
        segments.append(_attrs(
            c.get('ClaveProdServ'),
            c.get('Cantidad'),
            c.get('ClaveUnidad'),
            c.get('Descripcion'),
            c.get('ValorUnitario'),
            c.get('Importe'),
            c.get('ObjetoImp'),
        ))

    # ── pago20:Pagos ──────────────────────────────────────────────────────────
    pagos_el = root.find('c:Complemento/p:Pagos', ns_cp)
    segments.append(_attrs(pagos_el.get('Version')))

    # pago20:Totales
    tot = pagos_el.find('p:Totales', ns_p)
    segments.append(_attrs(
        tot.get('TotalRetencionesIVA'),
        tot.get('TotalRetencionesISR'),
        tot.get('TotalRetencionesIEPS'),
        tot.get('TotalTrasladosBaseIVA16'),
        tot.get('TotalTrasladosImpuestoIVA16'),
        tot.get('TotalTrasladosBaseIVA8'),
        tot.get('TotalTrasladosImpuestoIVA8'),
        tot.get('TotalTrasladosBaseIVA0'),
        tot.get('TotalTrasladosImpuestoIVA0'),
        tot.get('TotalTrasladosBaseIVAExento'),
        tot.get('MontoTotalPagos'),
    ))

    # Cada pago20:Pago
    for pago_el in pagos_el.findall('p:Pago', ns_p):
        segments.append(_attrs(
            pago_el.get('FechaPago'),
            pago_el.get('FormaDePagoP'),
            pago_el.get('MonedaP'),
            pago_el.get('TipoCambioPP'),
            pago_el.get('Monto'),
            pago_el.get('NumOperacion'),
        ))

        # Cada pago20:DoctoRelacionado
        for dr in pago_el.findall('p:DoctoRelacionado', ns_p):
            segments.append(_attrs(
                dr.get('IdDocumento'),
                dr.get('Serie'),
                dr.get('Folio'),
                dr.get('MonedaDR'),
                dr.get('EquivalenciaDR'),
                dr.get('NumParcialidad'),
                dr.get('ImpSaldoAnt'),
                dr.get('ImpPagado'),
                dr.get('ImpSaldoInsoluto'),
                dr.get('ObjetoImpDR'),
            ))
            # TrasladosDR
            for t in dr.findall('p:ImpuestosDR/p:TrasladosDR/p:TrasladoDR', ns_p):
                segments.append(_attrs(
                    t.get('BaseDR'), t.get('ImpuestoDR'),
                    t.get('TipoFactorDR'), t.get('TasaOCuotaDR'), t.get('ImporteDR'),
                ))

        # ImpuestosP / TrasladosP
        for t in pago_el.findall('p:ImpuestosP/p:TrasladosP/p:TrasladoP', ns_p):
            segments.append(_attrs(
                t.get('BaseP'), t.get('ImpuestoP'),
                t.get('TipoFactorP'), t.get('TasaOCuotaP'), t.get('ImporteP'),
            ))

    non_empty = [s for s in segments if s]
    return '||' + '||'.join(non_empty) + '||'


def generar_xml_complemento_pago(pago) -> str:
    """
    Genera el XML CFDI 4.0 tipo P (Complemento de Pago) sellado con el CSD
    del emisor de la factura relacionada.

    El emisor se obtiene de pago.documentos.first().factura.configuracion_fiscal.
    Todos los DoctoRelacionado de un Pago deben pertenecer al mismo emisor.

    Raises:
        ValueError: si el pago no tiene documentos relacionados o la config CSD falta.
        FileNotFoundError: si los archivos .cer/.key no existen.
    """
    doctos = list(pago.documentos.select_related('factura__configuracion_fiscal').all())
    if not doctos:
        raise ValueError('El Pago no tiene documentos relacionados.')

    config = doctos[0].factura.configuracion_fiscal
    key_password = config.get_key_password()
    cert_b64, no_certificado = _cargar_certificado(config.cert_path)
    private_key = _cargar_llave(config.key_path, key_password)

    fecha = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    # CFDI P usa folio propio — usamos pk del Pago como folio y serie 'P'
    folio_pago = str(pago.pk)

    # Calcular bases e IVA proporcional por DoctoRelacionado
    TASA_IVA = Decimal('0.16')
    base_total_p = Decimal('0')
    iva_total_p  = Decimal('0')

    dr_data: list[dict] = []
    for dr in doctos:
        factura = dr.factura
        # Base proporcional: imp_pagado × (subtotal / total)
        if factura.total > 0:
            base_dr = (dr.imp_pagado * factura.subtotal / factura.total).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
        else:
            base_dr = Decimal('0')
        iva_dr = (base_dr * TASA_IVA).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        base_total_p += base_dr
        iva_total_p  += iva_dr
        dr_data.append({
            'dr': dr, 'factura': factura,
            'base_dr': base_dr, 'iva_dr': iva_dr,
        })

    # ── Construir árbol XML ───────────────────────────────────────────────────
    nsmap = {None: CFDI_NS, 'xsi': XSI_NS}
    root = etree.Element(f'{{{CFDI_NS}}}Comprobante', nsmap=nsmap)
    root.set(f'{{{XSI_NS}}}schemaLocation', PAGOS20_SCHEMA_LOCATION)
    root.set('Version', '4.0')
    root.set('Serie', 'P')
    root.set('Folio', folio_pago)
    root.set('Fecha', fecha)
    root.set('Sello', '')
    root.set('NoCertificado', no_certificado)
    root.set('Certificado', cert_b64)
    root.set('SubTotal', '0')
    root.set('Moneda', 'XXX')  # No aplica para tipo P
    root.set('Total', '0')
    root.set('TipoDeComprobante', 'P')
    root.set('Exportacion', '01')
    root.set('LugarExpedicion', config.codigo_postal)

    # Emisor
    em = etree.SubElement(root, f'{{{CFDI_NS}}}Emisor')
    em.set('Rfc', config.rfc)
    em.set('Nombre', config.razon_social)
    em.set('RegimenFiscal', config.regimen_fiscal)

    # Receptor (tomado del primer DoctoRelacionado)
    primera_factura = doctos[0].factura
    re = etree.SubElement(root, f'{{{CFDI_NS}}}Receptor')
    re.set('Rfc', primera_factura.rfc_receptor)
    re.set('Nombre', primera_factura.nombre_receptor)
    re.set('DomicilioFiscalReceptor', primera_factura.domicilio_fiscal_receptor)
    re.set('RegimenFiscalReceptor', primera_factura.regimen_fiscal_receptor)
    re.set('UsoCFDI', 'CP01')  # Código SAT obligatorio para complemento de pago

    # Concepto genérico requerido por SAT para tipo P
    conceptos_el = etree.SubElement(root, f'{{{CFDI_NS}}}Conceptos')
    c_gen = etree.SubElement(conceptos_el, f'{{{CFDI_NS}}}Concepto')
    c_gen.set('ClaveProdServ', '84111506')
    c_gen.set('ClaveUnidad', 'ACT')
    c_gen.set('Cantidad', '1')
    c_gen.set('Descripcion', 'Pago')
    c_gen.set('ValorUnitario', '0')
    c_gen.set('Importe', '0')
    c_gen.set('ObjetoImp', '01')  # No objeto de impuesto

    # Complemento → Pagos20
    comp_el = etree.SubElement(root, f'{{{CFDI_NS}}}Complemento')
    pagos_el = etree.SubElement(comp_el, f'{{{PAGOS20_NS}}}Pagos')
    pagos_el.set('xmlns:pago20', PAGOS20_NS)
    pagos_el.set('Version', '2.0')

    # Totales
    tot_el = etree.SubElement(pagos_el, f'{{{PAGOS20_NS}}}Totales')
    if base_total_p > 0:
        tot_el.set('TotalTrasladosBaseIVA16', _d2(base_total_p))
        tot_el.set('TotalTrasladosImpuestoIVA16', _d2(iva_total_p))
    tot_el.set('MontoTotalPagos', _d2(pago.monto))

    # Pago
    pago_el = etree.SubElement(pagos_el, f'{{{PAGOS20_NS}}}Pago')
    pago_el.set('FechaPago', f'{pago.fecha_pago}T12:00:00')
    pago_el.set('FormaDePagoP', pago.forma_pago)
    pago_el.set('MonedaP', pago.moneda)
    pago_el.set('Monto', _d2(pago.monto))
    if pago.num_operacion:
        pago_el.set('NumOperacion', pago.num_operacion)

    # DoctoRelacionados
    for item in dr_data:
        dr = item['dr']
        factura = item['factura']
        dr_el = etree.SubElement(pago_el, f'{{{PAGOS20_NS}}}DoctoRelacionado')
        dr_el.set('IdDocumento', str(factura.uuid_fiscal))
        if factura.serie:
            dr_el.set('Serie', factura.serie)
        dr_el.set('Folio', str(factura.folio))
        dr_el.set('MonedaDR', factura.moneda)
        dr_el.set('EquivalenciaDR', '1')
        dr_el.set('NumParcialidad', str(dr.num_parcialidad))
        dr_el.set('ImpSaldoAnt', _d2(dr.imp_saldo_anterior))
        dr_el.set('ImpPagado', _d2(dr.imp_pagado))
        dr_el.set('ImpSaldoInsoluto', _d2(dr.imp_saldo_insoluto))
        dr_el.set('ObjetoImpDR', '02')

        if item['base_dr'] > 0:
            imp_dr = etree.SubElement(dr_el, f'{{{PAGOS20_NS}}}ImpuestosDR')
            tras_dr = etree.SubElement(imp_dr, f'{{{PAGOS20_NS}}}TrasladosDR')
            t_dr = etree.SubElement(tras_dr, f'{{{PAGOS20_NS}}}TrasladoDR')
            t_dr.set('BaseDR', _d2(item['base_dr']))
            t_dr.set('ImpuestoDR', '002')
            t_dr.set('TipoFactorDR', 'Tasa')
            t_dr.set('TasaOCuotaDR', _d6(TASA_IVA))
            t_dr.set('ImporteDR', _d2(item['iva_dr']))

    # ImpuestosP globales del Pago
    if iva_total_p > 0:
        imp_p = etree.SubElement(pago_el, f'{{{PAGOS20_NS}}}ImpuestosP')
        tras_p = etree.SubElement(imp_p, f'{{{PAGOS20_NS}}}TrasladosP')
        t_p = etree.SubElement(tras_p, f'{{{PAGOS20_NS}}}TrasladoP')
        t_p.set('BaseP', _d2(base_total_p))
        t_p.set('ImpuestoP', '002')
        t_p.set('TipoFactorP', 'Tasa')
        t_p.set('TasaOCuotaP', _d6(TASA_IVA))
        t_p.set('ImporteP', _d2(iva_total_p))

    # ── Cadena original y firma ───────────────────────────────────────────────
    cadena = _cadena_original_complemento_pago(root)
    firma = private_key.sign(
        cadena.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    root.set('Sello', base64.b64encode(firma).decode('ascii'))

    return etree.tostring(
        root,
        xml_declaration=True,
        encoding='UTF-8',
        pretty_print=False,
    ).decode('utf-8')
