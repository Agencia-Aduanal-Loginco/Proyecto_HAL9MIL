"""Cruce por partida de la pre-forma contra factura, lista de empaque,
certificados NOM y permisos.

Regresa dos listas: `discrepancias` (bloquean el visto bueno) y
`advertencias` (solo avisan). Ver
docs/superpowers/specs/2026-11-23-glosa-automatica-ia-port-design.md (sección 4.4).
"""
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

# Emparejamiento por descripción: tokens comunes / tokens de la descripción más corta.
UMBRAL_EMPAREJE = Decimal('0.6')

# Claves de identificador que exigen certificado NOM en el expediente.
CLAVES_IDENTIFICADOR_NOM = {'NM', 'EN'}

TOLERANCIA_CANTIDAD_PCT = Decimal('0.001')
TOLERANCIA_CANTIDAD_MIN = Decimal('0.01')
TOLERANCIA_IMPORTE_PCT = Decimal('0.01')
TOLERANCIA_IMPORTE_MIN = Decimal('1.00')

_RE_NOM = re.compile(r'NOM-\d{3}-[A-Z0-9]+(?:-[A-Z0-9]+)*')

_PALABRAS_VACIAS = {
    'DE', 'DEL', 'LA', 'LAS', 'EL', 'LOS', 'Y', 'O', 'EN', 'CON', 'PARA', 'POR',
    'SIN', 'UN', 'UNA', 'AL', 'A',
    'THE', 'OF', 'AND', 'OR', 'FOR', 'WITH', 'IN', 'TO', 'ON', 'AN',
}


def _texto_normalizado(texto):
    t = str(texto or '').upper()
    t = ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^A-Z0-9]+', ' ', t).strip()


def tokens_descripcion(texto):
    return {w for w in _texto_normalizado(texto).split() if w not in _PALABRAS_VACIAS}


def similitud_descripcion(a, b):
    ta, tb = tokens_descripcion(a), tokens_descripcion(b)
    if not ta or not tb:
        return Decimal('0')
    return Decimal(len(ta & tb)) / Decimal(min(len(ta), len(tb)))


def _d(valor):
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return None


def _cuadra(v1, v2, pct, minimo):
    return abs(v1 - v2) <= max(minimo, abs(v2) * pct)


def _sec(partida, idx):
    return partida.get('sec') or f'{idx + 1:03d}'


def grupos_descripcion(partidas):
    """Agrupa partidas con la misma descripción normalizada.

    Regresa una lista de tuplas de índices en orden de aparición. Una factura no
    distingue dos partidas con igual descripción (p. ej. mismo producto con
    distinto permiso), así que se comparan juntas.
    """
    grupos = {}
    for i, p in enumerate(partidas):
        clave = frozenset(tokens_descripcion(p.get('descripcion'))) or ('_sin_descripcion', i)
        grupos.setdefault(clave, []).append(i)
    return [tuple(g) for g in grupos.values()]


def emparejar(partidas, renglones):
    """Asigna cada renglón al grupo de partidas con descripción más parecida.

    Regresa ({tupla_de_indices: [renglones]}, [renglones sin partida]).
    Varios renglones pueden caer en un mismo grupo.
    """
    grupos = grupos_descripcion(partidas)
    asignados = {g: [] for g in grupos}
    sin_partida = []
    for r in renglones:
        mejor, mejor_puntaje = None, Decimal('0')
        for g in grupos:
            puntaje = similitud_descripcion(partidas[g[0]].get('descripcion'), r.get('descripcion'))
            if puntaje > mejor_puntaje:
                mejor, mejor_puntaje = g, puntaje
        if mejor is not None and mejor_puntaje >= UMBRAL_EMPAREJE:
            asignados[mejor].append(r)
        else:
            sin_partida.append(r)
    return asignados, sin_partida


def _secs(partidas, grupo):
    secs = [_sec(partidas[i], i) for i in grupo]
    return ('Partida ' if len(secs) == 1 else 'Partidas ') + ', '.join(secs), ','.join(secs)


def _ocurrencia(partidas, i):
    """Cuántas partidas anteriores tienen la misma descripción (para la anotación)."""
    clave = tokens_descripcion(partidas[i].get('descripcion'))
    return sum(1 for p in partidas[:i] if tokens_descripcion(p.get('descripcion')) == clave)


def _renglones(docs):
    renglones = []
    for d in docs:
        for r in (d.datos_extraidos or {}).get('partidas') or []:
            if isinstance(r, dict) and r.get('descripcion'):
                renglones.append({**r, '_documento_id': d.id})
    return renglones


def _suma(renglones, campo):
    """Suma del campo; None si algún renglón no lo trae."""
    total = Decimal('0')
    for r in renglones:
        v = _d(r.get(campo))
        if v is None:
            return None
        total += v
    return total


def _advertencia(codigo, mensaje, campo, documento_id, tipo_documento, encontrado, esperado, en_preforma):
    return {
        "codigo": codigo,
        "severidad": "ADVERTENCIA",
        "mensaje": mensaje,
        "campo": campo,
        "documento_id": documento_id,
        "tipo_documento": tipo_documento,
        "valor_encontrado": encontrado,
        "valor_esperado": esperado,
        "en_preforma": en_preforma,
    }


def _discrepancia(codigo, mensaje, campo, documento_id, tipo_documento, encontrado, esperado, en_preforma,
                  texto_busqueda=None, ocurrencia=None):
    disc = {
        "codigo": codigo,
        "mensaje": mensaje,
        "campo": campo,
        "documento_id": documento_id,
        "tipo_documento": tipo_documento,
        "valor_encontrado": encontrado,
        "valor_esperado": esperado,
        "en_preforma": en_preforma,
    }
    if texto_busqueda:
        # La pre-forma anotada busca este texto en vez de valor_encontrado:
        # una cantidad suelta ("100") aparece en muchos lugares del PDF.
        disc["texto_busqueda"] = texto_busqueda
        if ocurrencia is not None:
            # Qué aparición marcar cuando varias partidas comparten descripción.
            disc["ocurrencia_busqueda"] = ocurrencia
    return disc


def _factor_moneda(partidas, preforma_datos, fac_docs):
    """Pesos de precio pagado por unidad de moneda de factura."""
    pagado = _suma(partidas, 'precio_pagado')
    base = _d(preforma_datos.get('valor_moneda_factura'))
    if base is None and fac_docs:
        base = _suma([d.datos_extraidos or {} for d in fac_docs], 'valor_total')
    if not pagado or not base:
        return None
    return pagado / base


def _cruce_cantidades(partidas, renglones, tipo, preforma_id, discrepancias, advertencias, resumen):
    nombre = 'factura' if tipo == 'FACTURA' else 'lista de empaque'
    asignados, sin_partida = emparejar(partidas, renglones)

    clave_resumen = 'factura' if tipo == 'FACTURA' else 'lista_empaque'
    for grupo_idx, grupo in asignados.items():
        etiqueta, secs = _secs(partidas, grupo_idx)
        p0 = partidas[grupo_idx[0]]
        _marcar_grupo(resumen, partidas, grupo_idx)
        if not grupo:
            for i in grupo_idx:
                resumen[i][clave_resumen] = {'valor': None, 'estado': 'SIN'}
            advertencias.append(_advertencia(
                f"PARTIDA_SIN_{tipo}",
                f"{etiqueta}: no se encontró su descripción en la {nombre}.",
                f"partidas.{secs}.descripcion", preforma_id, "PREFORMA",
                p0.get('descripcion'), f"Renglón en {nombre}", False,
            ))
            continue
        cant_pf = _suma([partidas[i] for i in grupo_idx], 'cantidad_umc')
        cant_doc = _suma(grupo, 'cantidad')
        cuadra = cant_pf is None or cant_doc is None or _cuadra(
            cant_pf, cant_doc, TOLERANCIA_CANTIDAD_PCT, TOLERANCIA_CANTIDAD_MIN
        )
        for i in grupo_idx:
            resumen[i][clave_resumen] = {
                'valor': str(cant_doc) if cant_doc is not None else None,
                'estado': 'OK' if cuadra else 'DISC',
            }
            if len(grupo_idx) > 1 and cant_pf is not None:
                # Suma del grupo en pre-forma, para comparar contra el documento.
                resumen[i][clave_resumen]['preforma'] = str(cant_pf)
        if not cuadra:
            discrepancias.append(_discrepancia(
                f"PARTIDA_CANTIDAD_{tipo}",
                f"{etiqueta}: cantidad {cant_pf} en pre-forma vs {cant_doc} en {nombre}.",
                f"partidas.{secs}.cantidad_umc", preforma_id, "PREFORMA",
                str(cant_pf), str(cant_doc), True, texto_busqueda=p0.get('descripcion'),
            ))

    for r in sin_partida:
        advertencias.append(_advertencia(
            f"RENGLON_{tipo}_SIN_PARTIDA",
            f"Renglón de {nombre} sin partida en pre-forma: {r.get('descripcion')}.",
            "partidas.descripcion", r['_documento_id'], tipo,
            r.get('descripcion'), "Partida en pre-forma", False,
        ))
    return asignados


def _cruce_importes(partidas, asignados_fac, factor, preforma_id, discrepancias, resumen):
    if not factor:
        return
    for grupo_idx, grupo in asignados_fac.items():
        pagado = _suma([partidas[i] for i in grupo_idx], 'precio_pagado')
        importe_fac = _suma(grupo, 'importe')
        if not grupo or pagado is None or importe_fac is None:
            continue
        importe_pf = (pagado / factor).quantize(Decimal('0.01'))
        cuadra = _cuadra(importe_pf, importe_fac, TOLERANCIA_IMPORTE_PCT, TOLERANCIA_IMPORTE_MIN)
        for i in grupo_idx:
            resumen[i]['importe'] = {
                'preforma': str(importe_pf), 'valor': str(importe_fac),
                'estado': 'OK' if cuadra else 'DISC',
            }
        if not cuadra:
            etiqueta, secs = _secs(partidas, grupo_idx)
            discrepancias.append(_discrepancia(
                "PARTIDA_IMPORTE_FACTURA",
                f"{etiqueta}: importe {importe_pf} (moneda de factura) en pre-forma vs {importe_fac} en factura.",
                f"partidas.{secs}.precio_pagado", preforma_id, "PREFORMA",
                str(pagado), str(importe_fac), True,
                texto_busqueda=partidas[grupo_idx[0]].get('descripcion'),
            ))


def _cruce_valor_factura(preforma_datos, fac_docs, preforma_id, discrepancias):
    valor_pf = _d(preforma_datos.get('valor_moneda_factura'))
    valor_fac = _suma([d.datos_extraidos or {} for d in fac_docs], 'valor_total')
    if valor_pf is None or valor_fac is None or not fac_docs:
        return
    if not _cuadra(valor_pf, valor_fac, TOLERANCIA_IMPORTE_PCT, TOLERANCIA_IMPORTE_MIN):
        discrepancias.append(_discrepancia(
            "VALOR_FACTURA",
            f"Valor en moneda de factura {valor_pf} en pre-forma vs {valor_fac} en factura(s).",
            "valor_moneda_factura", preforma_id, "PREFORMA",
            preforma_datos.get('valor_moneda_factura'), str(valor_fac), True,
        ))


def _normas_de(texto):
    return set(_RE_NOM.findall(_texto_normalizado(texto).replace(' ', '-')))


def _norma_base(nom):
    """NOM-003-SCFI-2014 -> NOM-003-SCFI (el año puede no venir)."""
    partes = nom.split('-')
    return '-'.join(partes[:3])


def _cubre_modelo(modelos_doc, modelo):
    if not modelos_doc or not modelo:
        return True
    m = _texto_normalizado(modelo).replace(' ', '')
    return any(_texto_normalizado(x).replace(' ', '') == m for x in modelos_doc)


def _modelos_partida(partida):
    """Modelos de la partida: columna MODELO o, si va vacía, los de OBSERVACIONES A NIVEL PARTIDA."""
    if (partida.get('modelo') or '').strip():
        return [partida['modelo'].strip()]
    return list(partida.get('modelos_observaciones') or [])


def _num(texto):
    return _texto_normalizado(texto).replace(' ', '')


def _es_nom(numero):
    return bool(_normas_de(numero))


def _bloque_permisos(partida):
    """Renglones del bloque CLAVE / NUM. PERMISO O NOM con número."""
    return [x for x in partida.get('permisos') or [] if isinstance(x, dict) and (x.get('numero') or '').strip()]


def _cruce_nom(partidas, cert_docs, preforma_id, discrepancias, resumen):
    for i, p in enumerate(partidas):
        ids_nom = [x for x in p.get('identificadores') or [] if (x.get('clave') or '').upper() in CLAVES_IDENTIFICADOR_NOM]
        noms_bloque = [x for x in _bloque_permisos(p) if _es_nom(x.get('numero'))]
        if not ids_nom and not noms_bloque:
            continue
        sec = _sec(p, i)
        normas = set()
        for x in ids_nom:
            for k in ('complemento1', 'complemento2', 'complemento3'):
                normas |= {_norma_base(n) for n in _normas_de(x.get(k))}
        for x in noms_bloque:
            normas |= {_norma_base(n) for n in _normas_de(x.get('numero'))}
        modelos = _modelos_partida(p)
        modelo = ', '.join(modelos)

        # Certificados de la misma norma; cada modelo de la partida debe estar
        # cubierto por alguno de ellos (un certificado puede no listar modelos).
        certs_norma = []
        for d in cert_docs:
            datos = d.datos_extraidos or {}
            normas_cert = set()
            for n in datos.get('normas') or []:
                normas_cert |= {_norma_base(x) for x in _normas_de(n)}
            if normas and normas_cert and not (normas & normas_cert):
                continue
            certs_norma.append(datos)
        if not modelos:
            cubierto = bool(certs_norma)
        else:
            cubierto = bool(certs_norma) and all(
                any(_cubre_modelo(datos.get('modelos'), m) for datos in certs_norma)
                for m in modelos
            )

        resumen[i]['nom'] = {
            'valor': ', '.join(sorted(normas)) or 'NOM',
            'estado': 'OK' if cubierto else 'DISC',
        }
        if not cubierto:
            origen = sorted({(x.get('clave') or '').upper() for x in ids_nom + noms_bloque})
            requerido = ', '.join(sorted(normas)) or 'certificado NOM'
            detalle = f" modelo {modelo}" if modelo else ""
            discrepancias.append(_discrepancia(
                "PARTIDA_SIN_CERTIFICADO_NOM",
                f"Partida {sec} (clave {', '.join(origen)}) requiere {requerido}{detalle}; no hay certificado en el expediente que la cubra.",
                f"partidas.{sec}.identificadores", preforma_id, "PREFORMA",
                requerido, "Certificado NOM en expediente", True,
                texto_busqueda=p.get('descripcion'), ocurrencia=_ocurrencia(partidas, i),
            ))


def _fecha(valor):
    try:
        return date.fromisoformat(str(valor)[:10])
    except (TypeError, ValueError):
        return None


def _cruce_permisos(partidas, perm_docs, preforma_id, discrepancias, advertencias, resumen):
    """Permisos del bloque CLAVE / NUM. PERMISO O NOM de cada partida."""
    hoy = date.today()
    permisos = {}
    for d in perm_docs:
        num = _num((d.datos_extraidos or {}).get('numero_permiso'))
        if num:
            permisos[num] = d

    declarados = set()
    vencidos_reportados = set()
    for i, p in enumerate(partidas):
        sec = _sec(p, i)
        fraccion = re.sub(r'\D', '', p.get('fraccion') or '')
        numeros_partida, estado_partida = [], 'OK'
        for x in _bloque_permisos(p):
            if _es_nom(x.get('numero')):
                continue  # Lo revisa _cruce_nom.
            numero = x.get('numero')
            num = _num(numero)
            declarados.add(num)
            numeros_partida.append(numero)
            clave = (x.get('clave') or '').upper()
            d = permisos.get(num)
            if d is None:
                discrepancias.append(_discrepancia(
                    "PARTIDA_SIN_PERMISO",
                    f"Partida {sec}: permiso {clave} {numero} declarado sin documento en el expediente.",
                    f"partidas.{sec}.permisos", preforma_id, "PREFORMA",
                    numero, "Permiso en expediente", True,
                    texto_busqueda=p.get('descripcion'), ocurrencia=_ocurrencia(partidas, i),
                ))
                estado_partida = 'DISC'
                continue
            datos = d.datos_extraidos or {}
            vig = _fecha(datos.get('fecha_vigencia'))
            if vig and vig < hoy:
                estado_partida = 'DISC'
            if vig and vig < hoy and num not in vencidos_reportados:
                vencidos_reportados.add(num)
                discrepancias.append(_discrepancia(
                    "PERMISO_VENCIDO",
                    f"Permiso {numero} vencido (vigencia: {vig}).",
                    "fecha_vigencia", d.id, "PERMISOS",
                    str(vig), f">= {hoy}", False,
                ))
            fracciones = {re.sub(r'\D', '', f) for f in datos.get('fracciones') or [] if f}
            if fracciones and fraccion and fraccion not in fracciones:
                estado_partida = 'DISC'
                discrepancias.append(_discrepancia(
                    "PERMISO_FRACCION",
                    f"Partida {sec}: fracción {fraccion} no está amparada por el permiso {numero} ({', '.join(sorted(fracciones))}).",
                    f"partidas.{sec}.fraccion", preforma_id, "PREFORMA",
                    fraccion, ', '.join(sorted(fracciones)), True,
                    texto_busqueda=p.get('descripcion'), ocurrencia=_ocurrencia(partidas, i),
                ))

        if numeros_partida:
            resumen[i]['permiso'] = {'valor': ', '.join(numeros_partida), 'estado': estado_partida}

    # Cada permiso del expediente debe estar declarado en alguna partida.
    for num, d in permisos.items():
        if num not in declarados:
            advertencias.append(_advertencia(
                "PERMISO_NO_DECLARADO",
                f"Permiso {(d.datos_extraidos or {}).get('numero_permiso')} del expediente no aparece en ninguna partida de la pre-forma.",
                "permisos", d.id, "PERMISOS",
                (d.datos_extraidos or {}).get('numero_permiso'), "Declarado en partida", False,
            ))


def _resumen_inicial(partidas):
    return [{
        'sec': _sec(p, i),
        'descripcion': p.get('descripcion') or '',
        'fraccion': p.get('fraccion') or '',
        'modelo': ', '.join(_modelos_partida(p)),
        'cantidad_umc': p.get('cantidad_umc'),
        'grupo': None,
        'factura': None,
        'lista_empaque': None,
        'importe': None,
        'nom': None,
        'permiso': None,
        'estado': 'OK',
    } for i, p in enumerate(partidas)]


def _marcar_grupo(resumen, partidas, grupo_idx):
    if len(grupo_idx) > 1:
        secs = ', '.join(_sec(partidas[i], i) for i in grupo_idx)
        for i in grupo_idx:
            resumen[i]['grupo'] = secs


def _estado_resumen(fila):
    estados = {fila[k]['estado'] for k in ('factura', 'lista_empaque', 'importe', 'nom', 'permiso') if fila[k]}
    if 'DISC' in estados:
        return 'DISCREPANCIA'
    if 'SIN' in estados:
        return 'ADVERTENCIA'
    return 'OK'


def cruzar_partidas(preforma_doc, preforma_datos, documentos):
    """Regresa (discrepancias, advertencias, resumen) del cruce por partida.

    `resumen` trae una fila por partida para la tabla "Cruce por partida"; cada
    celda es None (no aplica) o {'valor', 'estado': OK|DISC|SIN}.
    """
    discrepancias, advertencias = [], []
    partidas = [p for p in preforma_datos.get('partidas') or [] if isinstance(p, dict)]
    if not preforma_doc or not partidas:
        return discrepancias, advertencias, []
    resumen = _resumen_inicial(partidas)

    preforma_id = preforma_doc.id
    fac_docs = [d for d in documentos if d.tipo_documento == 'FACTURA']
    le_docs = [d for d in documentos if d.tipo_documento == 'LISTA_EMPAQUE']
    cert_docs = [d for d in documentos if d.tipo_documento == 'CERTIFICADOS']
    perm_docs = [d for d in documentos if d.tipo_documento == 'PERMISOS']

    _cruce_valor_factura(preforma_datos, fac_docs, preforma_id, discrepancias)

    renglones_fac = _renglones(fac_docs)
    if renglones_fac:
        asignados_fac = _cruce_cantidades(partidas, renglones_fac, 'FACTURA', preforma_id, discrepancias, advertencias, resumen)
        factor = _factor_moneda(partidas, preforma_datos, fac_docs)
        _cruce_importes(partidas, asignados_fac, factor, preforma_id, discrepancias, resumen)

    renglones_le = _renglones(le_docs)
    if renglones_le:
        _cruce_cantidades(partidas, renglones_le, 'LISTA_EMPAQUE', preforma_id, discrepancias, advertencias, resumen)

    _cruce_nom(partidas, cert_docs, preforma_id, discrepancias, resumen)
    _cruce_permisos(partidas, perm_docs, preforma_id, discrepancias, advertencias, resumen)
    for fila in resumen:
        fila['estado'] = _estado_resumen(fila)
    return discrepancias, advertencias, resumen
