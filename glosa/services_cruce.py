import os
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from .services_extraccion import normalizar_contenedor, contenedor_valido
from .services_cruce_partidas import cruzar_partidas

def normalizar_texto(texto):
    if not texto:
        return ''
    return str(texto).strip().upper()

def normalizar_nombre(texto):
    if not texto:
        return ''
    t = str(texto).upper()
    t = ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')
    t = re.sub(r'[^A-Z0-9\s]', '', t)
    suffixes = {'SA', 'CV', 'LTD', 'CO', 'INC', 'LLC', 'SRL', 'DE', 'LA'}
    words = [w for w in t.split() if w not in suffixes]
    return ' '.join(words)

def distancia_edicion(s1, s2):
    s1 = str(s1)
    s2 = str(s2)
    if len(s1) < len(s2):
        return distancia_edicion(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

def comparar_numerico(v1, v2, tolerancia_pct=Decimal('0.005'), min_absoluto=Decimal('1.0')):
    if v1 is None or v2 is None:
        return False
    try:
        d1 = Decimal(str(v1))
        d2 = Decimal(str(v2))
    except (InvalidOperation, ValueError):
        return False
    diff = abs(d1 - d2)
    tol = max(min_absoluto, d1 * tolerancia_pct)
    return diff <= tol

def comparar_valor(v1, v2, tolerancia=Decimal('0.01')):
    if v1 is None or v2 is None:
        return False
    try:
        d1 = Decimal(str(v1))
        d2 = Decimal(str(v2))
    except (InvalidOperation, ValueError):
        return False
    return abs(d1 - d2) <= tolerancia

def ejecutar_cruce(ref):
    documentos = list(ref.documentos_glosa.filter(activo=True))
    preforma_doc = ref.documentos_glosa.filter(activo=True, tipo_documento='PREFORMA').order_by('-creado_en').first()
    preforma_datos = preforma_doc.datos_extraidos if preforma_doc and preforma_doc.datos_extraidos else {}

    # 1. Regla bloqueante: referencia y pedimento
    if preforma_doc and preforma_datos:
        p_refe = normalizar_texto(preforma_datos.get('num_refe'))
        r_refe = normalizar_texto(ref.num_refe)
        p_ped = normalizar_texto(preforma_datos.get('num_pedimento'))
        if p_ped and len(p_ped) >= 7:
            p_ped = p_ped[-7:]
        r_ped = normalizar_texto(ref.num_pedimento)
        if r_ped and len(r_ped) >= 7:
            r_ped = r_ped[-7:]

        refe_no_coincide = p_refe and r_refe and p_refe != r_refe
        ped_no_coincide = p_ped and r_ped and p_ped != r_ped

        if refe_no_coincide or ped_no_coincide:
            msg = f"Esta pre-forma pertenece a {preforma_datos.get('num_refe')}, no a {ref.num_refe}."
            return {
                "version": 2,
                "estado": "DISCREPANCIAS_DETECTADAS",
                "preforma_id": preforma_doc.id,
                "bloqueante": {
                    "tipo": "REFERENCIA_NO_COINCIDE",
                    "preforma": f"{preforma_datos.get('num_refe', '')} / {preforma_datos.get('num_pedimento', '')}",
                    "referencia": f"{ref.num_refe} / {ref.num_pedimento or ''}"
                },
                "subdivision": {"detectada": False, "pedimento_relacionado": None},
                "reglas": [],
                "advertencias": [],
                "discrepancias": [{
                    "codigo": "REFERENCIA_NO_COINCIDE",
                    "mensaje": msg,
                    "campo": "num_refe",
                    "documento_id": preforma_doc.id,
                    "tipo_documento": "PREFORMA",
                    "valor_encontrado": preforma_datos.get('num_refe'),
                    "valor_esperado": ref.num_refe,
                    "en_preforma": True
                }]
            }

    subdivision = preforma_datos.get('subdivision', {'detectada': False, 'pedimento_relacionado': None})

    # Recopilar fuentes por tipo de documento / CASA
    bl_docs = [d for d in documentos if d.tipo_documento == 'BL']
    fac_docs = [d for d in documentos if d.tipo_documento == 'FACTURA']
    le_docs = [d for d in documentos if d.tipo_documento == 'LISTA_EMPAQUE']
    supp_docs = [d for d in documentos if d.tipo_documento in ('CARTA_TECNICA', 'CERTIFICADOS', 'PERMISOS', 'OTROS')]

    # Voz declarada (PREFORMA o CASA como respaldo, usando ref.peso_kg para toneladas -> kg)
    declarada_fuente = "PREFORMA" if preforma_doc else "CASA"
    declarada_doc_id = preforma_doc.id if preforma_doc else None

    reglas = []
    discrepancias = []
    advertencias = []
    hay_error_lectura = False
    hay_discrepancia = False

    # ----------------------------------------------------
    # Campo 1: contenedores
    # ----------------------------------------------------
    fuentes_cont = []
    cont_decl = preforma_datos.get('contenedores', [])
    if not cont_decl and not preforma_doc:
        cont_decl = [c.num_cont for c in ref.contenedores.all()]
    if cont_decl:
        fuentes_cont.append({"fuente": declarada_fuente, "documento_id": declarada_doc_id, "valores": [normalizar_contenedor(c) for c in cont_decl]})

    for d in bl_docs:
        d_conts = d.datos_extraidos.get('contenedores', [])
        vals = []
        for c in d_conts:
            num = c.get('numero') if isinstance(c, dict) else c
            if num:
                vals.append(normalizar_contenedor(num))
        if vals:
            fuentes_cont.append({"fuente": "BL", "documento_id": d.id, "valores": vals})

    for d in le_docs:
        d_conts = d.datos_extraidos.get('contenedores', [])
        vals = []
        for c in d_conts:
            num = c.get('numero') if isinstance(c, dict) else c
            if num:
                vals.append(normalizar_contenedor(num))
        if vals:
            fuentes_cont.append({"fuente": "LISTA_EMPAQUE", "documento_id": d.id, "valores": vals})

    for d in supp_docs:
        d_conts = d.datos_extraidos.get('contenedores', [])
        vals = []
        for c in d_conts:
            num = c.get('numero') if isinstance(c, dict) else c
            if num:
                vals.append(normalizar_contenedor(num))
        if vals:
            fuentes_cont.append({"fuente": d.tipo_documento, "documento_id": d.id, "valores": vals})

    if fuentes_cont:
        all_conts = set()
        for f in fuentes_cont:
            for v in f['valores']:
                all_conts.add(v)

        if all_conts:
            detalles_fuentes = []
            cont_estado = "OK"
            cont_mensaje = ""

            consenso_conts = set()
            conts_counts = {}
            for f in fuentes_cont:
                for v in f['valores']:
                    if contenedor_valido(v):
                        conts_counts[v] = conts_counts.get(v, 0) + 1

            if conts_counts:
                max_count = max(conts_counts.values())
                consenso_conts = {k for k, v in conts_counts.items() if v == max_count}

            val_consenso_str = ", ".join(sorted(consenso_conts)) if consenso_conts else list(all_conts)[0]

            for f in fuentes_cont:
                coincide = all(c in consenso_conts for c in f['valores']) if consenso_conts else True
                valido = all(contenedor_valido(c) for c in f['valores'])

                if not valido or not coincide:
                    if f['fuente'] in ('BL', 'LISTA_EMPAQUE') and len(fuentes_cont) >= 3:
                        es_error_lectura = False
                        for vc in consenso_conts:
                            for vc_src in f['valores']:
                                if not contenedor_valido(vc_src) or distancia_edicion(vc_src, vc) <= 2:
                                    es_error_lectura = True
                        if es_error_lectura:
                            hay_error_lectura = True
                            cont_estado = "ERROR_LECTURA"
                            cont_mensaje = f"El {f['fuente']} parece mal leído: difiere del consenso y/o no pasa validación ISO 6346."
                    else:
                        if cont_estado != "ERROR_LECTURA":
                            cont_estado = "DISCREPANCIA"
                            hay_discrepancia = True
                            msg = "Discrepancia en contenedores entre fuentes."
                            discrepancias.append({
                                "codigo": "CONTENEDORES",
                                "mensaje": msg,
                                "campo": "contenedores",
                                "documento_id": f['documento_id'],
                                "tipo_documento": f['fuente'],
                                "valor_encontrado": ", ".join(f['valores']),
                                "valor_esperado": val_consenso_str,
                                "en_preforma": (f['fuente'] == 'PREFORMA')
                            })

                detalles_fuentes.append({
                    "fuente": f['fuente'],
                    "documento_id": f['documento_id'],
                    "valor": ", ".join(f['valores']),
                    "coincide": coincide,
                    "valido": valido
                })

            reglas.append({
                "campo": "contenedores",
                "estado": cont_estado,
                "valor_consenso": val_consenso_str,
                "fuentes": detalles_fuentes,
                "mensaje": cont_mensaje
            })
    else:
        reglas.append({
            "campo": "contenedores",
            "estado": "FALTA_DATO",
            "valor_consenso": "",
            "fuentes": [],
            "mensaje": "No se encontraron contenedores en las fuentes."
        })

    # ----------------------------------------------------
    # Campo 2: peso_bruto_kg (usando ref.peso_kg si no hay preforma)
    # ----------------------------------------------------
    fuentes_peso = []
    peso_decl = preforma_datos.get('peso_bruto_kg')
    if not peso_decl and not preforma_doc:
        peso_decl = ref.peso_kg
    if peso_decl is not None:
        fuentes_peso.append({"fuente": declarada_fuente, "documento_id": declarada_doc_id, "valor": Decimal(str(peso_decl))})

    for d in bl_docs:
        p = d.datos_extraidos.get('peso_bruto_kg')
        if p is not None:
            fuentes_peso.append({"fuente": "BL", "documento_id": d.id, "valor": Decimal(str(p))})

    for d in le_docs:
        p = d.datos_extraidos.get('peso_bruto_kg')
        if p not in (None, ''):
            fuentes_peso.append({"fuente": "LISTA_EMPAQUE", "documento_id": d.id, "valor": Decimal(str(p))})

    if fuentes_peso:
        val_ref = fuentes_peso[0]['valor']
        peso_estado = "OK"
        peso_mensaje = ""
        detalles_fuentes = []
        for f in fuentes_peso:
            coincide = comparar_numerico(f['valor'], val_ref)
            valido = f['valor'] > 0
            if not coincide:
                if f['fuente'] == 'BL' and subdivision.get('detectada'):
                    if peso_estado == "OK":
                        peso_estado = "REVISAR_SUBDIVISION"
                        peso_mensaje = (
                            f"El BL ampara {f['valor']} kg del embarque completo; "
                            f"verificar contra la lista de empaque por subdivisión."
                        )
                else:
                    peso_estado = "DISCREPANCIA"
                    peso_mensaje = f"{f['fuente']} declara {f['valor']} kg vs {val_ref} kg."
                    hay_discrepancia = True
                    discrepancias.append({
                        "codigo": "PESO_BRUTO",
                        "mensaje": peso_mensaje,
                        "campo": "peso_bruto_kg",
                        "documento_id": f['documento_id'],
                        "tipo_documento": f['fuente'],
                        "valor_encontrado": str(f['valor']),
                        "valor_esperado": str(val_ref),
                        "en_preforma": (f['fuente'] == 'PREFORMA')
                    })
            detalles_fuentes.append({
                "fuente": f['fuente'],
                "documento_id": f['documento_id'],
                "valor": str(f['valor']),
                "coincide": coincide,
                "valido": valido
            })
        reglas.append({
            "campo": "peso_bruto_kg",
            "estado": peso_estado,
            "valor_consenso": str(val_ref),
            "fuentes": detalles_fuentes,
            "mensaje": peso_mensaje
        })
    else:
        reglas.append({
            "campo": "peso_bruto_kg",
            "estado": "FALTA_DATO",
            "valor_consenso": "",
            "fuentes": [],
            "mensaje": "Falta peso bruto en las fuentes."
        })

    # ----------------------------------------------------
    # Campo 3: proveedor
    # ----------------------------------------------------
    fuentes_prov = []
    prov_decl = preforma_datos.get('proveedor_nombre')
    if prov_decl:
        fuentes_prov.append({"fuente": declarada_fuente, "documento_id": declarada_doc_id, "valor": prov_decl, "norm": normalizar_nombre(prov_decl)})

    for d in fac_docs + supp_docs:
        prov = d.datos_extraidos.get('proveedor') or d.datos_extraidos.get('proveedor_nombre')
        if prov:
            fuentes_prov.append({"fuente": d.tipo_documento, "documento_id": d.id, "valor": prov, "norm": normalizar_nombre(prov)})

    if fuentes_prov:
        val_ref_norm = fuentes_prov[0]['norm']
        val_ref_orig = fuentes_prov[0]['valor']
        prov_estado = "OK"
        detalles_fuentes = []
        for f in fuentes_prov:
            coincide = (f['norm'] == val_ref_norm) or (val_ref_norm in f['norm'] or f['norm'] in val_ref_norm)
            if not coincide:
                prov_estado = "DISCREPANCIA"
                hay_discrepancia = True
                msg = f"Discrepancia en proveedor ({f['fuente']}: {f['valor']} vs {val_ref_orig})."
                discrepancias.append({
                    "codigo": "PROVEEDOR",
                    "mensaje": msg,
                    "campo": "proveedor",
                    "documento_id": f['documento_id'],
                    "tipo_documento": f['fuente'],
                    "valor_encontrado": f['valor'],
                    "valor_esperado": val_ref_orig,
                    "en_preforma": (f['fuente'] == 'PREFORMA')
                })
            detalles_fuentes.append({
                "fuente": f['fuente'],
                "documento_id": f['documento_id'],
                "valor": f['valor'],
                "coincide": coincide,
                "valido": True
            })
        reglas.append({
            "campo": "proveedor",
            "estado": prov_estado,
            "valor_consenso": val_ref_orig,
            "fuentes": detalles_fuentes,
            "mensaje": ""
        })

    # ----------------------------------------------------
    # Reglas ED (Electronic Documents)
    # ----------------------------------------------------
    if preforma_doc:
        eds_declarados = set(preforma_datos.get('documentos_ed', []))
        eds_expediente = set()
        ed_regex = re.compile(r'^[A-Z0-9]{13}$')

        for d in documentos:
            fn = os.path.splitext(os.path.basename(d.archivo.name))[0].upper()
            if ed_regex.match(fn):
                eds_expediente.add(fn)

        for ed in eds_declarados:
            if ed not in eds_expediente:
                advertencias.append({
                    "codigo": "ED_SIN_DOCUMENTO",
                    "severidad": "ADVERTENCIA",
                    "mensaje": f"ED declarado sin documento en el expediente: {ed}",
                    "campo": "documentos_ed",
                    "documento_id": preforma_doc.id if preforma_doc else None,
                    "tipo_documento": "PREFORMA",
                    "valor_encontrado": ed,
                    "valor_esperado": "Presente en expediente",
                    "en_preforma": True
                })

        for ed in eds_expediente:
            if ed not in eds_declarados:
                ed_doc = next((d for d in documentos if os.path.splitext(os.path.basename(d.archivo.name))[0].upper() == ed), None)
                advertencias.append({
                    "codigo": "ED_NO_DECLARADO",
                    "severidad": "ADVERTENCIA",
                    "mensaje": f"Documento en expediente no declarado en el pedimento: {ed}",
                    "campo": "documentos_ed",
                    "documento_id": ed_doc.id if ed_doc else None,
                    "tipo_documento": ed_doc.tipo_documento if ed_doc else "EXPEDIENTE",
                    "valor_encontrado": ed,
                    "valor_esperado": "Declarado en pre-forma",
                    "en_preforma": False
                })

    # ----------------------------------------------------
    # Validación de Certificados
    # ----------------------------------------------------
    cert_docs = [d for d in documentos if d.tipo_documento == 'CERTIFICADOS']
    hoy = date.today()

    for d in cert_docs:
        c_data = d.datos_extraidos or {}
        num_cert = c_data.get('numero_certificado')
        f_vig = c_data.get('fecha_vigencia')

        if f_vig:
            try:
                fv_date = date.fromisoformat(f_vig)
                if fv_date < hoy:
                    hay_discrepancia = True
                    discrepancias.append({
                        "codigo": "CERTIFICADO_VENCIDO",
                        "mensaje": f"Certificado {num_cert or d.id} vencido (vigencia: {f_vig}).",
                        "campo": "fecha_vigencia",
                        "documento_id": d.id,
                        "tipo_documento": "CERTIFICADOS",
                        "valor_encontrado": f_vig,
                        "valor_esperado": f">= {hoy}",
                        "en_preforma": False
                    })
            except ValueError:
                pass
        else:
            hay_discrepancia = True
            discrepancias.append({
                "codigo": "CERTIFICADO_SIN_VIGENCIA",
                "mensaje": f"Certificado {num_cert or d.id} sin fecha de vigencia.",
                "campo": "fecha_vigencia",
                "documento_id": d.id,
                "tipo_documento": "CERTIFICADOS",
                "valor_encontrado": None,
                "valor_esperado": "Fecha de vigencia válida",
                "en_preforma": False
            })

        if preforma_doc and num_cert:
            certs_declarados = preforma_datos.get('certificados') or []
            obs_preforma = preforma_datos.get('observaciones', '')
            declarado = any(num_cert.upper() in str(c).upper() for c in certs_declarados) or (num_cert.upper() in obs_preforma.upper())
            if not declarado:
                hay_discrepancia = True
                discrepancias.append({
                    "codigo": "CERTIFICADO_NO_DECLARADO",
                    "mensaje": f"Certificado {num_cert} no declarado en pre-forma.",
                    "campo": "certificados",
                    "documento_id": d.id,
                    "tipo_documento": "CERTIFICADOS",
                    "valor_encontrado": num_cert,
                    "valor_esperado": "Declarado en pre-forma",
                    "en_preforma": True
                })

    # ----------------------------------------------------
    # Datos de CASA (Loginco): guía/B/L, contenedores y número de partidas
    # ----------------------------------------------------
    hay_discrepancia = _cruce_casa(ref, preforma_doc, preforma_datos, bl_docs,
                                   reglas, discrepancias, advertencias) or hay_discrepancia

    # ----------------------------------------------------
    # Cruce por partida
    # ----------------------------------------------------
    disc_partidas, adv_partidas, resumen_partidas = cruzar_partidas(preforma_doc, preforma_datos, documentos)
    if disc_partidas:
        hay_discrepancia = True
        discrepancias.extend(disc_partidas)
    advertencias.extend(adv_partidas)

    if not preforma_doc or not documentos or hay_error_lectura:
        estado_global = "PENDIENTE"
    elif hay_discrepancia:
        estado_global = "DISCREPANCIAS_DETECTADAS"
    else:
        estado_global = "EN_REVISION"

    return {
        "version": 2,
        "estado": estado_global,
        "preforma_id": preforma_doc.id if preforma_doc else None,
        "bloqueante": None,
        "subdivision": subdivision,
        "reglas": reglas,
        "discrepancias": discrepancias,
        "advertencias": advertencias,
        "partidas": resumen_partidas
    }


def _norm_guia(valor):
    return re.sub(r'[^A-Z0-9]', '', str(valor or '').upper())


def _misma_guia(a, b):
    """Iguales, o una termina con la otra (algunos B/L omiten el prefijo SCAC)."""
    if not a or not b:
        return False
    if a == b:
        return True
    corto, largo = sorted((a, b), key=len)
    return len(corto) >= 6 and largo.endswith(corto)


def _cruce_casa(ref, preforma_doc, preforma_datos, bl_docs, reglas, discrepancias, advertencias):
    """Reglas contra los datos sincronizados de CASA. Regresa True si hubo discrepancia.

    - guia_bl: el número de B/L leído debe ser una guía declarada (pre-forma o,
      sin pre-forma, SAAIO_GUIAS). Diferencia = discrepancia GUIA_BL.
    - Contenedores y guías de la pre-forma vs CASA: advertencias (CASA puede
      estar desfasado si el sync no ha corrido).
    - Número de partidas de la pre-forma leída vs Referencia.num_partidas:
      advertencia; suele indicar que la extracción omitió partidas.
    """
    hubo_discrepancia = False
    preforma_id = preforma_doc.id if preforma_doc else None

    guias_pf = [g for g in (_norm_guia(x) for x in preforma_datos.get('guias') or []) if g]
    guias_casa = [g for g in (_norm_guia(x.numero_guia) for x in ref.guias.all()) if g]
    declaradas, fuente_decl = (guias_pf, 'PREFORMA') if guias_pf else (guias_casa, 'CASA')

    numeros_bl = []
    for d in bl_docs:
        num = _norm_guia((d.datos_extraidos or {}).get('numero_bl'))
        if num:
            numeros_bl.append((d, num))

    if declaradas and numeros_bl:
        fuentes = [{
            "fuente": fuente_decl, "documento_id": preforma_id if fuente_decl == 'PREFORMA' else None,
            "valor": ", ".join(declaradas), "coincide": True, "valido": True,
        }]
        estado = "OK"
        for d, num in numeros_bl:
            coincide = any(_misma_guia(num, g) for g in declaradas)
            fuentes.append({"fuente": "BL", "documento_id": d.id, "valor": num, "coincide": coincide, "valido": True})
            if not coincide:
                estado = "DISCREPANCIA"
                hubo_discrepancia = True
                discrepancias.append({
                    "codigo": "GUIA_BL",
                    "mensaje": f"El B/L {num} no corresponde a la guía declarada ({', '.join(declaradas)}).",
                    "campo": "guias",
                    "documento_id": preforma_id if fuente_decl == 'PREFORMA' else d.id,
                    "tipo_documento": fuente_decl if fuente_decl == 'PREFORMA' else "BL",
                    "valor_encontrado": declaradas[0],
                    "valor_esperado": num,
                    "en_preforma": fuente_decl == 'PREFORMA',
                })
        reglas.append({
            "campo": "guia_bl", "estado": estado, "valor_consenso": ", ".join(declaradas),
            "fuentes": fuentes, "mensaje": "" if estado == "OK" else "Guía declarada distinta al B/L.",
        })
    elif bl_docs or declaradas:
        reglas.append({
            "campo": "guia_bl", "estado": "FALTA_DATO", "valor_consenso": ", ".join(declaradas),
            "fuentes": [], "mensaje": "Falta el número de B/L o la guía declarada para cruzar.",
        })

    if not preforma_doc:
        return hubo_discrepancia

    if guias_pf and guias_casa and not all(any(_misma_guia(g, c) for c in guias_casa) for g in guias_pf):
        advertencias.append({
            "codigo": "CASA_GUIAS", "severidad": "ADVERTENCIA",
            "mensaje": f"Guías de la pre-forma ({', '.join(guias_pf)}) distintas a las de CASA ({', '.join(guias_casa)}).",
            "campo": "guias", "documento_id": preforma_id, "tipo_documento": "PREFORMA",
            "valor_encontrado": ", ".join(guias_pf), "valor_esperado": ", ".join(guias_casa), "en_preforma": True,
        })

    conts_pf = {normalizar_contenedor(c) for c in preforma_datos.get('contenedores') or [] if c}
    conts_casa = {normalizar_contenedor(c.num_cont) for c in ref.contenedores.all() if c.num_cont}
    if conts_pf and conts_casa and conts_pf != conts_casa:
        advertencias.append({
            "codigo": "CASA_CONTENEDORES", "severidad": "ADVERTENCIA",
            "mensaje": (f"Contenedores de la pre-forma ({', '.join(sorted(conts_pf))}) distintos a los de "
                        f"CASA ({', '.join(sorted(conts_casa))}). Revise si CASA está actualizado."),
            "campo": "contenedores", "documento_id": preforma_id, "tipo_documento": "PREFORMA",
            "valor_encontrado": ", ".join(sorted(conts_pf)), "valor_esperado": ", ".join(sorted(conts_casa)),
            "en_preforma": True,
        })

    partidas = preforma_datos.get('partidas') or []
    if ref.num_partidas and partidas and len(partidas) != ref.num_partidas:
        advertencias.append({
            "codigo": "NUM_PARTIDAS", "severidad": "ADVERTENCIA",
            "mensaje": (f"Se leyeron {len(partidas)} partidas de la pre-forma y CASA registra "
                        f"{ref.num_partidas}. Revise si la lectura omitió partidas."),
            "campo": "partidas", "documento_id": preforma_id, "tipo_documento": "PREFORMA",
            "valor_encontrado": str(len(partidas)), "valor_esperado": str(ref.num_partidas),
            "en_preforma": False,
        })
    return hubo_discrepancia
