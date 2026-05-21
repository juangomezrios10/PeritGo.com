from flask import Flask, render_template, request, send_file, jsonify
import json
import io
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm, cm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                  Spacer, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas as rl_canvas
from pypdf import PdfWriter, PdfReader

app = Flask(__name__)

# ─── Colores de marca ───────────────────────────────────────────────────────
AZUL        = colors.HexColor('#1a73c7')
AZUL_CLARO  = colors.HexColor('#e8f1fb')
GRIS_TABLA  = colors.HexColor('#f5f5f5')
GRIS_HEADER = colors.HexColor('#4a4a4a')
VERDE       = colors.HexColor('#27ae60')
ROJO        = colors.HexColor('#e74c3c')
NARANJA     = colors.HexColor('#e67e22')
BLANCO      = colors.white
NEGRO       = colors.black


# ─── Estilos ────────────────────────────────────────────────────────────────
def get_styles():
    styles = getSampleStyleSheet()
    custom = {
        'titulo_seccion': ParagraphStyle('titulo_seccion', fontSize=9, fontName='Helvetica-Bold',
                                          textColor=BLANCO, alignment=TA_CENTER, spaceAfter=0),
        'celda_label': ParagraphStyle('celda_label', fontSize=7.5, fontName='Helvetica',
                                       textColor=GRIS_HEADER, leading=10),
        'celda_valor': ParagraphStyle('celda_valor', fontSize=8, fontName='Helvetica-Bold',
                                       textColor=NEGRO, leading=10),
        'normal_sm': ParagraphStyle('normal_sm', fontSize=7.5, fontName='Helvetica',
                                     textColor=NEGRO, leading=10),
        'obs_label': ParagraphStyle('obs_label', fontSize=7.5, fontName='Helvetica-Bold',
                                     textColor=BLANCO, alignment=TA_CENTER),
        'obs_aviso': ParagraphStyle('obs_aviso', fontSize=7.5, fontName='Helvetica-Bold',
                                     textColor=NEGRO, alignment=TA_CENTER, backColor=colors.HexColor('#f0c040')),
        'obs_inmediato': ParagraphStyle('obs_inmediato', fontSize=7.5, fontName='Helvetica-Bold',
                                         textColor=BLANCO, alignment=TA_CENTER, backColor=ROJO),
        'obs_pendiente': ParagraphStyle('obs_pendiente', fontSize=7.5, fontName='Helvetica-Bold',
                                         textColor=BLANCO, alignment=TA_CENTER, backColor=AZUL),
        'calificacion': ParagraphStyle('calificacion', fontSize=28, fontName='Helvetica-Bold',
                                        textColor=AZUL, alignment=TA_CENTER),
        'pie': ParagraphStyle('pie', fontSize=5.5, fontName='Helvetica', textColor=colors.grey,
                               leading=7),
    }
    return custom


# ─── Helper: encabezado de sección ──────────────────────────────────────────
def seccion_header(titulo, styles):
    return Table(
        [[Paragraph(titulo, styles['titulo_seccion'])]],
        colWidths=[185*mm],
        style=TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), AZUL),
            ('ROWPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ])
    )


def check_box(value, styles):
    """Render X or empty."""
    return 'X' if value else ''


def calidad_bar(pct, label):
    """Color badge for section result."""
    pct = int(pct or 0)
    if pct >= 90:
        bg = VERDE
    elif pct >= 70:
        bg = NARANJA
    else:
        bg = ROJO
    return pct


# ─── Generador PDF ──────────────────────────────────────────────────────────
def generar_pdf(datos):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=14*mm, bottomMargin=14*mm
    )
    styles = get_styles()
    story = []
    W = 185*mm  # ancho útil

    # ── Cálculo automático de puntajes por sección ───────────────────────────
    PESOS_SECCION = {
        'motor':    20,
        'electrico': 10,
        'fluidos':   10,
        'llantas':   10,
        'tren':      20,
        'interior':  10,
        'ruta':      20,
    }
    ITEMS_DEF_BACK = {
        'motor':    ['arranque','radiador','carter_motor','carter_caja','caja_vel','soporte_caja',
                     'soporte_motor','ruido_motor','ventiladores','mangueras','embrague',
                     'sincronizacion','correa_rep','correa_acc','polea_tensores'],
        'electrico':['panoramico_del','panoramico_tra','vidrios','plumillas','farola_der',
                     'farola_izq','explor_der','explor_izq','stop_der','stop_izq','bocina',
                     'bateria','fusibles','alternador','luz_placa'],
        'fluidos':  ['aceite_motor','aceite_caja','refrigerante','liq_freno','dir_hidraulica','diferencial'],
        'llantas':  ['del','tra','repuesto','desgaste','rines'],
        'tren':     ['pastillas','discos','amort_del','amort_tra','puntas','axiales',
                     'terminales','rotulas','bujes','tijeras','caja_dir','rodamientos'],
        'interior': ['calefaccion','aire_ac','cinturones','asiento_der','asiento_izq',
                     'asientos_tra','techo','manija_techo','luz_techo','carteras',
                     'millare','alfombras','cabeceros','tapetes'],
        'ruta':     ['aceleracion','maniobrabilidad','alineacion','frenado','embrague',
                     'relacion_caja','vibraciones'],
    }
    VALOR_ESTADO = {'B': 100, 'R': 50, 'M': 0}

    suma_ponderada = 0
    suma_pesos = 0
    for sec_key, items in ITEMS_DEF_BACK.items():
        vals = []
        for key in items:
            v = datos.get(f'{sec_key}_{key}', 'B')
            if v != 'NA':
                vals.append(VALOR_ESTADO.get(v, 100))
        pct_sec = round(sum(vals) / len(vals)) if vals else 100
        datos[f'pct_{sec_key}'] = pct_sec
        peso = PESOS_SECCION.get(sec_key, 10)
        suma_ponderada += pct_sec * peso
        suma_pesos += peso

    datos['calificacion_total'] = round(suma_ponderada / suma_pesos) if suma_pesos else 100

    # ── Encabezado ───────────────────────────────────────────────────────────
    fecha = datos.get('fecha', datetime.now().strftime('%d/%m/%Y'))
    no_servicio = datos.get('no_servicio', '---')
    placa = datos.get('placa', '---').upper()

    # Recuadro de placa con diseño destacado
    placa_table = Table([
        [Paragraph('PLACA', ParagraphStyle('placa_lbl', fontSize=7, fontName='Helvetica-Bold',
                                            textColor=BLANCO, alignment=TA_CENTER))],
        [Paragraph(f'<b>{placa}</b>', ParagraphStyle('placa_val', fontSize=22,
                                                      fontName='Helvetica-Bold', textColor=BLANCO,
                                                      alignment=TA_CENTER, leading=26))],
    ], colWidths=[52*mm],
    style=TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), AZUL),
        ('BOX', (0, 0), (-1, -1), 2.5, colors.HexColor('#0d5fa8')),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 2),
        ('TOPPADDING', (0, 1), (-1, 1), 2),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))

    encabezado_data = [
        [
            Paragraph('<b>INFORME DE INSPECCIÓN</b>', ParagraphStyle('', fontSize=14,
                       fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_RIGHT)),
        ],
        [
            Table([
                [
                    Table([
                        [Paragraph('No. de servicio', styles['celda_label']),
                         Paragraph(f'<b>{no_servicio}</b>', ParagraphStyle('', fontSize=13,
                                    fontName='Helvetica-Bold', textColor=NEGRO))],
                        [Paragraph('Fecha', styles['celda_label']),
                         Paragraph(f'<b>{fecha}</b>', styles['celda_valor'])],
                    ], colWidths=[35*mm, 58*mm],
                    style=TableStyle([
                        ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                        ('ROWPADDING', (0, 0), (-1, -1), 5),
                    ])),
                    Spacer(4*mm, 1),
                    placa_table,
                ]
            ], colWidths=[W],
            style=TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.grey),
                ('ROWPADDING', (0, 0), (-1, -1), 4),
            ]))
        ]
    ]
    story.append(Table(encabezado_data, colWidths=[W],
                        style=TableStyle([('ROWPADDING', (0, 0), (-1, -1), 3)])))
    story.append(Spacer(1, 4*mm))

    # ── Sección 1: Datos del vehículo ────────────────────────────────────────
    story.append(seccion_header('1. Datos del Vehículo', styles))

    campos_v = [
        ('Clase', 'clase'), ('Combustible', 'combustible'),
        ('Marca', 'marca'), ('Pintura', 'pintura'),
        ('Línea', 'linea'), ('Servicio', 'servicio'),
        ('Carrocería', 'carroceria'), ('Kilometraje', 'kilometraje'),
        ('Modelo', 'modelo'), ('Color', 'color'),
        ('Nacionalidad', 'nacionalidad'), ('No. Chasis', 'no_chasis'),
        ('Tipo de caja', 'tipo_caja'), ('No. Serial', 'no_serial'),
        ('Cilindraje', 'cilindraje'), ('No. Motor', 'no_motor'),
    ]

    filas_v = []
    for i in range(0, len(campos_v), 2):
        l1, k1 = campos_v[i]
        l2, k2 = campos_v[i+1]
        filas_v.append([
            Paragraph(l1, styles['celda_label']),
            Paragraph(str(datos.get(k1, '')), styles['celda_valor']),
            Paragraph(l2, styles['celda_label']),
            Paragraph(str(datos.get(k2, '')), styles['celda_valor']),
        ])

    tabla_v = Table(filas_v, colWidths=[28*mm, 62*mm, 30*mm, 65*mm],
                    style=TableStyle([
                        ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                        ('BACKGROUND', (0, 0), (0, -1), GRIS_TABLA),
                        ('BACKGROUND', (2, 0), (2, -1), GRIS_TABLA),
                        ('ROWPADDING', (0, 0), (-1, -1), 3),
                        ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ]))
    story.append(tabla_v)

    # Propietario
    prop_data = [
        [Paragraph('Propietario', styles['celda_label']),
         Paragraph(str(datos.get('propietario', '')), styles['celda_valor']),
         Paragraph('Documento/NIT', styles['celda_label']),
         Paragraph(str(datos.get('documento', '')), styles['celda_valor'])],
        [Paragraph('Dueños anteriores', styles['celda_label']),
         Paragraph(str(datos.get('duenos_anteriores', '')), styles['celda_valor']),
         Paragraph('Aseguradora', styles['celda_label']),
         Paragraph(str(datos.get('aseguradora', '')), styles['celda_valor'])],
    ]
    story.append(Table(prop_data, colWidths=[35*mm, 55*mm, 35*mm, 60*mm],
                        style=TableStyle([
                            ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                            ('BACKGROUND', (0, 0), (0, -1), GRIS_TABLA),
                            ('BACKGROUND', (2, 0), (2, -1), GRIS_TABLA),
                            ('ROWPADDING', (0, 0), (-1, -1), 3),
                        ])))
    story.append(Spacer(1, 3*mm))

    # ── Sección 2: Documentación + Sección 3: Valores ────────────────────────
    docs_vals = Table([
        [seccion_header('2. Documentación', styles),
         Spacer(5*mm, 1),
         seccion_header('3. Valores', styles)],
    ], colWidths=[88*mm, 9*mm, 88*mm],
    style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(docs_vals)

    reporta = datos.get('reporta_siniestros', False)
    doc_data = [
        [Paragraph('Aseguradora', styles['celda_label']),
         Paragraph(str(datos.get('aseguradora', '')), styles['celda_valor'])],
        [Paragraph('Reporta Siniestros', styles['celda_label']),
         Paragraph(f"{'Sí' if reporta else 'No'} | Cuántos: {datos.get('cuantos_siniestros','0')} | "
                   f"Reclamaciones: ${datos.get('valor_reclamaciones','0')}", styles['celda_valor'])],
        [Paragraph('Documentos', styles['celda_label']),
         Paragraph(
             f"{'✓' if datos.get('tarjeta_propiedad') else '✗'} Tarjeta  "
             f"{'✓' if datos.get('soat') else '✗'} SOAT  "
             f"{'✓' if datos.get('rev_tecnomecanica') else '✗'} Tecnomecánica",
             styles['celda_valor'])],
    ]
    vals_data = [
        ('Revista Motor', 'val_revista_motor'),
        ('Fasecolda',     'val_fasecolda'),
        ('Mercado',       'val_mercado'),
        ('Accesorios',    'val_accesorios'),
        ('Depreciación',  'val_depreciacion'),
        ('Elperito.com',  'val_elperito'),
    ]

    side_by_side = Table([
        [
            Table(doc_data, colWidths=[32*mm, 55*mm],
                  style=TableStyle([
                      ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                      ('BACKGROUND', (0, 0), (0, -1), GRIS_TABLA),
                      ('ROWPADDING', (0, 0), (-1, -1), 3),
                  ])),
            Spacer(9*mm, 1),
            Table(
                [[Paragraph(l, styles['celda_label']),
                  Paragraph(f"$ {datos.get(k,'0')}", styles['celda_valor'])]
                 for l, k in vals_data],
                colWidths=[35*mm, 53*mm],
                style=TableStyle([
                    ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                    ('BACKGROUND', (0, 0), (0, -1), GRIS_TABLA),
                    ('ROWPADDING', (0, 0), (-1, -1), 3),
                ]))
        ]
    ], colWidths=[88*mm, 9*mm, 88*mm])
    story.append(side_by_side)
    story.append(Spacer(1, 3*mm))

    # ── Sección 4: Inspección Visual ─────────────────────────────────────────
    story.append(seccion_header('4. Inspección Visual y Técnica', styles))

    pct_carr = datos.get('pct_carroceria', 0)
    pct_chas = datos.get('pct_chasis', 0)
    pct_tot  = datos.get('calificacion_total', 0)

    insp_header = Table([
        [Paragraph(f'CARROCERÍA  {pct_carr}%', ParagraphStyle('', fontSize=9,
                    fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_CENTER)),
         Paragraph(f'CHASIS / ESTRUCTURA  {pct_chas}%', ParagraphStyle('', fontSize=9,
                    fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_CENTER)),
         Paragraph('COMPRESIÓN MOTOR (PSI)', ParagraphStyle('', fontSize=9,
                    fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_CENTER))],
    ], colWidths=[62*mm, 62*mm, 61*mm],
    style=TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, AZUL),
        ('LINEAFTER', (0, 0), (1, -1), 0.5, AZUL),
        ('ROWPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(insp_header)

    # Daños carrocería
    danos = datos.get('danos_carroceria', [])
    danos_rows = []
    for d in danos[:8]:
        estado = d.get('estado', '')
        desc = d.get('descripcion', '')
        color_bg = ROJO if estado == 'Malo' else (NARANJA if estado == 'Regular' else VERDE)
        danos_rows.append([
            Paragraph(d.get('pieza', ''), styles['normal_sm']),
            Paragraph(estado, ParagraphStyle('', fontSize=7.5, fontName='Helvetica-Bold',
                                              textColor=BLANCO, backColor=color_bg)),
            Paragraph(desc, styles['normal_sm']),
        ])
    while len(danos_rows) < 8:
        danos_rows.append(['', '', ''])

    # Compresión motor
    cils = datos.get('compresion_motor', {})
    comp_rows = []
    for i in range(1, 5):
        c1 = str(cils.get(f'cil{i}', ''))
        c2 = str(cils.get(f'cil{i+4}', ''))
        comp_rows.append([
            Paragraph(f'Cil.{i}', styles['celda_label']),
            Paragraph(c1, styles['celda_valor']),
            Paragraph(f'Cil.{i+4}', styles['celda_label']),
            Paragraph(c2, styles['celda_valor']),
        ])

    insp_body = Table([
        [
            Table(danos_rows, colWidths=[30*mm, 18*mm, 14*mm],
                  style=TableStyle([
                      ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                      ('ROWPADDING', (0, 0), (-1, -1), 2),
                  ])),
            Table([[Paragraph('(Ver obs. perito)', styles['normal_sm'])]],
                  colWidths=[53*mm]),
            Table(comp_rows, colWidths=[13*mm, 18*mm, 13*mm, 18*mm],
                  style=TableStyle([
                      ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                      ('BACKGROUND', (0, 0), (0, -1), GRIS_TABLA),
                      ('BACKGROUND', (2, 0), (2, -1), GRIS_TABLA),
                      ('ROWPADDING', (0, 0), (-1, -1), 3),
                  ])),
        ]
    ], colWidths=[62*mm, 53*mm, 62*mm],
    style=TableStyle([('VALIGN', (0,0),(-1,-1),'TOP')]))
    story.append(insp_body)

    # Llantas
    llantas_data = [
        [Paragraph('LLANTAS Y AMORTIGUADORES', ParagraphStyle('', fontSize=8,
                    fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_CENTER))],
        [Table([
            [Paragraph('Ruedas delanteras', styles['celda_label']),
             Paragraph(f"Vida útil: {datos.get('llanta_del_vida','')}"
                       f"  Presión: {datos.get('llanta_del_psi','')} PSI", styles['celda_valor']),
             Paragraph('Ruedas traseras', styles['celda_label']),
             Paragraph(f"Vida útil: {datos.get('llanta_tra_vida','')}"
                       f"  Presión: {datos.get('llanta_tra_psi','')} PSI", styles['celda_valor'])],
            [Paragraph('Requiere cambio', styles['celda_label']),
             Paragraph('Sí' if datos.get('llantas_cambio') else 'No', styles['celda_valor']),
             Paragraph('Suspensión delantera', styles['celda_label']),
             Paragraph('Requiere cambio: ' + ('Sí' if datos.get('susp_del_cambio') else 'No'),
                        styles['celda_valor'])],
            [Paragraph('Suspensión trasera', styles['celda_label']),
             Paragraph('Requiere cambio: ' + ('Sí' if datos.get('susp_tra_cambio') else 'No'),
                        styles['celda_valor']),
             Paragraph('Cal. Final Total', styles['celda_label']),
             Paragraph(f'<b>{pct_tot}%</b>', ParagraphStyle('', fontSize=12,
                        fontName='Helvetica-Bold', textColor=AZUL))],
        ], colWidths=[35*mm, 55*mm, 35*mm, 60*mm],
        style=TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ('BACKGROUND', (0, 0), (0, -1), GRIS_TABLA),
            ('BACKGROUND', (2, 0), (2, -1), GRIS_TABLA),
            ('ROWPADDING', (0, 0), (-1, -1), 3),
        ]))]
    ]
    story.append(Table(llantas_data, colWidths=[W],
                        style=TableStyle([
                            ('BOX', (0, 0), (-1, -1), 0.5, AZUL),
                            ('ROWPADDING', (0, 0), (-1, -1), 2),
                        ])))
    story.append(Spacer(1, 3*mm))

    # ── Secciones 5-12: tabla de ítems bueno/regular/malo ───────────────────
    def tabla_items(seccion_key, items_def, datos_d):
        """Genera tabla de ítems con marcas B/R/M/NA y ponderación automática."""
        n_items = len(items_def)
        peso_item = round(100 / n_items, 1) if n_items else 0
        filas = [[
            Paragraph('Ítem', styles['celda_label']),
            Paragraph('Peso', styles['celda_label']),
            Paragraph('B', styles['celda_label']),
            Paragraph('R', styles['celda_label']),
            Paragraph('M', styles['celda_label']),
            Paragraph('N/A', styles['celda_label']),
        ]]
        for label, key in items_def:
            val = datos_d.get(f'{seccion_key}_{key}', 'B')
            filas.append([
                Paragraph(label, styles['normal_sm']),
                Paragraph(f'{peso_item}%', ParagraphStyle('pw', fontSize=6.5,
                           fontName='Helvetica', textColor=colors.grey, alignment=TA_CENTER)),
                'X' if val == 'B' else '',
                'X' if val == 'R' else '',
                'X' if val == 'M' else '',
                'X' if val == 'NA' else '',
            ])
        pct = int(datos_d.get(f'pct_{seccion_key}', 0) or 0)
        result_color = VERDE if pct >= 90 else (NARANJA if pct >= 70 else ROJO)
        filas.append([
            Paragraph(f'<b>Resultado sección: {pct}%</b>', ParagraphStyle('res', fontSize=8,
                       fontName='Helvetica-Bold', textColor=BLANCO, alignment=TA_CENTER)),
            '', '', '', '', ''
        ])
        return Table(filas, colWidths=[45*mm, 11*mm, 8*mm, 8*mm, 8*mm, 8*mm],
                     style=TableStyle([
                         ('GRID', (0, 0), (-1, -2), 0.3, colors.lightgrey),
                         ('BACKGROUND', (0, 0), (-1, 0), AZUL_CLARO),
                         ('FONTSIZE', (0, 0), (-1, -1), 7.5),
                         ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
                         ('ROWPADDING', (0, 0), (-1, -1), 2),
                         ('SPAN', (0, -1), (-1, -1)),
                         ('BACKGROUND', (0, -1), (-1, -1), result_color),
                         ('TOPPADDING', (0, -1), (-1, -1), 5),
                         ('BOTTOMPADDING', (0, -1), (-1, -1), 5),
                     ]))

    secciones_items = [
        ('5. Motor', 'motor', [
            ('Arranque', 'arranque'), ('Radiador', 'radiador'), ('Carter motor', 'carter_motor'),
            ('Carter de caja', 'carter_caja'), ('Caja velocidades', 'caja_vel'),
            ('Soporte de caja', 'soporte_caja'), ('Soporte de motor', 'soporte_motor'),
            ('Ruido de motor', 'ruido_motor'), ('Ventiladores', 'ventiladores'),
            ('Mangueras', 'mangueras'), ('Embrague', 'embrague'),
            ('Sincronización', 'sincronizacion'), ('Correa repartición', 'correa_rep'),
            ('Correa accesorios', 'correa_acc'), ('Polea y tensores', 'polea_tensores'),
        ]),
        ('6. Sistema Eléctrico', 'electrico', [
            ('Panorámico delantero', 'panoramico_del'), ('Panorámico trasero', 'panoramico_tra'),
            ('Vidrios', 'vidrios'), ('Plumillas', 'plumillas'),
            ('Farola derecha', 'farola_der'), ('Farola izquierda', 'farola_izq'),
            ('Exploradora derecha', 'explor_der'), ('Exploradora izquierda', 'explor_izq'),
            ('Stop derecho', 'stop_der'), ('Stop izquierdo', 'stop_izq'),
            ('Bocina', 'bocina'), ('Batería', 'bateria'),
            ('Fusibles', 'fusibles'), ('Alternador', 'alternador'),
            ('Luz porta placa', 'luz_placa'),
        ]),
        ('7. Fuga de Fluidos', 'fluidos', [
            ('Fuga aceite motor', 'aceite_motor'), ('Fuga aceite caja', 'aceite_caja'),
            ('Fuga refrigerante', 'refrigerante'), ('Fuga líquido freno', 'liq_freno'),
            ('Fuga dirección hidráulica', 'dir_hidraulica'), ('Fuga diferencial', 'diferencial'),
        ]),
        ('8. Llantas', 'llantas', [
            ('Llantas delanteras', 'del'), ('Llantas traseras', 'tra'),
            ('Llanta de repuesto', 'repuesto'), ('Desgaste', 'desgaste'),
            ('Estado rines', 'rines'),
        ]),
        ('9. Tren Delantero y Suspensión', 'tren', [
            ('Pastillas de freno', 'pastillas'), ('Discos de freno', 'discos'),
            ('Amortiguadores del.', 'amort_del'), ('Amortiguadores tra.', 'amort_tra'),
            ('Puntas de ejes', 'puntas'), ('Axiales', 'axiales'),
            ('Terminales', 'terminales'), ('Rótulas', 'rotulas'),
            ('Bujes', 'bujes'), ('Tijeras', 'tijeras'),
            ('Caja de dirección', 'caja_dir'), ('Rodamientos', 'rodamientos'),
        ]),
        ('10. Interior del Vehículo', 'interior', [
            ('Calefacción', 'calefaccion'), ('Aire acondicionado', 'aire_ac'),
            ('Cinturones', 'cinturones'), ('Asiento derecho', 'asiento_der'),
            ('Asiento izquierdo', 'asiento_izq'), ('Asientos traseros', 'asientos_tra'),
            ('Condición del techo', 'techo'), ('Manija techo', 'manija_techo'),
            ('Luz de techo', 'luz_techo'), ('Carteras', 'carteras'),
            ('Millare', 'millare'), ('Alfombras', 'alfombras'),
            ('Cabeceros', 'cabeceros'), ('Tapetes', 'tapetes'),
        ]),
        ('11. Prueba de Ruta', 'ruta', [
            ('Aceleración', 'aceleracion'), ('Maniobrabilidad', 'maniobrabilidad'),
            ('Ángulo de alineación', 'alineacion'), ('Condición de frenado', 'frenado'),
            ('Condición del embrague', 'embrague'), ('Relación caja-motor', 'relacion_caja'),
            ('Vibraciones', 'vibraciones'),
        ]),
    ]

    # Agrupar secciones de 2 en 2 columnas
    for i in range(0, len(secciones_items), 2):
        left_titulo, left_key, left_items = secciones_items[i]
        story.append(Spacer(1, 2*mm))
        if i + 1 < len(secciones_items):
            right_titulo, right_key, right_items = secciones_items[i+1]
            row = Table([
                [seccion_header(left_titulo, styles), Spacer(5*mm, 1),
                 seccion_header(right_titulo, styles)],
                [tabla_items(left_key, left_items, datos), Spacer(5*mm, 1),
                 tabla_items(right_key, right_items, datos)],
            ], colWidths=[90*mm, 5*mm, 90*mm],
            style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        else:
            row = Table([
                [seccion_header(left_titulo, styles)],
                [tabla_items(left_key, left_items, datos)],
            ], colWidths=[W])
        story.append(row)

    # ── Sección 12: Accesorios ───────────────────────────────────────────────
    story.append(Spacer(1, 2*mm))
    story.append(seccion_header('12. Accesorios', styles))
    accesorios = datos.get('accesorios', [])
    acc_rows = []
    for j in range(0, len(accesorios), 3):
        row = []
        for k in range(3):
            if j+k < len(accesorios):
                row.append(Paragraph(f'• {accesorios[j+k]}', styles['normal_sm']))
            else:
                row.append('')
        acc_rows.append(row)
    if not acc_rows:
        acc_rows = [['', '', '']]
    story.append(Table(acc_rows, colWidths=[62*mm, 62*mm, 61*mm],
                        style=TableStyle([
                            ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                            ('ROWPADDING', (0, 0), (-1, -1), 3),
                        ])))

    # ── Observaciones del Perito ─────────────────────────────────────────────
    story.append(Spacer(1, 3*mm))
    story.append(seccion_header('Observaciones del Perito', styles))
    observaciones = datos.get('observaciones', [])
    nivel_colores = {
        'AVISO': colors.HexColor('#f39c12'),
        'PENDIENTE': AZUL,
        'INMEDIATO': ROJO,
    }
    obs_rows = [[
        Paragraph('Nivel', styles['titulo_seccion']),
        Paragraph('Acción recomendada', styles['titulo_seccion']),
    ]]
    for obs in observaciones:
        nivel = obs.get('nivel', 'AVISO').upper()
        bg = nivel_colores.get(nivel, NARANJA)
        obs_rows.append([
            Paragraph(nivel, ParagraphStyle('', fontSize=7.5, fontName='Helvetica-Bold',
                                             textColor=BLANCO, backColor=bg, alignment=TA_CENTER)),
            Paragraph(obs.get('descripcion', ''), styles['normal_sm']),
        ])
    story.append(Table(obs_rows, colWidths=[30*mm, 155*mm],
                        style=TableStyle([
                            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
                            ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                            ('ROWPADDING', (0, 0), (-1, -1), 3),
                        ])))

    # ── Calificación Final ───────────────────────────────────────────────────
    story.append(Spacer(1, 4*mm))
    story.append(Table([
        [Paragraph('CALIFICACIÓN FINAL TOTAL:',
                   ParagraphStyle('', fontSize=14, fontName='Helvetica-Bold',
                                  textColor=NEGRO, alignment=TA_RIGHT)),
         Paragraph(f'<b>{pct_tot}%</b>',
                   ParagraphStyle('', fontSize=24, fontName='Helvetica-Bold',
                                  textColor=AZUL, alignment=TA_CENTER))],
    ], colWidths=[140*mm, 45*mm],
    style=TableStyle([
        ('BOX', (0, 0), (-1, -1), 1.5, AZUL),
        ('ROWPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ])))

    # ── Pie de página ────────────────────────────────────────────────────────
    story.append(Spacer(1, 4*mm))
    pie_txt = (
        "Aviso legal: Este diagnóstico automotriz está basado exclusivamente en criterios técnicos "
        "y va destinado únicamente al solicitante. No se podrá usar como medio que garantice la "
        "comercialización, ni relación contractual alguna del vehículo."
    )
    story.append(Paragraph(pie_txt, styles['pie']))

    doc.build(story)
    buffer.seek(0)
    return buffer


# ─── Rutas Flask ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('formulario.html')


@app.route('/generar_pdf', methods=['POST'])
def generar_pdf_route():
    # Soporta tanto multipart/form-data (con adjunto) como JSON (sin adjunto)
    if request.content_type and 'multipart/form-data' in request.content_type:
        datos = json.loads(request.form.get('datos', '{}'))
        pdf_adjunto = request.files.get('pdf_antecedentes')
    else:
        datos = request.get_json() or {}
        pdf_adjunto = None

    buffer_informe = generar_pdf(datos)
    placa = datos.get('placa', 'vehiculo')
    nombre = f"peritaje_{placa}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    if pdf_adjunto and pdf_adjunto.filename:
        # Fusionar: informe pericial + PDF de antecedentes
        try:
            writer = PdfWriter()
            # Páginas del informe generado
            reader_informe = PdfReader(buffer_informe)
            for page in reader_informe.pages:
                writer.add_page(page)
            # Páginas del PDF adjunto
            contenido_adjunto = io.BytesIO(pdf_adjunto.read())
            reader_adjunto = PdfReader(contenido_adjunto)
            for page in reader_adjunto.pages:
                writer.add_page(page)
            # Salida fusionada
            buffer_final = io.BytesIO()
            writer.write(buffer_final)
            buffer_final.seek(0)
            nombre = f"informe_completo_{placa}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            return send_file(buffer_final, mimetype='application/pdf',
                             as_attachment=True, download_name=nombre)
        except Exception as e:
            # Si falla el merge, retorna solo el informe generado
            buffer_informe.seek(0)
            return send_file(buffer_informe, mimetype='application/pdf',
                             as_attachment=True, download_name=nombre)

    buffer_informe.seek(0)
    return send_file(buffer_informe, mimetype='application/pdf',
                     as_attachment=True, download_name=nombre)


if __name__ == '__main__':
    app.run(debug=True, port=5050)
