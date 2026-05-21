from flask import Flask, render_template, request, send_file, jsonify, session, redirect, url_for
import json, io, sqlite3, hashlib, os
from datetime import datetime
from functools import wraps
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from pypdf import PdfWriter, PdfReader

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'peritgo-dev-xK9mN2025')
DB_PATH = os.path.join(os.path.dirname(__file__), 'peritgo.db')

# ─── Database ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nombre TEXT NOT NULL,
            activo INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placa TEXT NOT NULL,
            cliente TEXT DEFAULT '',
            tipo_servicio TEXT DEFAULT 'Peritaje Completo',
            estado TEXT DEFAULT 'creado',
            paso_actual INTEGER DEFAULT 0,
            perito_id INTEGER,
            perito_nombre TEXT DEFAULT '',
            fecha_creacion TEXT,
            fecha_actualizacion TEXT,
            datos_json TEXT DEFAULT '{}',
            pdf_data BLOB,
            activo INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            servicio_id INTEGER NOT NULL,
            tipo TEXT DEFAULT 'cir',
            nombre TEXT,
            datos BLOB,
            fecha TEXT,
            FOREIGN KEY (servicio_id) REFERENCES servicios(id)
        );
    ''')
    for username, pw, nombre in [('admin', 'admin123', 'Administrador'), ('perito1', 'perito123', 'Perito 1')]:
        ph = hashlib.sha256(pw.encode()).hexdigest()
        try:
            conn.execute('INSERT INTO usuarios (username, password_hash, nombre) VALUES (?,?,?)', (username, ph, nombre))
            conn.commit()
        except sqlite3.IntegrityError:
            pass
    conn.close()

init_db()

# ─── Auth ────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ─── Pages ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('bitacora') if 'user_id' in session else url_for('login_page'))

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if 'user_id' in session:
        return redirect(url_for('bitacora'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db()
        user = conn.execute('SELECT * FROM usuarios WHERE username=? AND activo=1', (username,)).fetchone()
        conn.close()
        if user and user['password_hash'] == hash_pw(password):
            session.update({'user_id': user['id'], 'user_nombre': user['nombre'], 'username': user['username']})
            return redirect(url_for('bitacora'))
        error = 'Usuario o contraseña incorrectos'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/bitacora')
@login_required
def bitacora():
    return render_template('bitacora.html', usuario=session.get('user_nombre'), username=session.get('username'))

@app.route('/formulario/<int:servicio_id>')
@login_required
def formulario(servicio_id):
    conn = get_db()
    svc = conn.execute('SELECT * FROM servicios WHERE id=?', (servicio_id,)).fetchone()
    conn.close()
    if not svc:
        return redirect(url_for('bitacora'))
    return render_template('formulario.html', servicio_id=servicio_id,
                           placa=svc['placa'], cliente=svc['cliente'],
                           tipo_servicio=svc['tipo_servicio'], usuario=session.get('user_nombre'))

# ─── API: Servicios ───────────────────────────────────────────────────────────

@app.route('/api/servicios', methods=['POST'])
@login_required
def crear_servicio():
    data = request.get_json()
    placa = data.get('placa', '').upper().strip()
    if not placa:
        return jsonify({'error': 'Placa requerida'}), 400
    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO servicios (placa, cliente, tipo_servicio, perito_id, perito_nombre, fecha_creacion, fecha_actualizacion) VALUES (?,?,?,?,?,?,?)',
        (placa, data.get('cliente', ''), data.get('tipo_servicio', 'Peritaje Completo'),
         session['user_id'], session['user_nombre'], ahora, ahora))
    sid = cur.lastrowid
    conn.commit(); conn.close()
    return jsonify({'id': sid, 'placa': placa}), 201

@app.route('/api/servicios/activos')
@login_required
def servicios_activos():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, placa, cliente, tipo_servicio, estado, paso_actual, perito_nombre, "
        "fecha_creacion, fecha_actualizacion, (pdf_data IS NOT NULL) as tiene_pdf "
        "FROM servicios WHERE activo=1 AND estado != 'cerrado' ORDER BY fecha_actualizacion DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/servicios/buscar')
@login_required
def buscar_servicios():
    q = '%' + request.args.get('q', '').strip() + '%'
    conn = get_db()
    rows = conn.execute(
        "SELECT id, placa, cliente, tipo_servicio, estado, paso_actual, perito_nombre, "
        "fecha_creacion, fecha_actualizacion, (pdf_data IS NOT NULL) as tiene_pdf "
        "FROM servicios WHERE upper(placa) LIKE upper(?) OR upper(cliente) LIKE upper(?) "
        "ORDER BY fecha_creacion DESC LIMIT 50",
        (q, q)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/servicios/<int:sid>/progreso', methods=['POST'])
@login_required
def actualizar_progreso(sid):
    data = request.get_json()
    paso = data.get('paso', 0)
    estado = data.get('estado', f'paso_{paso}')
    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    conn.execute('UPDATE servicios SET estado=?, paso_actual=?, fecha_actualizacion=? WHERE id=?',
                 (estado, int(paso) if str(paso).isdigit() else 11, ahora, sid))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/servicios/<int:sid>/cerrar', methods=['POST'])
@login_required
def cerrar_servicio(sid):
    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    conn.execute("UPDATE servicios SET estado='cerrado', activo=0, fecha_actualizacion=? WHERE id=?", (ahora, sid))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/servicios/<int:sid>/pdf')
@login_required
def descargar_pdf_servicio(sid):
    conn = get_db()
    row = conn.execute('SELECT pdf_data, placa FROM servicios WHERE id=?', (sid,)).fetchone()
    conn.close()
    if not row or not row['pdf_data']:
        return jsonify({'error': 'PDF no disponible'}), 404
    return send_file(io.BytesIO(bytes(row['pdf_data'])), mimetype='application/pdf',
                     as_attachment=True, download_name=f"peritaje_{row['placa']}.pdf")

@app.route('/api/servicios/<int:sid>/cir', methods=['POST'])
@login_required
def subir_cir(sid):
    archivo = request.files.get('cir')
    if not archivo:
        return jsonify({'error': 'Archivo requerido'}), 400
    datos = archivo.read()
    ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    existing = conn.execute('SELECT id FROM documentos WHERE servicio_id=? AND tipo="cir"', (sid,)).fetchone()
    if existing:
        conn.execute('UPDATE documentos SET datos=?, nombre=?, fecha=? WHERE id=?',
                     (datos, archivo.filename, ahora, existing['id']))
    else:
        conn.execute('INSERT INTO documentos (servicio_id, tipo, nombre, datos, fecha) VALUES (?,?,?,?,?)',
                     (sid, 'cir', archivo.filename, datos, ahora))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/servicios/<int:sid>/tiene_cir')
@login_required
def tiene_cir(sid):
    conn = get_db()
    row = conn.execute('SELECT nombre FROM documentos WHERE servicio_id=? AND tipo="cir"', (sid,)).fetchone()
    conn.close()
    return jsonify({'tiene_cir': row is not None, 'nombre': row['nombre'] if row else None})

@app.route('/api/servicios/<int:sid>/completo')
@login_required
def pdf_completo(sid):
    conn = get_db()
    svc = conn.execute('SELECT pdf_data, placa FROM servicios WHERE id=?', (sid,)).fetchone()
    doc = conn.execute('SELECT datos FROM documentos WHERE servicio_id=? AND tipo="cir"', (sid,)).fetchone()
    conn.close()
    if not svc or not svc['pdf_data']:
        return jsonify({'error': 'PDF del informe no disponible'}), 404
    if not doc or not doc['datos']:
        return send_file(io.BytesIO(bytes(svc['pdf_data'])), mimetype='application/pdf',
                         as_attachment=True, download_name=f"informe_{svc['placa']}.pdf")
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(bytes(svc['pdf_data']))).pages:
        writer.add_page(page)
    for page in PdfReader(io.BytesIO(bytes(doc['datos']))).pages:
        writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf); buf.seek(0)
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=True, download_name=f"completo_{svc['placa']}.pdf")

# ─── Colores y estilos PDF ────────────────────────────────────────────────────

AZUL        = colors.HexColor('#1a73c7')
AZUL_CLARO  = colors.HexColor('#e8f1fb')
GRIS_TABLA  = colors.HexColor('#f5f5f5')
GRIS_HEADER = colors.HexColor('#4a4a4a')
VERDE       = colors.HexColor('#27ae60')
ROJO        = colors.HexColor('#e74c3c')
NARANJA     = colors.HexColor('#e67e22')
BLANCO      = colors.white
NEGRO       = colors.black

def get_styles():
    custom = {
        'titulo_seccion': ParagraphStyle('titulo_seccion', fontSize=9, fontName='Helvetica-Bold',
                                          textColor=BLANCO, alignment=TA_CENTER, spaceAfter=0),
        'celda_label': ParagraphStyle('celda_label', fontSize=7.5, fontName='Helvetica',
                                       textColor=GRIS_HEADER, leading=10),
        'celda_valor': ParagraphStyle('celda_valor', fontSize=8, fontName='Helvetica-Bold',
                                       textColor=NEGRO, leading=10),
        'normal_sm': ParagraphStyle('normal_sm', fontSize=7.5, fontName='Helvetica',
                                     textColor=NEGRO, leading=10),
        'pie': ParagraphStyle('pie', fontSize=5.5, fontName='Helvetica', textColor=colors.grey, leading=7),
    }
    return custom

def seccion_header(titulo, styles):
    return Table([[Paragraph(titulo, styles['titulo_seccion'])]],
                  colWidths=[185*mm],
                  style=TableStyle([('BACKGROUND', (0,0), (-1,-1), AZUL),
                                     ('ROWPADDING', (0,0), (-1,-1), 4),
                                     ('TOPPADDING', (0,0), (-1,-1), 5),
                                     ('BOTTOMPADDING', (0,0), (-1,-1), 5)]))

# ─── Generador PDF ────────────────────────────────────────────────────────────

def generar_pdf(datos):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                             leftMargin=12*mm, rightMargin=12*mm,
                             topMargin=14*mm, bottomMargin=14*mm)
    styles = get_styles()
    story = []
    W = 185*mm

    # Cálculo automático de puntajes
    PESOS = {'motor': 20, 'electrico': 10, 'fluidos': 10, 'llantas': 10, 'tren': 20, 'interior': 10, 'ruta': 20}
    ITEMS_BACK = {
        'motor':    ['arranque','radiador','carter_motor','carter_caja','caja_vel','soporte_caja','soporte_motor','ruido_motor','ventiladores','mangueras','embrague','sincronizacion','correa_rep','correa_acc','polea_tensores'],
        'electrico':['panoramico_del','panoramico_tra','vidrios','plumillas','farola_der','farola_izq','explor_der','explor_izq','stop_der','stop_izq','bocina','bateria','fusibles','alternador','luz_placa'],
        'fluidos':  ['aceite_motor','aceite_caja','refrigerante','liq_freno','dir_hidraulica','diferencial'],
        'llantas':  ['del','tra','repuesto','desgaste','rines'],
        'tren':     ['pastillas','discos','amort_del','amort_tra','puntas','axiales','terminales','rotulas','bujes','tijeras','caja_dir','rodamientos'],
        'interior': ['calefaccion','aire_ac','cinturones','asiento_der','asiento_izq','asientos_tra','techo','manija_techo','luz_techo','carteras','millare','alfombras','cabeceros','tapetes'],
        'ruta':     ['aceleracion','maniobrabilidad','alineacion','frenado','embrague','relacion_caja','vibraciones'],
    }
    VALOR = {'B': 100, 'R': 50, 'M': 0}
    suma_p = suma_w = 0
    for sec, items in ITEMS_BACK.items():
        vals = [VALOR.get(datos.get(f'{sec}_{k}', 'B'), 100) for k in items if datos.get(f'{sec}_{k}', 'B') != 'NA']
        pct = round(sum(vals)/len(vals)) if vals else 100
        datos[f'pct_{sec}'] = pct
        suma_p += pct * PESOS[sec]; suma_w += PESOS[sec]
    datos['calificacion_total'] = round(suma_p/suma_w) if suma_w else 100

    # Encabezado
    fecha = datos.get('fecha', datetime.now().strftime('%d/%m/%Y'))
    placa = datos.get('placa', '---').upper()
    no_servicio = datos.get('no_servicio', '---')

    placa_table = Table([
        [Paragraph('PLACA', ParagraphStyle('pl', fontSize=7, fontName='Helvetica-Bold', textColor=BLANCO, alignment=TA_CENTER))],
        [Paragraph(f'<b>{placa}</b>', ParagraphStyle('pv', fontSize=22, fontName='Helvetica-Bold', textColor=BLANCO, alignment=TA_CENTER, leading=26))],
    ], colWidths=[52*mm],
    style=TableStyle([('BACKGROUND',(0,0),(-1,-1),AZUL),('BOX',(0,0),(-1,-1),2.5,colors.HexColor('#0d5fa8')),
                       ('TOPPADDING',(0,0),(-1,0),6),('BOTTOMPADDING',(0,0),(-1,0),2),
                       ('TOPPADDING',(0,1),(-1,1),2),('BOTTOMPADDING',(0,1),(-1,1),8)]))

    enc = Table([[Paragraph('<b>INFORME DE INSPECCIÓN</b>',
                             ParagraphStyle('', fontSize=14, fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_RIGHT))]],
                 colWidths=[W], style=TableStyle([('ROWPADDING',(0,0),(-1,-1),3)]))
    story.append(enc)

    meta = Table([[
        Table([[Paragraph('No. de servicio', styles['celda_label']),
                 Paragraph(f'<b>{no_servicio}</b>', ParagraphStyle('', fontSize=13, fontName='Helvetica-Bold', textColor=NEGRO))],
                [Paragraph('Fecha', styles['celda_label']),
                 Paragraph(f'<b>{fecha}</b>', styles['celda_valor'])]],
               colWidths=[35*mm, 58*mm],
               style=TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('ROWPADDING',(0,0),(-1,-1),5)])),
        Spacer(4*mm, 1),
        placa_table,
    ]], colWidths=[W],
    style=TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('BOX',(0,0),(-1,-1),0.5,colors.grey),('ROWPADDING',(0,0),(-1,-1),4)]))
    story.append(meta)
    story.append(Spacer(1, 4*mm))

    # Sección 1: Datos del vehículo
    story.append(seccion_header('1. Datos del Vehículo', styles))
    campos_v = [('Clase','clase'),('Combustible','combustible'),('Marca','marca'),('Pintura','pintura'),
                ('Línea','linea'),('Servicio','servicio'),('Carrocería','carroceria'),('Kilometraje','kilometraje'),
                ('Modelo','modelo'),('Color','color'),('Nacionalidad','nacionalidad'),('No. Chasis','no_chasis'),
                ('Tipo de caja','tipo_caja'),('No. Serial','no_serial'),('Cilindraje','cilindraje'),('No. Motor','no_motor')]
    filas_v = []
    for i in range(0, len(campos_v), 2):
        l1,k1 = campos_v[i]; l2,k2 = campos_v[i+1]
        filas_v.append([Paragraph(l1, styles['celda_label']), Paragraph(str(datos.get(k1,'')), styles['celda_valor']),
                         Paragraph(l2, styles['celda_label']), Paragraph(str(datos.get(k2,'')), styles['celda_valor'])])
    story.append(Table(filas_v, colWidths=[28*mm,62*mm,30*mm,65*mm],
                        style=TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
                                           ('BACKGROUND',(0,0),(0,-1),GRIS_TABLA),('BACKGROUND',(2,0),(2,-1),GRIS_TABLA),
                                           ('ROWPADDING',(0,0),(-1,-1),3),('FONTSIZE',(0,0),(-1,-1),8)])))
    story.append(Table([
        [Paragraph('Propietario',styles['celda_label']),Paragraph(str(datos.get('propietario','')),styles['celda_valor']),
         Paragraph('Documento/NIT',styles['celda_label']),Paragraph(str(datos.get('documento','')),styles['celda_valor'])],
        [Paragraph('Dueños anteriores',styles['celda_label']),Paragraph(str(datos.get('duenos_anteriores','')),styles['celda_valor']),
         Paragraph('Aseguradora',styles['celda_label']),Paragraph(str(datos.get('aseguradora','')),styles['celda_valor'])],
    ], colWidths=[35*mm,55*mm,35*mm,60*mm],
    style=TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),
                       ('BACKGROUND',(0,0),(0,-1),GRIS_TABLA),('BACKGROUND',(2,0),(2,-1),GRIS_TABLA),
                       ('ROWPADDING',(0,0),(-1,-1),3)])))
    story.append(Spacer(1,3*mm))

    # Sección 2+3
    story.append(seccion_header('2. Documentación y 3. Valores', styles))
    reporta = datos.get('reporta_siniestros', False)
    doc_data = [
        [Paragraph('Aseguradora',styles['celda_label']),Paragraph(str(datos.get('aseguradora','')),styles['celda_valor'])],
        [Paragraph('Reporta Siniestros',styles['celda_label']),
         Paragraph(f"{'Sí' if reporta else 'No'} | Cuántos: {datos.get('cuantos_siniestros','0')} | Reclamaciones: ${datos.get('valor_reclamaciones','0')}",styles['celda_valor'])],
        [Paragraph('Documentos',styles['celda_label']),
         Paragraph(f"{'✓' if datos.get('tarjeta_propiedad') else '✗'} Tarjeta  {'✓' if datos.get('soat') else '✗'} SOAT  {'✓' if datos.get('rev_tecnomecanica') else '✗'} Tecnomecánica",styles['celda_valor'])],
    ]
    vals_data = [('Revista Motor','val_revista_motor'),('Fasecolda','val_fasecolda'),('Mercado','val_mercado'),
                 ('Accesorios','val_accesorios'),('Depreciación','val_depreciacion'),('Elperito.com','val_elperito')]
    story.append(Table([[
        Table(doc_data, colWidths=[32*mm,55*mm],
              style=TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('BACKGROUND',(0,0),(0,-1),GRIS_TABLA),('ROWPADDING',(0,0),(-1,-1),3)])),
        Spacer(9*mm,1),
        Table([[Paragraph(l,styles['celda_label']),Paragraph(f"$ {datos.get(k,'0')}",styles['celda_valor'])] for l,k in vals_data],
              colWidths=[35*mm,53*mm],
              style=TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('BACKGROUND',(0,0),(0,-1),GRIS_TABLA),('ROWPADDING',(0,0),(-1,-1),3)]))
    ]], colWidths=[88*mm,9*mm,88*mm]))
    story.append(Spacer(1,3*mm))

    # Sección 4
    story.append(seccion_header('4. Inspección Visual y Técnica', styles))
    pct_carr = datos.get('pct_carroceria', 0)
    pct_chas = datos.get('pct_chasis', 0)
    pct_tot = datos.get('calificacion_total', 0)
    story.append(Table([[
        Paragraph(f'CARROCERÍA  {pct_carr}%', ParagraphStyle('', fontSize=9, fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_CENTER)),
        Paragraph(f'CHASIS / ESTRUCTURA  {pct_chas}%', ParagraphStyle('', fontSize=9, fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_CENTER)),
        Paragraph('COMPRESIÓN MOTOR (PSI)', ParagraphStyle('', fontSize=9, fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_CENTER)),
    ]], colWidths=[62*mm,62*mm,61*mm],
    style=TableStyle([('BOX',(0,0),(-1,-1),0.5,AZUL),('LINEAFTER',(0,0),(1,-1),0.5,AZUL),('ROWPADDING',(0,0),(-1,-1),4)])))

    danos = datos.get('danos_carroceria', [])
    danos_rows = []
    for d in danos[:8]:
        estado = d.get('estado','')
        bg = ROJO if estado=='Malo' else (NARANJA if estado=='Regular' else VERDE)
        danos_rows.append([Paragraph(d.get('pieza',''), styles['normal_sm']),
                            Paragraph(estado, ParagraphStyle('', fontSize=7.5, fontName='Helvetica-Bold', textColor=BLANCO, backColor=bg)),
                            Paragraph(d.get('descripcion',''), styles['normal_sm'])])
    while len(danos_rows) < 8:
        danos_rows.append(['','',''])

    cils = datos.get('compresion_motor', {})
    comp_rows = [[Paragraph(f'Cil.{i}',styles['celda_label']),Paragraph(str(cils.get(f'cil{i}','')),styles['celda_valor']),
                   Paragraph(f'Cil.{i+4}',styles['celda_label']),Paragraph(str(cils.get(f'cil{i+4}','')),styles['celda_valor'])] for i in range(1,5)]

    story.append(Table([[
        Table(danos_rows, colWidths=[30*mm,18*mm,14*mm],
              style=TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('ROWPADDING',(0,0),(-1,-1),2)])),
        Table([[Paragraph('(Ver obs. perito)',styles['normal_sm'])]],colWidths=[53*mm]),
        Table(comp_rows, colWidths=[13*mm,18*mm,13*mm,18*mm],
              style=TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('BACKGROUND',(0,0),(0,-1),GRIS_TABLA),
                                 ('BACKGROUND',(2,0),(2,-1),GRIS_TABLA),('ROWPADDING',(0,0),(-1,-1),3)]))
    ]], colWidths=[62*mm,53*mm,62*mm], style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP')])))

    story.append(Table([
        [Paragraph('LLANTAS Y AMORTIGUADORES', ParagraphStyle('', fontSize=8, fontName='Helvetica-Bold', textColor=AZUL, alignment=TA_CENTER))],
        [Table([
            [Paragraph('Ruedas delanteras',styles['celda_label']),Paragraph(f"Vida útil: {datos.get('llanta_del_vida','')}  Presión: {datos.get('llanta_del_psi','')} PSI",styles['celda_valor']),
             Paragraph('Ruedas traseras',styles['celda_label']),Paragraph(f"Vida útil: {datos.get('llanta_tra_vida','')}  Presión: {datos.get('llanta_tra_psi','')} PSI",styles['celda_valor'])],
            [Paragraph('Requiere cambio',styles['celda_label']),Paragraph('Sí' if datos.get('llantas_cambio') else 'No',styles['celda_valor']),
             Paragraph('Suspensión delantera',styles['celda_label']),Paragraph('Requiere cambio: '+('Sí' if datos.get('susp_del_cambio') else 'No'),styles['celda_valor'])],
            [Paragraph('Suspensión trasera',styles['celda_label']),Paragraph('Requiere cambio: '+('Sí' if datos.get('susp_tra_cambio') else 'No'),styles['celda_valor']),
             Paragraph('Cal. Final Total',styles['celda_label']),Paragraph(f'<b>{pct_tot}%</b>',ParagraphStyle('',fontSize=12,fontName='Helvetica-Bold',textColor=AZUL))],
        ], colWidths=[35*mm,55*mm,35*mm,60*mm],
        style=TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('BACKGROUND',(0,0),(0,-1),GRIS_TABLA),
                           ('BACKGROUND',(2,0),(2,-1),GRIS_TABLA),('ROWPADDING',(0,0),(-1,-1),3)]))]
    ], colWidths=[W], style=TableStyle([('BOX',(0,0),(-1,-1),0.5,AZUL),('ROWPADDING',(0,0),(-1,-1),2)])))
    story.append(Spacer(1,3*mm))

    # Secciones 5-11
    def tabla_items(sec_key, items_def, datos_d):
        n = len(items_def)
        peso = round(100/n,1) if n else 0
        filas = [[Paragraph('Ítem',styles['celda_label']),Paragraph('Peso',styles['celda_label']),
                   Paragraph('B',styles['celda_label']),Paragraph('R',styles['celda_label']),
                   Paragraph('M',styles['celda_label']),Paragraph('N/A',styles['celda_label'])]]
        for label, key in items_def:
            val = datos_d.get(f'{sec_key}_{key}', 'B')
            filas.append([Paragraph(label,styles['normal_sm']),
                           Paragraph(f'{peso}%',ParagraphStyle('pw',fontSize=6.5,fontName='Helvetica',textColor=colors.grey,alignment=TA_CENTER)),
                           'X' if val=='B' else '', 'X' if val=='R' else '', 'X' if val=='M' else '', 'X' if val=='NA' else ''])
        pct = int(datos_d.get(f'pct_{sec_key}',0) or 0)
        rc = VERDE if pct>=90 else (NARANJA if pct>=70 else ROJO)
        filas.append([Paragraph(f'<b>Resultado sección: {pct}%</b>',
                                 ParagraphStyle('res',fontSize=8,fontName='Helvetica-Bold',textColor=BLANCO,alignment=TA_CENTER)),'','','','',''])
        return Table(filas, colWidths=[45*mm,11*mm,8*mm,8*mm,8*mm,8*mm],
                     style=TableStyle([('GRID',(0,0),(-1,-2),0.3,colors.lightgrey),
                                        ('BACKGROUND',(0,0),(-1,0),AZUL_CLARO),('FONTSIZE',(0,0),(-1,-1),7.5),
                                        ('ALIGN',(1,0),(-1,-1),'CENTER'),('ROWPADDING',(0,0),(-1,-1),2),
                                        ('SPAN',(0,-1),(-1,-1)),('BACKGROUND',(0,-1),(-1,-1),rc),
                                        ('TOPPADDING',(0,-1),(-1,-1),5),('BOTTOMPADDING',(0,-1),(-1,-1),5)]))

    secciones_items = [
        ('5. Motor','motor',[('Arranque','arranque'),('Radiador','radiador'),('Carter motor','carter_motor'),('Carter de caja','carter_caja'),('Caja velocidades','caja_vel'),('Soporte de caja','soporte_caja'),('Soporte de motor','soporte_motor'),('Ruido de motor','ruido_motor'),('Ventiladores','ventiladores'),('Mangueras','mangueras'),('Embrague','embrague'),('Sincronización','sincronizacion'),('Correa repartición','correa_rep'),('Correa accesorios','correa_acc'),('Polea y tensores','polea_tensores')]),
        ('6. Sistema Eléctrico','electrico',[('Panorámico delantero','panoramico_del'),('Panorámico trasero','panoramico_tra'),('Vidrios','vidrios'),('Plumillas','plumillas'),('Farola derecha','farola_der'),('Farola izquierda','farola_izq'),('Exploradora derecha','explor_der'),('Exploradora izquierda','explor_izq'),('Stop derecho','stop_der'),('Stop izquierdo','stop_izq'),('Bocina','bocina'),('Batería','bateria'),('Fusibles','fusibles'),('Alternador','alternador'),('Luz porta placa','luz_placa')]),
        ('7. Fuga de Fluidos','fluidos',[('Fuga aceite motor','aceite_motor'),('Fuga aceite caja','aceite_caja'),('Fuga refrigerante','refrigerante'),('Fuga líquido freno','liq_freno'),('Fuga dirección hidráulica','dir_hidraulica'),('Fuga diferencial','diferencial')]),
        ('8. Llantas','llantas',[('Llantas delanteras','del'),('Llantas traseras','tra'),('Llanta de repuesto','repuesto'),('Desgaste','desgaste'),('Estado rines','rines')]),
        ('9. Tren Delantero y Suspensión','tren',[('Pastillas de freno','pastillas'),('Discos de freno','discos'),('Amortiguadores del.','amort_del'),('Amortiguadores tra.','amort_tra'),('Puntas de ejes','puntas'),('Axiales','axiales'),('Terminales','terminales'),('Rótulas','rotulas'),('Bujes','bujes'),('Tijeras','tijeras'),('Caja de dirección','caja_dir'),('Rodamientos','rodamientos')]),
        ('10. Interior del Vehículo','interior',[('Calefacción','calefaccion'),('Aire acondicionado','aire_ac'),('Cinturones','cinturones'),('Asiento derecho','asiento_der'),('Asiento izquierdo','asiento_izq'),('Asientos traseros','asientos_tra'),('Condición del techo','techo'),('Manija techo','manija_techo'),('Luz de techo','luz_techo'),('Carteras','carteras'),('Millare','millare'),('Alfombras','alfombras'),('Cabeceros','cabeceros'),('Tapetes','tapetes')]),
        ('11. Prueba de Ruta','ruta',[('Aceleración','aceleracion'),('Maniobrabilidad','maniobrabilidad'),('Ángulo de alineación','alineacion'),('Condición de frenado','frenado'),('Condición del embrague','embrague'),('Relación caja-motor','relacion_caja'),('Vibraciones','vibraciones')]),
    ]

    for i in range(0, len(secciones_items), 2):
        lt, lk, li = secciones_items[i]
        story.append(Spacer(1, 2*mm))
        if i+1 < len(secciones_items):
            rt, rk, ri = secciones_items[i+1]
            story.append(Table([[seccion_header(lt,styles),Spacer(5*mm,1),seccion_header(rt,styles)],
                                  [tabla_items(lk,li,datos),Spacer(5*mm,1),tabla_items(rk,ri,datos)]],
                                 colWidths=[90*mm,5*mm,90*mm],
                                 style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP')])))
        else:
            story.append(Table([[seccion_header(lt,styles)],[tabla_items(lk,li,datos)]],colWidths=[W]))

    # Sección 12: Accesorios
    story.append(Spacer(1,2*mm))
    story.append(seccion_header('12. Accesorios', styles))
    accesorios = datos.get('accesorios', [])
    acc_rows = []
    for j in range(0, max(len(accesorios),1), 3):
        row = [Paragraph(f'• {accesorios[j+k]}',styles['normal_sm']) if j+k<len(accesorios) else '' for k in range(3)]
        acc_rows.append(row)
    story.append(Table(acc_rows, colWidths=[62*mm,62*mm,61*mm],
                        style=TableStyle([('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('ROWPADDING',(0,0),(-1,-1),3)])))

    # Observaciones
    story.append(Spacer(1,3*mm))
    story.append(seccion_header('Observaciones del Perito', styles))
    nivel_colores = {'AVISO': colors.HexColor('#f39c12'), 'PENDIENTE': AZUL, 'INMEDIATO': ROJO}
    obs_rows = [[Paragraph('Nivel',styles['titulo_seccion']),Paragraph('Acción recomendada',styles['titulo_seccion'])]]
    for obs in datos.get('observaciones', []):
        nivel = obs.get('nivel','AVISO').upper()
        bg = nivel_colores.get(nivel, NARANJA)
        obs_rows.append([Paragraph(nivel,ParagraphStyle('',fontSize=7.5,fontName='Helvetica-Bold',textColor=BLANCO,backColor=bg,alignment=TA_CENTER)),
                          Paragraph(obs.get('descripcion',''),styles['normal_sm'])])
    story.append(Table(obs_rows, colWidths=[30*mm,155*mm],
                        style=TableStyle([('BACKGROUND',(0,0),(-1,0),AZUL),('GRID',(0,0),(-1,-1),0.3,colors.lightgrey),('ROWPADDING',(0,0),(-1,-1),3)])))

    # Calificación final
    story.append(Spacer(1,4*mm))
    story.append(Table([[Paragraph('CALIFICACIÓN FINAL TOTAL:',ParagraphStyle('',fontSize=14,fontName='Helvetica-Bold',textColor=NEGRO,alignment=TA_RIGHT)),
                          Paragraph(f'<b>{pct_tot}%</b>',ParagraphStyle('',fontSize=24,fontName='Helvetica-Bold',textColor=AZUL,alignment=TA_CENTER))]],
                        colWidths=[140*mm,45*mm],
                        style=TableStyle([('BOX',(0,0),(-1,-1),1.5,AZUL),('ROWPADDING',(0,0),(-1,-1),6),('VALIGN',(0,0),(-1,-1),'MIDDLE')])))
    story.append(Spacer(1,4*mm))
    story.append(Paragraph("Aviso legal: Este diagnóstico automotriz está basado exclusivamente en criterios técnicos y va destinado únicamente al solicitante. No se podrá usar como medio que garantice la comercialización, ni relación contractual alguna del vehículo.", styles['pie']))

    doc.build(story)
    buffer.seek(0)
    return buffer

# ─── Ruta PDF ─────────────────────────────────────────────────────────────────

@app.route('/generar_pdf', methods=['POST'])
@login_required
def generar_pdf_route():
    if request.content_type and 'multipart/form-data' in request.content_type:
        datos = json.loads(request.form.get('datos', '{}'))
        pdf_adjunto = request.files.get('pdf_antecedentes')
        servicio_id = request.form.get('servicio_id')
    else:
        datos = request.get_json() or {}
        pdf_adjunto = None
        servicio_id = datos.pop('servicio_id', None)

    buffer_informe = generar_pdf(datos)
    placa = datos.get('placa', 'vehiculo')

    if pdf_adjunto and pdf_adjunto.filename:
        try:
            writer = PdfWriter()
            for page in PdfReader(buffer_informe).pages:
                writer.add_page(page)
            for page in PdfReader(io.BytesIO(pdf_adjunto.read())).pages:
                writer.add_page(page)
            buf_final = io.BytesIO()
            writer.write(buf_final); buf_final.seek(0)
            pdf_bytes = buf_final.getvalue()
            nombre = f"informe_completo_{placa}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        except Exception:
            buffer_informe.seek(0)
            pdf_bytes = buffer_informe.getvalue()
            nombre = f"peritaje_{placa}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    else:
        buffer_informe.seek(0)
        pdf_bytes = buffer_informe.getvalue()
        nombre = f"peritaje_{placa}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    if servicio_id:
        ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_db()
        conn.execute("UPDATE servicios SET pdf_data=?, estado='completado', paso_actual=11, datos_json=?, fecha_actualizacion=? WHERE id=?",
                     (pdf_bytes, json.dumps(datos), ahora, int(servicio_id)))
        conn.commit(); conn.close()

    return send_file(io.BytesIO(pdf_bytes), mimetype='application/pdf',
                     as_attachment=True, download_name=nombre)

if __name__ == '__main__':
    app.run(debug=True, port=5050)
