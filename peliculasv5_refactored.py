# ===================================================================
# LA CLAVE MAESTRA CONTRA EL BLOQUEO
# ===================================================================
import eventlet
eventlet.monkey_patch()

# Ahora sí, el resto de las importaciones
import os
import uuid
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, join_room, leave_room, emit, disconnect
from werkzeug.utils import secure_filename
from moviepy import VideoFileClip

# --- 1. CONFIGURACIÓN ---
UPLOAD_FOLDER = 'static/videos'
ASSETS_FOLDER = 'static/assets'
DATABASE_FILE = 'cinesa_schedule.db'
INTRO_VIDEO = 'assets/intro.mp4'
OUTRO_VIDEO = 'assets/outro.mp4'
ADMIN_PASSWORD = '4321'
STATUS_SCHEDULED = 'scheduled'
STATUS_VESTIBULE = 'vestibule'
STATUS_ACTIVE = 'active'
STATUS_FINISHED = 'finished'
VESTIBULE_OPEN_MINUTES = 15
POST_SHOW_CLOSE_MINUTES = 5
EMPTY_ROOM_CLOSE_MINUTES = 10

# --- App Initialization ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'este-es-el-secreto-definitivo-ahora-si')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
socketio = SocketIO(app, async_mode='eventlet')

# --- Estado Global en Memoria ---
active_sessions = {}
sessions_lock = threading.Lock()

# --- 2. GESTIÓN DE BASE DE DATOS ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, movie_title TEXT NOT NULL, movie_file TEXT NOT NULL,
            poster_file TEXT, playlist TEXT NOT NULL, scheduled_time TIMESTAMP NOT NULL,
            status TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(ASSETS_FOLDER, exist_ok=True)
    os.makedirs('static/posters', exist_ok=True)

# --- 3. FUNCIONES AUXILIARES ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'mp4', 'webm', 'ogg'}

def allowed_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'webp', 'gif'}

def get_available_movies():
    movies = {}
    if not os.path.exists(UPLOAD_FOLDER): return movies
    for filename in os.listdir(UPLOAD_FOLDER):
        if allowed_file(filename): movies[os.path.splitext(filename)[0]] = filename
    return movies

def is_admin():
    return session.get('admin_logged_in', False)

# --- 4. LÓGICA DEL PROYECCIONISTA Y CICLO DE VIDA ---
class VirtualProjectionist(threading.Thread):
    def __init__(self, session_id):
        super().__init__()
        self.session_id = session_id
        self.stop_event = threading.Event()
        self.daemon = True

    def run(self):
        print(f"[Projectionist {self.session_id}]: Hilo iniciado.")
        while not self.stop_event.is_set():
            socketio.sleep(1)
            with sessions_lock:
                if self.session_id not in active_sessions:
                    self.stop()
                    continue
                
                s_data = active_sessions[self.session_id]
                state = s_data['state']

                # Solo avanzar el tiempo si la sesión está ACTIVA y en PLAY
                if state.get('status') == STATUS_ACTIVE and state.get('playing'):
                    state['time'] += 1
                    current_video_info = s_data['playlist'][state['current_video_index']]
                    duration = current_video_info.get('duration', 3600)
                    
                    if state['time'] >= duration:
                        self.next_video()
                    else:
                        # Solo enviar pulso si hay alguien en la sala de cine
                        if s_data['users']['watch_room']:
                           socketio.emit('sync_pulse', {'time': state['time']}, to=self.session_id)

        print(f"[Projectionist {self.session_id}]: Hilo detenido.")

    def next_video(self):
        # Esta función ya se llama desde dentro de un 'with sessions_lock'
        s_data = active_sessions[self.session_id]
        state = s_data['state']
        state['current_video_index'] += 1
        
        if state['current_video_index'] >= len(s_data['playlist']):
            self.finish_session()
        else:
            state['time'] = 0
            state['playing'] = True # Continuar reproduciendo automáticamente
            next_video = s_data['playlist'][state['current_video_index']]
            print(f"[Projectionist {self.session_id}]: Cambiando al siguiente vídeo: {next_video['src']}")
            socketio.emit('play_next_video', {'state': state}, to=self.session_id)

    def finish_session(self):
        # Esta función ya se llama desde dentro de un 'with sessions_lock'
        s_data = active_sessions[self.session_id]
        state = s_data['state']
        state['status'] = STATUS_FINISHED
        state['playing'] = False
        s_data['close_timer_start'] = time.time()
        print(f"[Projectionist {self.session_id}]: Sesión finalizada.")
        socketio.emit('session_finished', to=self.session_id)
        self.stop() # Detener el hilo del proyeccionista

    def stop(self):
        self.stop_event.set()

def schedule_monitor():
    """
    Hilo en segundo plano que gestiona el ciclo de vida de las sesiones:
    1. Auto-inicia sesiones cuya hora programada ha llegado.
    2. Cierra sesiones vacías o que han finalizado hace tiempo.
    """
    print("[Monitor]: El monitor de sesiones está activo.")
    while True:
        socketio.sleep(5) # Revisar cada 5 segundos
        now = datetime.now()
        sessions_to_start = []
        sessions_to_close = []

        with sessions_lock:
            for session_id, data in list(active_sessions.items()):
                # Lógica de auto-inicio
                if data['state'].get('status') == STATUS_VESTIBULE and now >= data.get('scheduled_time'):
                    sessions_to_start.append(session_id)

                # Lógica de cierre de sala vacía
                is_empty = not data['users']['vestibule'] and not data['users']['watch_room']
                if is_empty:
                    if 'empty_timer_start' not in data:
                        data['empty_timer_start'] = time.time()
                    elif time.time() - data.get('empty_timer_start', 0) > EMPTY_ROOM_CLOSE_MINUTES * 60:
                        sessions_to_close.append(session_id)
                elif 'empty_timer_start' in data:
                    del data['empty_timer_start']
                
                # Lógica de cierre post-función
                if data['state'].get('status') == STATUS_FINISHED and time.time() - data.get('close_timer_start', 0) > POST_SHOW_CLOSE_MINUTES * 60:
                    sessions_to_close.append(session_id)
        
        # Ejecutar acciones fuera del bucle principal para evitar problemas de concurrencia
        for session_id in set(sessions_to_start):
            print(f"[Monitor]: La hora programada para la sesión {session_id} ha llegado. Iniciando...")
            start_projection(session_id)

        for session_id in set(sessions_to_close):
            print(f"[Monitor]: Cerrando sesión inactiva/finalizada {session_id}")
            with sessions_lock:
                if session_id in active_sessions:
                    if 'projectionist_thread' in active_sessions[session_id]:
                        active_sessions[session_id]['projectionist_thread'].stop()
                    del active_sessions[session_id]
            conn = get_db_connection()
            conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (STATUS_FINISHED, session_id))
            conn.commit(); conn.close()
            socketio.emit('admin_panel_update')

# --- 5. RUTAS FLASK (Sin cambios importantes) ---
@app.route('/')
def index():
    return redirect(url_for('cartelera'))

@app.route('/cartelera')
def cartelera():
    conn = get_db_connection()
    sessions_db_raw = conn.execute("SELECT * FROM sessions WHERE status != ? ORDER BY scheduled_time ASC", (STATUS_FINISHED,)).fetchall()
    conn.close()
    sessions_processed = []
    now = datetime.now()
    with sessions_lock:
        for s_db in sessions_db_raw:
            session_dict = dict(s_db)
            scheduled_time = datetime.fromisoformat(session_dict['scheduled_time'])
            open_time = scheduled_time - timedelta(minutes=VESTIBULE_OPEN_MINUTES)
            session_dict['scheduled_time_obj'] = scheduled_time
            # Determinar el estado a mostrar
            status_in_memory = active_sessions.get(session_dict['id'], {}).get('state', {}).get('status')
            if status_in_memory:
                session_dict['display_status'] = status_in_memory
            else:
                if now >= scheduled_time: session_dict['display_status'] = STATUS_VESTIBULE
                elif now >= open_time: session_dict['display_status'] = STATUS_VESTIBULE
                else: session_dict['display_status'] = STATUS_SCHEDULED
            sessions_processed.append(session_dict)
    return render_template("cartelera.html", sessions=sessions_processed)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_admin(): return redirect(url_for('admin_panel'))
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_panel'))
        else:
            return render_template("admin_login.html", error="Contraseña incorrecta")
    return render_template("admin_login.html")

@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('login'))

@app.route('/admin')
def admin_panel():
    if not is_admin(): return redirect(url_for('login'))
    conn = get_db_connection()
    sessions_db = conn.execute("SELECT * FROM sessions ORDER BY scheduled_time DESC").fetchall()
    conn.close()
    all_sessions_data = []
    with sessions_lock:
        for s_db in sessions_db:
            data = dict(s_db)
            if data['id'] in active_sessions:
                active_data = active_sessions[data['id']]
                data['current_status_in_memory'] = active_data['state'].get('status', 'N/A')
                data['user_count_vestibule'] = len(active_data['users']['vestibule'])
                data['user_count_watch_room'] = len(active_data['users']['watch_room'])
            else:
                data['current_status_in_memory'] = data['status']
                data['user_count_vestibule'] = 0
                data['user_count_watch_room'] = 0
            all_sessions_data.append(data)
    return render_template("admin_panel.html", movies=get_available_movies(), sessions=all_sessions_data)

@app.route('/upload', methods=['POST'])
def upload_file():
    if not is_admin(): return redirect(url_for('login'))
    movie_file = request.files.get('movie_file'); poster_file = request.files.get('poster_file')
    if not all([movie_file, poster_file, allowed_file(movie_file.filename), allowed_image_file(poster_file.filename)]):
        return redirect(url_for('admin_panel'))
    movie_filename = secure_filename(movie_file.filename)
    movie_file.save(os.path.join(app.config['UPLOAD_FOLDER'], movie_filename))
    poster_ext = poster_file.filename.rsplit('.', 1)[1].lower()
    poster_filename = f"{os.path.splitext(movie_filename)[0]}.{poster_ext}"
    poster_file.save(os.path.join('static/posters', secure_filename(poster_filename)))
    return redirect(url_for('admin_panel'))

@app.route('/schedule_session', methods=['POST'])
def schedule_session():
    if not is_admin(): return redirect(url_for('login'))
    movie_title = request.form.get('movie_title')
    scheduled_time_str = request.form.get('scheduled_time')
    movie_file = get_available_movies().get(movie_title)

    if not all([movie_title, scheduled_time_str, movie_file]):
        return redirect(url_for('admin_panel'))

    # <-- INICIO DE LA LÓGICA CORREGIDA -->
    try:
        # Obtiene la ruta completa al archivo de vídeo
        video_path = os.path.join(app.config['UPLOAD_FOLDER'], movie_file)
        # Usa moviepy para abrir el vídeo y obtener su duración en segundos
        with VideoFileClip(video_path) as clip:
            movie_duration = int(clip.duration)
        print(f"Duración detectada para '{movie_file}': {movie_duration} segundos.")
    except Exception as e:
        print(f"ADVERTENCIA: No se pudo obtener la duración de '{movie_file}'. Error: {e}. Usando 3600s por defecto.")
        movie_duration = 3600
    # <-- FIN DE LA LÓGICA CORREGIDA -->

    poster_file = None
    for ext in ['jpg', 'png', 'jpeg', 'webp', 'gif']:
        p_file = f"{movie_title}.{ext}"
        if os.path.exists(os.path.join('static/posters', p_file)):
            poster_file = p_file
            break

    # Ahora la playlist usa la duración correcta
    playlist = [
        {'src': INTRO_VIDEO, 'duration': 5},
        {'src': f'videos/{movie_file}', 'duration': movie_duration},
        {'src': OUTRO_VIDEO, 'duration': 5} # He puesto 5s como dijiste
    ]

    conn = get_db_connection()
    conn.execute("INSERT INTO sessions (id, movie_title, movie_file, poster_file, playlist, scheduled_time, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (str(uuid.uuid4())[:8], movie_title, movie_file, poster_file, json.dumps(playlist), scheduled_time_str, STATUS_SCHEDULED))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/delete_session/<session_id>', methods=['POST'])
def delete_session(session_id):
    if not is_admin(): return redirect(url_for('login'))
    with sessions_lock:
        if session_id in active_sessions:
            if 'projectionist_thread' in active_sessions[session_id]: active_sessions[session_id]['projectionist_thread'].stop()
            del active_sessions[session_id]
    conn = get_db_connection(); conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,)); conn.commit(); conn.close()
    socketio.emit('admin_panel_update'); return redirect(url_for('admin_panel'))

@app.route('/vestibulo/<session_id>')
def vestibulo(session_id):
    # Cargar sesión o crearla si es la primera vez que se entra
    with sessions_lock:
        if session_id in active_sessions:
            s_data = active_sessions[session_id]
            if s_data['state']['status'] == STATUS_ACTIVE: return redirect(url_for('watch_room', session_id=session_id))
            time_to_start = (s_data['scheduled_time'] - datetime.now()).total_seconds()
            return render_template("vestibulo.html", session_id=session_id, session_data=s_data, time_to_start=max(0, time_to_start), is_admin=is_admin())

    # Si no está en memoria, la cargamos de la DB
    conn = get_db_connection(); s_db = conn.execute("SELECT * FROM sessions WHERE id = ? AND status != ?", (session_id, STATUS_FINISHED)).fetchone(); conn.close()
    if s_db:
        scheduled_time = datetime.fromisoformat(s_db['scheduled_time'])
        open_time = scheduled_time - timedelta(minutes=VESTIBULE_OPEN_MINUTES)
        if datetime.now() >= open_time:
            with sessions_lock:
                if session_id not in active_sessions:
                    print(f"[Session]: Creando sesión '{session_id}' en memoria desde la base de datos.")
                    active_sessions[session_id] = {
                        'db_id': s_db['id'], 'movie_title': s_db['movie_title'], 'poster_file': s_db['poster_file'],
                        'playlist': json.loads(s_db['playlist']), 'scheduled_time': scheduled_time,
                        'users': {'vestibule': {}, 'watch_room': {}},
                        'chat': {'vestibule': [], 'watch_room': []},
                        'state': {'status': STATUS_VESTIBULE, 'chat_enabled': True},
                        'muted_users': set(),
                        'banned_users': {} # username: sid (o True si no tenemos sid en el momento del ban)
                    }
                    # Cargar mutes/bans persistentes si existieran (Fase 2)
                    # Ejemplo:
                    # mutes_bans_json = s_db['moderation_data']
                    # if mutes_bans_json:
                    #     mod_data = json.loads(mutes_bans_json)
                    #     active_sessions[session_id]['muted_users'] = set(mod_data.get('muted', []))
                    #     active_sessions[session_id]['banned_users'] = mod_data.get('banned', {})
                    conn = get_db_connection(); conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (STATUS_VESTIBULE, s_db['id'])); conn.commit(); conn.close()
                    socketio.emit('admin_panel_update')
            return redirect(url_for('vestibulo', session_id=session_id))
    return render_template("error.html", message="El vestíbulo no está abierto o la sesión no existe.")

@app.route('/watch/<session_id>')
def watch_room(session_id):
    # Simplemente renderiza la plantilla. La lógica de unión y estado se maneja por sockets.
    return render_template("watch_room.html", session_id=session_id, is_admin=is_admin())

# --- 6. LÓGICA DE PROYECCIÓN Y SOCKET.IO ---
def start_projection(session_id):
    with sessions_lock:
        if session_id not in active_sessions or active_sessions[session_id]['state']['status'] != STATUS_VESTIBULE:
            print(f"[Transition Aborted]: Intento de iniciar sesión {session_id}, pero su estado es '{active_sessions.get(session_id, {}).get('state', {}).get('status')}' en lugar de 'vestibule'.")
            return
        
        print(f"[Transition]: Iniciando proyección para sesión {session_id}. Actualizando estado a ACTIVE.")
        s_data = active_sessions[session_id]
        
        # 1. Actualizar estado a ACTIVO
        s_data['state'].update({
            'status': STATUS_ACTIVE, 'playing': False, 'time': 0, 'current_video_index': 0
        })
        
        # 2. Notificar a los clientes del vestíbulo para que se muevan
        socketio.emit('force_start_projection', to=f"{session_id}_vestibule")

        # 3. Iniciar el hilo del proyeccionista
        projectionist = VirtualProjectionist(session_id)
        projectionist.start()
        s_data['projectionist_thread'] = projectionist
        
        # 4. Programar el inicio de la reproducción tras una cuenta atrás
        def delayed_start(sid):
            # Emitir a la sala principal (donde estarán los de watch_room)
            socketio.emit('playback_starting', {'countdown': 5}, to=sid)
            socketio.sleep(5)
            with sessions_lock:
                if sid in active_sessions and active_sessions[sid]['state']['status'] == STATUS_ACTIVE:
                    print(f"[Playback {sid}]: Cuenta atrás finalizada. Estableciendo 'playing' a True.")
                    active_sessions[sid]['state']['playing'] = True
                    # Notificar a todos los clientes del cambio de estado final (ahora con playing=True)
                    socketio.emit('state_change', active_sessions[sid]['state'], to=sid)
        
        socketio.start_background_task(target=delayed_start, sid=session_id)
        
        # 5. Actualizar la base de datos y el panel de admin
        conn = get_db_connection()
        conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (STATUS_ACTIVE, session_id))
        conn.commit(); conn.close()
        socketio.emit('admin_panel_update')
        print(f"[Transition]: El estado de la sesión {session_id} ahora es {s_data['state']}.")

# --- 7. MANEJADORES DE EVENTOS SOCKET.IO ---
@socketio.on('join')
def on_join(data):
    session_id = data.get('session_id'); room_type = data.get('room_type')
    username = data.get('username', 'Anónimo').strip()[:25]; sid = request.sid
    if not all([session_id, room_type, username]): return

    with sessions_lock:
        if session_id not in active_sessions:
            emit('force_disconnect', {'reason': 'La sesión ha finalizado.'}); return
        
        s = active_sessions[session_id]
        print(f"[Join]: Usuario '{username}' (sid: {sid}) intentando unirse a '{room_type}' en sesión '{session_id}'.")

        # Verificar si el usuario está baneado
        if username in s.get('banned_users', {}):
            print(f"[Join Denied]: Usuario '{username}' está baneado de la sesión '{session_id}'. Desconectando.")
            emit('force_disconnect', {'reason': 'Estás baneado de esta sesión.'})
            disconnect(sid) # Asegurarse de que el usuario es desconectado
            return

        # Asignar a la sala de socket.io correcta
        socket_room_id = f"{session_id}_{room_type}" if room_type == 'vestibule' else session_id
        join_room(socket_room_id)
        
        # Eliminar al usuario de otras salas de esta sesión si existiera
        if sid in s['users']['vestibule']: del s['users']['vestibule'][sid]
        if sid in s['users']['watch_room']: del s['users']['watch_room'][sid]
        s['users'][room_type][sid] = username

        emit('system_message', {'text': f"'{username}' se ha unido."}, to=socket_room_id, include_self=False)
        
        print(f"[State Sent]: Enviando 'initial_state' a '{username}'. Estado actual: {s['state']}")
        # Enviar también la lista de usuarios muteados/baneados para que el cliente admin pueda actualizar su UI
        user_lists_for_client = {
            'muted': list(s.get('muted_users', set())),
            'banned': list(s.get('banned_users', {}).keys())
        }
        emit('initial_state', {
            'my_sid': sid, 'my_username': username,
            'chat_history': s['chat'].get(room_type, []),
            'state': s['state'],
            'playlist': s['playlist'],
            'user_lists': user_lists_for_client # Nuevo: enviar listas de moderación
        })
        socketio.emit('admin_panel_update')
        # Enviar actualización de listas de usuarios a todos en la sala (para admins)
        socketio.emit('user_list_update', user_lists_for_client, to=socket_room_id)

@socketio.on('chat_message')
def on_chat_message(data):
    session_id = data.get('session_id'); room_type = data.get('room_type')
    message_text = data.get('message', '').strip(); sid = request.sid
    if not all([session_id, room_type, message_text]): return

    with sessions_lock:
        if session_id not in active_sessions: return
        s = active_sessions[session_id]
        
        if not s['state'].get('chat_enabled', True): return
        
        username = s['users'].get(room_type, {}).get(sid)
        # Comprobar si el usuario está muteado o baneado
        if not username or username in s.get('muted_users', set()) or username in s.get('banned_users', {}):
            # Podríamos enviar un mensaje al usuario informándole que está silenciado,
            # pero por ahora simplemente ignoramos el mensaje.
            print(f"[Chat Denied]: Mensaje de '{username}' (muteado/baneado) ignorado en sesión '{session_id}'.")
            return

        # Añadir ID único y timestamp al mensaje
        msg = {
            'id': str(uuid.uuid4()), # ID único para el mensaje
            'sid': sid,
            'username': username,
            'text': message_text,
            'timestamp': time.time()
        }
        s['chat'][room_type].append(msg)
        
        socket_room_id = f"{session_id}_{room_type}" if room_type == 'vestibule' else session_id
        emit('new_message', msg, to=socket_room_id)

@socketio.on('admin_action')
def on_admin_action(data):
    if not is_admin(): return
    session_id = data.get('session_id'); action = data.get('action')
    print(f"[Admin Action]: Recibida acción '{action}' para la sesión '{session_id}' con datos: {data}")
    
    with sessions_lock:
        if session_id not in active_sessions: return
        s = active_sessions[session_id]
        
        room_type = data.get('room_type', 'watch_room') # Default a watch_room si no se especifica
        socket_room_id = f"{session_id}_{room_type}" if room_type == 'vestibule' else session_id

        user_lists_for_client = lambda: { # Funcion para obtener listas actualizadas
            'muted': list(s.get('muted_users', set())),
            'banned': list(s.get('banned_users', {}).keys())
        }

        if action == 'force_start':
            start_projection(session_id)
        
        elif action == 'state_change':
            if s['state']['status'] == STATUS_ACTIVE:
                s['state'].update(data.get('state', {}))
                socketio.emit('state_change', s['state'], to=session_id)
                print(f"[Admin Action SUCCESS]: Estado de {session_id} cambiado a {s['state']}")
            else:
                print(f"[Admin Action FAIL]: Se intentó cambiar el estado de la sesión {session_id}, pero no está 'active'. Estado actual: {s['state']['status']}")
        
        elif action == 'toggle_chat':
            s['state']['chat_enabled'] = not s['state'].get('chat_enabled', True)
            status_text = "activado" if s['state']['chat_enabled'] else "desactivado"
            socketio.emit('chat_state_change', {'chat_enabled': s['state']['chat_enabled']}, to=socket_room_id)
            socketio.emit('system_message', {'text': f"Un administrador ha {status_text} el chat."}, to=socket_room_id)

        elif action == 'mute_user':
            username = data.get('username')
            if username:
                s['muted_users'].add(username)
                print(f"[Admin Action]: Usuario '{username}' muteado en sesión '{session_id}'.")
                socketio.emit('system_message', {'text': f"'{username}' ha sido silenciado por un administrador."}, to=socket_room_id)
                # Notificar al usuario específico que ha sido muteado (opcional, pero buena UX)
                user_sid_to_notify = next((sid for sid, uname in s['users'].get(room_type, {}).items() if uname == username), None)
                if user_sid_to_notify:
                    socketio.emit('personal_notification', {'text': 'Has sido silenciado en este chat.'}, to=user_sid_to_notify)
                socketio.emit('user_list_update', user_lists_for_client(), to=socket_room_id)


        elif action == 'unmute_user':
            username = data.get('username')
            if username and username in s['muted_users']:
                s['muted_users'].remove(username)
                print(f"[Admin Action]: Usuario '{username}' desmuteado en sesión '{session_id}'.")
                socketio.emit('system_message', {'text': f"A '{username}' se le ha quitado el silencio."}, to=socket_room_id)
                user_sid_to_notify = next((sid for sid, uname in s['users'].get(room_type, {}).items() if uname == username), None)
                if user_sid_to_notify:
                    socketio.emit('personal_notification', {'text': 'Se te ha quitado el silencio en este chat.'}, to=user_sid_to_notify)
                socketio.emit('user_list_update', user_lists_for_client(), to=socket_room_id)

        elif action == 'ban_user':
            username = data.get('username')
            sid_to_ban = data.get('sid') # SID del usuario a banear, puede ser None si se banea "offline"

            if username:
                s['banned_users'][username] = sid_to_ban if sid_to_ban else True # Guardar True si no hay SID
                print(f"[Admin Action]: Usuario '{username}' baneado de la sesión '{session_id}'.")
                socketio.emit('system_message', {'text': f"'{username}' ha sido baneado de la sesión por un administrador."}, to=socket_room_id)

                # Desconectar al usuario si está online (tiene SID y está en la lista de usuarios)
                actual_sid_to_disconnect = None
                if sid_to_ban and sid_to_ban in s['users'].get(room_type, {}):
                     actual_sid_to_disconnect = sid_to_ban
                else: # Intentar encontrar el SID por username si no se proveyó o no coincide
                    actual_sid_to_disconnect = next((sid_iter for sid_iter, uname in s['users'].get(room_type, {}).items() if uname == username), None)

                if actual_sid_to_disconnect:
                    print(f"Intentando desconectar SID {actual_sid_to_disconnect} perteneciente a {username}")
                    # Emitir primero, luego desconectar. El disconnect() puede ser problemático dentro del lock de socketio
                    # si la desconexión es síncrona y trata de modificar la misma estructura que está lockeada.
                    # Es más seguro hacerlo fuera del lock o de forma asíncrona.
                    # Por ahora, lo dejamos así, pero es un punto a revisar si da problemas de deadlock.
                    socketio.emit('force_disconnect', {'reason': 'Has sido baneado de esta sesión por un administrador.'}, to=actual_sid_to_disconnect)
                    socketio.server.disconnect(actual_sid_to_disconnect, namespace='/') # Usar el método del servidor para desconectar
                    print(f"SID {actual_sid_to_disconnect} ({username}) desconectado (o intento enviado).")
                else:
                    print(f"Usuario '{username}' baneado pero no encontrado en línea para desconexión inmediata.")


                socketio.emit('user_list_update', user_lists_for_client(), to=socket_room_id)

        elif action == 'unban_user': # Generalmente llamado desde el panel de admin
            username = data.get('username')
            if username and username in s['banned_users']:
                del s['banned_users'][username]
                print(f"[Admin Action]: Usuario '{username}' desbaneado de la sesión '{session_id}'.")
                socketio.emit('system_message', {'text': f"'{username}' ha sido desbaneado de la sesión."}, to=socket_room_id)
                socketio.emit('user_list_update', user_lists_for_client(), to=socket_room_id)

        elif action == 'delete_message':
            message_id = data.get('message_id')
            room_chat = s['chat'].get(room_type)
            if message_id and room_chat is not None:
                original_len = len(room_chat)
                s['chat'][room_type] = [msg for msg in room_chat if msg.get('id') != message_id]
                if len(s['chat'][room_type]) < original_len:
                    print(f"[Admin Action]: Mensaje '{message_id}' borrado de la sesión '{session_id}', sala '{room_type}'.")
                    socketio.emit('message_deleted', {'id': message_id}, to=socket_room_id)
                else:
                    print(f"[Admin Action Warning]: No se encontró el mensaje '{message_id}' para borrar en '{session_id}', sala '{room_type}'.")

        elif action == 'get_moderation_lists': # Para el panel de admin
            # Este evento debería ser emitido solo al admin que lo solicita
            requesting_admin_sid = request.sid
            emit('moderation_lists_update', user_lists_for_client(), to=requesting_admin_sid)

@socketio.on('request_state_sync')
def on_request_state(data):
    """
    Un cliente pide el estado actual porque su vídeo se ha parado.
    """
    session_id = data.get('session_id')
    sid = request.sid
    with sessions_lock:
        if session_id in active_sessions:
            s = active_sessions[session_id]
            print(f"[Sync Request]: El usuario {sid} pide resincronización. Enviando estado actual.")
            # Emitir solo al usuario que lo pidió
            emit('state_change', s['state'], to=sid)

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    sid_found_and_removed = False
    session_id_of_disconnect = None
    room_type_of_disconnect = None
    username_disconnected = "N/A"

    with sessions_lock:
        for session_id_iter, s_data in active_sessions.items():
            for r_type in ['vestibule', 'watch_room']:
                if sid in s_data['users'][r_type]:
                    username_disconnected = s_data['users'][r_type].pop(sid)
                    session_id_of_disconnect = session_id_iter
                    room_type_of_disconnect = r_type
                    sid_found_and_removed = True
                    print(f"Usuario '{username_disconnected}' (sid: {sid}) desconectado de {r_type} en sesión {session_id_iter}.")
                    break
            if sid_found_and_removed:
                break

    if sid_found_and_removed and session_id_of_disconnect and room_type_of_disconnect:
        # Emitir fuera del lock si es posible, o asegurar que el lock no cause problemas
        # La sala de socket.io correcta para el mensaje de "ha salido"
        socket_room_id_for_message = f"{session_id_of_disconnect}_{room_type_of_disconnect}" if room_type_of_disconnect == 'vestibule' else session_id_of_disconnect

        # Verificar si la sesión aún existe antes de emitir (podría haber sido eliminada)
        with sessions_lock:
            if session_id_of_disconnect in active_sessions:
                socketio.emit('system_message', {'text': f"'{username_disconnected}' ha salido."}, to=socket_room_id_for_message)

                # Actualizar listas de moderación para los admins en esa sala
                s = active_sessions[session_id_of_disconnect]
                user_lists = {
                    'muted': list(s.get('muted_users', set())),
                    'banned': list(s.get('banned_users', {}).keys())
                }
                socketio.emit('user_list_update', user_lists, to=socket_room_id_for_message)

        socketio.emit('admin_panel_update') # Actualizar el panel de admin
    else:
        print(f"SID {sid} se desconectó, pero no se encontró en ninguna sesión activa/sala conocida.")

# --- 8. INICIO DE LA APLICACIÓN ---
if __name__ == '__main__':
    init_db()
    # Iniciar el monitor de sesiones en segundo plano
    scheduler_thread = threading.Thread(target=schedule_monitor)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    print("===============================================================")
    print("== INICIANDO SERVIDOR CON EVENTLET                           ==")
    print("== Ejecutando con 'python peliculasv5_refactored.py'         ==")
    print("== Servidor disponible en http://127.0.0.1:5000              ==")
    print("===============================================================")
    
    socketio.run(app, host='0.0.0.0', port=5000, use_reloader=False)