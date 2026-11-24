import pymupdf as fitz
from django.conf import settings


def encabezado_agencia(referencia):
    """Nombre de la agencia (settings.GLOSA_NOMBRE_AGENCIA) y patente de la referencia."""
    agencia = getattr(settings, 'GLOSA_NOMBRE_AGENCIA', '') or 'Loginco'
    patente = (getattr(referencia, 'patente', '') or '').strip()
    return f"{agencia} - Patente {patente}" if patente else agencia


def generar_preforma_anotada(pdf_bytes, discrepancias, referencia):
    """
    Toma los bytes de un PDF de pre-forma, antepone una página de resumen de observaciones
    y marca con rectángulos rojos y el número de observación las discrepancias que tienen en_preforma=True
    y valor_encontrado. Retorna los bytes del PDF anotado.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # 1. Crear página de resumen de observaciones al inicio (página 0)
    doc.insert_page(0, width=595, height=842) # Formato A4
    page_summary = doc[0]

    summary_text = f"REPORTE DE DISCREPANCIAS Y OBSERVACIONES\n"
    summary_text += f"Referencia: {referencia.num_refe}\n"
    summary_text += f"{encabezado_agencia(referencia)}\n"
    summary_text += f"-" * 55 + "\n\n"

    disc_en_preforma = [d for d in discrepancias if d.get('en_preforma', False)]

    if disc_en_preforma:
        summary_text += f"Se detectaron {len(disc_en_preforma)} observaciones en pre-forma:\n\n"
        for idx, disc in enumerate(disc_en_preforma, 1):
            codigo = disc.get('codigo', 'DISC')
            mensaje = disc.get('mensaje', 'Sin descripcion')
            campo = disc.get('campo', '')
            val_enc = disc.get('valor_encontrado', '')
            val_esp = disc.get('valor_esperado', '')

            summary_text += f"{idx}. [{codigo}] {mensaje}\n"
            if campo:
                summary_text += f"   - Campo: {campo}\n"
            if val_enc:
                summary_text += f"   - Valor encontrado: {val_enc}\n"
            if val_esp:
                summary_text += f"   - Valor esperado en documento: {val_esp}\n"
            summary_text += "\n"
    else:
        summary_text += "No se registran discrepancias aplicables a pre-forma.\n"

    page_summary.insert_textbox(
        fitz.Rect(40, 40, 555, 800),
        summary_text,
        fontsize=9,
        color=(0, 0, 0)
    )

    # 2. Anotar el documento original (ahora a partir de la página 1 en adelante).
    # Solo se dibuja el cuadro y el número de la observación (rojo, negrita) a
    # la derecha, para no tapar los datos. El detalle va en la página resumen.
    marcas = {}  # (página, rect) -> números de observación
    for idx, disc in enumerate(disc_en_preforma, 1):
        val_enc = str(disc.get('texto_busqueda') or disc.get('valor_encontrado') or '').strip()
        if not val_enc:
            continue

        hallazgos = []
        for page_num in range(1, len(doc)):
            for rect in doc[page_num].search_for(val_enc):
                hallazgos.append((page_num, rect))

        # Si varias partidas comparten descripción, marcar solo la que toca.
        ocurrencia = disc.get('ocurrencia_busqueda')
        if isinstance(ocurrencia, int) and 0 <= ocurrencia < len(hallazgos):
            hallazgos = [hallazgos[ocurrencia]]

        for page_num, rect in hallazgos:
            clave = (page_num, tuple(round(v, 1) for v in rect))
            marcas.setdefault(clave, (rect, []))[1].append(str(idx))

    for (page_num, _), (rect, numeros) in marcas.items():
        page = doc[page_num]
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(color=(1, 0, 0), width=1.5)
        shape.commit()

        etiqueta = ','.join(numeros)
        fontsize = 9
        ancho = fitz.get_text_length(etiqueta, fontname='hebo', fontsize=fontsize)
        x = rect.x1 + 3
        if x + ancho > page.rect.width - 2:
            x = max(2, rect.x0 - ancho - 3)
        y = rect.y0 + (rect.height + fontsize * 0.7) / 2
        page.insert_text(fitz.Point(x, y), etiqueta, fontsize=fontsize, fontname='hebo', color=(1, 0, 0))

    annotated_bytes = doc.tobytes()
    doc.close()
    return annotated_bytes
