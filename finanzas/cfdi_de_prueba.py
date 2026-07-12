"""Constructores de CFDI 4.0 sintéticos para tests.

Reproducen la estructura real de las facturas de LCT (addenda Diverza con
dvz:datosExtra) y APM (addenda Edicom con APMTLZC) sin datos fiscales reales.
"""

_PLANTILLA_LCT = '''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    Version="4.0" Serie="L" Folio="4563870" Fecha="2026-07-08T08:53:11"
    SubTotal="9563.79" Moneda="MXN" Total="11094.00" TipoDeComprobante="I"
    MetodoPago="PPD" LugarExpedicion="60950">
  <cfdi:Emisor Rfc="LCT030408U39" Nombre="L C TERMINAL PORTUARIA DE CONTENEDORES" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="CIN220216BS2" Nombre="CACIPA INTERNACIONAL" UsoCFDI="G03" DomicilioFiscalReceptor="90200" RegimenFiscalReceptor="601"/>
  <cfdi:Conceptos>
    <cfdi:Concepto Cantidad="1" ClaveProdServ="78141700" ClaveUnidad="E48" Descripcion="MUELLAJE" ValorUnitario="261.00" Importe="261.00" ObjetoImp="02"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="1530.21">
    <cfdi:Traslados>
      <cfdi:Traslado Impuesto="002" TipoFactor="Tasa" Base="9563.79" TasaOCuota="0.160000" Importe="1530.21"/>
    </cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="1.1" UUID="{uuid}" FechaTimbrado="2026-07-08T08:53:17"/>
  </cfdi:Complemento>
  <cfdi:Addenda>
    <dvz:addenda xmlns:dvz="http://www.diverza.com/addenda">
      <dvz:datosExtra valor="{patente}" atributo="LeyendaEspecial15"/>
      <dvz:datosExtra valor="{pedimento}" atributo="LeyendaEspecial16"/>
      <dvz:datosExtra valor="{bl}" atributo="LeyendaEspecial20"/>
      <dvz:datosExtra valor="{contenedor}" atributo="LeyendaEspecial25"/>
    </dvz:addenda>
  </cfdi:Addenda>
</cfdi:Comprobante>'''

_PLANTILLA_APM = '''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    Version="4.0" Serie="C" Folio="1786738" Fecha="2026-07-08T19:10:10"
    SubTotal="7877.79" Moneda="MXN" Total="9138.24" TipoDeComprobante="I"
    MetodoPago="PUE" LugarExpedicion="60950">
  <cfdi:Emisor Rfc="ATL120106DC6" Nombre="APM TERMINALS LAZARO CARDENAS" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="IGJ181107TZ3" Nombre="CLIENTE DE PRUEBA" UsoCFDI="G03" DomicilioFiscalReceptor="06300" RegimenFiscalReceptor="601"/>
  <cfdi:Conceptos>
    <cfdi:Concepto Cantidad="1" ClaveProdServ="78141700" ClaveUnidad="E48" Descripcion="{contenedor}-MUELLAJE 40 HC" ValorUnitario="261.00" Importe="261.00" ObjetoImp="02"/>
    <cfdi:Concepto Cantidad="1" ClaveProdServ="78141804" ClaveUnidad="E48" Descripcion="{contenedor}-CODIGO ISPS" ValorUnitario="163.79" Importe="163.79" ObjetoImp="02"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="1260.45">
    <cfdi:Traslados>
      <cfdi:Traslado Impuesto="002" TipoFactor="Tasa" Base="7877.79" TasaOCuota="0.160000" Importe="1260.45"/>
    </cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="1.1" UUID="{uuid}" FechaTimbrado="2026-07-08T19:10:20"/>
  </cfdi:Complemento>
  <cfdi:Addenda>
    <customized xmlns="http://repository.edicomnet.com/schemas/mx/cfd/addenda">
      <APMTLZC>
        <MANIOBRA>SEGUNDA</MANIOBRA>
        <TRAFICO>IMPORTACION</TRAFICO>
        <PEDIMENTO>{pedimento}</PEDIMENTO>
        <AGENTEADUANAL>{agente}</AGENTEADUANAL>
        <CONOCIMIENTO>{bl}</CONOCIMIENTO>
        <BUQUE>SAN FERNANDO</BUQUE>
        <VIAJE>623E</VIAJE>
      </APMTLZC>
    </customized>
  </cfdi:Addenda>
</cfdi:Comprobante>'''


def cfdi_lct(uuid='11111111-1111-1111-1111-111111111111', patente='1656',
             pedimento='1656-6001126', contenedor='CSNU 879377 0',
             bl='COSU6501186800'):
    return _PLANTILLA_LCT.format(
        uuid=uuid, patente=patente, pedimento=pedimento,
        contenedor=contenedor, bl=bl,
    ).encode('utf-8')


def cfdi_apm(uuid='22222222-2222-2222-2222-222222222222', pedimento='6000517',
             agente='1627/LUIS FELIPE VAZQUEZ DIAZ', contenedor='BEAU4729066',
             bl='HLCUSHA2604CHSA6'):
    return _PLANTILLA_APM.format(
        uuid=uuid, pedimento=pedimento, agente=agente,
        contenedor=contenedor, bl=bl,
    ).encode('utf-8')


_PLANTILLA_CLIENTE = '''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    Version="4.0" Serie="F" Folio="10234" Fecha="2026-07-10T12:00:00"
    SubTotal="5000.00" Moneda="MXN" Total="5800.00" TipoDeComprobante="I"
    MetodoPago="PPD" LugarExpedicion="06600">
  <cfdi:Emisor Rfc="FPA010101AA1" Nombre="FLETES DEL PACIFICO" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="{rfc_receptor}" Nombre="{nombre_receptor}" UsoCFDI="G03" DomicilioFiscalReceptor="06600" RegimenFiscalReceptor="601"/>
  <cfdi:Conceptos>
    <cfdi:Concepto Cantidad="1" ClaveProdServ="78101800" ClaveUnidad="E48" Descripcion="FLETE TERRESTRE" ValorUnitario="5000.00" Importe="5000.00" ObjetoImp="02"/>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="800.00">
    <cfdi:Traslados>
      <cfdi:Traslado Impuesto="002" TipoFactor="Tasa" Base="5000.00" TasaOCuota="0.160000" Importe="800.00"/>
    </cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="1.1" UUID="{uuid}" FechaTimbrado="2026-07-10T12:00:05"/>
  </cfdi:Complemento>
</cfdi:Comprobante>'''


def cfdi_cliente(uuid='33333333-3333-3333-3333-333333333333',
                 rfc_receptor='XAXX010101ABC',
                 nombre_receptor='CLIENTE EJEMPLO'):
    """CFDI de un emisor NO soportado por los extractores (cae a PENDIENTE)."""
    return _PLANTILLA_CLIENTE.format(
        uuid=uuid, rfc_receptor=rfc_receptor, nombre_receptor=nombre_receptor,
    ).encode('utf-8')
