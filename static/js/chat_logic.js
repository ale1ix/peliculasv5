// static/js/chat_logic.js

document.addEventListener('DOMContentLoaded', () => {
    const chatContainer = document.getElementById('chat-container');
    if (!chatContainer) return;

    // --- 1. CONFIG & ELEMENTS ---
    const socket = io();
    const sessionId = chatContainer.dataset.sessionId;
    const roomType = chatContainer.dataset.roomType;
    const isAdmin = chatContainer.dataset.isAdmin === 'true';
    let mySid = '';
    let myUsername = ''; // Guardar el nombre de usuario propio
    let moderationState = { muted: [], banned: [] }; // Estado de moderación

    const elements = {
        headerText: document.getElementById('chat-header-text'),
        chatBox: document.getElementById('chat-box'),
        inputArea: document.getElementById('chat-input-area'),
        usernameArea: document.getElementById('chat-username-area'),
        usernameInput: document.getElementById('username-input'),
        joinBtn: document.getElementById('join-btn'),
        chatInput: document.getElementById('chat-input'),
        sendBtn: document.getElementById('send-btn'),
        toggleChatBtn: isAdmin ? document.getElementById('toggle-chat-btn') : null
    };

    // --- 2. CORE FUNCTIONS ---
    function autoJoinWithTempName() {
        const storedUsername = sessionStorage.getItem('cinesaUsername');
        myUsername = storedUsername || `user_${Math.floor(Math.random() * 10000)}`;
        if (!storedUsername) {
            sessionStorage.setItem('cinesaUsername', myUsername);
        }

        elements.usernameInput.value = myUsername; // Poner el nombre en el input
        elements.usernameArea.style.display = 'none'; // Ocultar input de nombre
        elements.inputArea.style.display = 'flex'; // Mostrar input de chat
        elements.chatInput.focus();

        socket.emit('join', { 'session_id': sessionId, 'room_type': roomType, 'username': myUsername });
        updateHeaderText(myUsername);
    }

    function sendUsername() { // Esta función ahora se usa si el usuario quiere cambiar su nombre.
        const newUsername = elements.usernameInput.value.trim();
        if (newUsername && newUsername !== myUsername) {
            // Aquí podrías emitir un evento 'change_username' si el backend lo soporta.
            // Por ahora, solo actualizamos localmente y para la próxima sesión.
            myUsername = newUsername;
            sessionStorage.setItem('cinesaUsername', myUsername);
            addSystemMessage(`Tu nombre de usuario ahora es: ${myUsername}. (El cambio completo tomará efecto al reunirte).`);
            updateHeaderText(myUsername);
        }
        if(elements.usernameArea) elements.usernameArea.style.display = 'none';
        if(elements.inputArea) elements.inputArea.style.display = 'flex';
        elements.chatInput.focus();
    }

    function updateHeaderText(username) {
        if (elements.headerText) {
            elements.headerText.textContent = `Chat (${roomType}) - ${username}`;
        }
    }

    function sendChatMessage() {
        const message = elements.chatInput.value.trim();
        if (message && !elements.chatInput.disabled && myUsername) { // Asegurarse que myUsername esté seteado
            socket.emit('chat_message', { session_id: sessionId, room_type: roomType, message: message, username: myUsername });
            elements.chatInput.value = '';
        }
    }

    function addMessageToChat(msg, isHistory = false) {
        if (!elements.chatBox || !msg || !msg.username) return; // Comprobación de msg y msg.username
        const wrapper = document.createElement('div');
        // Asignar un ID al contenedor del mensaje para poder borrarlo, si el msg tiene id
        if (msg.id) {
            wrapper.id = `msg-${msg.id}`;
        }
        wrapper.classList.add('message-wrapper', msg.username === myUsername ? 'my-message' : 'other-message');

        const avatar = document.createElement('div');
        avatar.classList.add('avatar');
        avatar.textContent = msg.username.substring(0, 2).toUpperCase();

        const messageContent = document.createElement('div');
        messageContent.classList.add('message-content');
        const messageHeader = document.createElement('div');
        messageHeader.classList.add('message-header');
        const usernameEl = document.createElement('span');
        usernameEl.classList.add('message-username');
        usernameEl.textContent = msg.username;
        const timeEl = document.createElement('span');
        timeEl.classList.add('message-time');
        timeEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute:'2-digit' });

        messageHeader.appendChild(usernameEl);
        messageHeader.appendChild(timeEl);

        // Solo mostrar menú de moderación si es admin Y el mensaje no es propio
        if (isAdmin && msg.username !== myUsername) {
            const modMenu = document.createElement('span');
            modMenu.classList.add('mod-menu');
            modMenu.textContent = '⋮'; // O un icono SVG
            modMenu.setAttribute('aria-label', 'Opciones de moderación');
            modMenu.setAttribute('role', 'button');
            modMenu.onclick = (e) => {
                e.stopPropagation();
                showModMenu(e.target, msg);
            };
            messageHeader.appendChild(modMenu);
        }

        const bubble = document.createElement('div');
        bubble.classList.add('message-bubble');
        bubble.textContent = msg.text;
        messageContent.appendChild(messageHeader);
        messageContent.appendChild(bubble);
        wrapper.appendChild(avatar);
        wrapper.appendChild(messageContent);
        elements.chatBox.appendChild(wrapper);

        if (!isHistory) {
            elements.chatBox.scrollTop = elements.chatBox.scrollHeight;
        }
    }

    function addSystemMessage(text) {
        if (!elements.chatBox) return;
        const el = document.createElement('div');
        el.classList.add('system-message');
        el.textContent = text;
        elements.chatBox.appendChild(el);
        elements.chatBox.scrollTop = elements.chatBox.scrollHeight;
    }

    function updateChatState(isEnabled) {
        elements.chatInput.disabled = !isEnabled;
        elements.sendBtn.disabled = !isEnabled;
        elements.chatInput.placeholder = isEnabled ? 'Escribe un mensaje...' : 'El chat está desactivado.';
        if (elements.toggleChatBtn) {
            elements.toggleChatBtn.checked = isEnabled;
        }
        // Si el usuario actual está en la lista de muteados, deshabilitar su propio input
        if (myUsername && moderationState.muted.includes(myUsername)) {
            elements.chatInput.disabled = true;
            elements.sendBtn.disabled = true;
            elements.chatInput.placeholder = 'Estás silenciado en este chat.';
        }
    }

    function showModMenu(target, msg) { // msg es el objeto del mensaje: {id, sid, username, text}
        const existingMenu = document.getElementById('mod-popup-menu');
        if (existingMenu) existingMenu.remove();

        const menu = document.createElement('div');
        menu.id = 'mod-popup-menu';
        const rect = target.getBoundingClientRect();
        menu.style.position = 'absolute';
        menu.style.left = `${window.scrollX + rect.left - 100}px`; // Ajustar para que no se salga
        menu.style.top = `${window.scrollY + rect.bottom + 5}px`;
        menu.style.zIndex = '1001';
        menu.style.background = 'var(--bg-medium)';
        menu.style.border = '1px solid var(--border-color)';
        menu.style.borderRadius = '6px';
        menu.style.padding = '5px 0'; // Padding vertical
        menu.style.boxShadow = '0 5px 15px rgba(0,0,0,0.3)';
        menu.style.minWidth = '120px';


        const createButton = (text, onClick, isDestructive = false) => {
            const btn = document.createElement('button');
            btn.textContent = text;
            btn.style.cssText = `
                background: none; border: none;
                color: ${isDestructive ? 'var(--danger)' : 'var(--text-primary)'};
                cursor: pointer; display: block; width: 100%;
                text-align: left; padding: 8px 15px; font-size: 0.9em;`;
            btn.onmouseover = () => btn.style.backgroundColor = 'var(--bg-light)';
            btn.onmouseout = () => btn.style.backgroundColor = 'transparent';
            btn.onclick = (e) => {
                e.stopPropagation();
                onClick();
                menu.remove();
            };
            return btn;
        };

        // Mute/Unmute User
        if (moderationState.muted.includes(msg.username)) {
            menu.appendChild(createButton('Quitar Silencio', () => {
                socket.emit('admin_action', { session_id: sessionId, room_type: roomType, action: 'unmute_user', username: msg.username });
            }));
        } else {
            menu.appendChild(createButton('Silenciar Usuario', () => {
                socket.emit('admin_action', { session_id: sessionId, room_type: roomType, action: 'mute_user', username: msg.username });
            }));
        }

        // Ban User (Solo opción de Ban, Unban desde panel admin)
        if (!moderationState.banned.includes(msg.username)) {
            menu.appendChild(createButton('Banear Usuario', () => {
                if (confirm(`¿Estás seguro de que quieres banear a '${msg.username}' de esta sesión? Esta acción es permanente para la sesión.`)) {
                    socket.emit('admin_action', { session_id: sessionId, room_type: roomType, action: 'ban_user', username: msg.username, sid: msg.sid });
                }
            }, true)); // isDestructive = true
        }

        // Separador
        const separator = document.createElement('hr');
        separator.style.cssText = 'border: none; border-top: 1px solid var(--border-color); margin: 5px 0;';
        menu.appendChild(separator);

        // Delete Message
        if (msg.id) { // Solo si el mensaje tiene un ID se puede borrar
            menu.appendChild(createButton('Borrar Mensaje', () => {
                socket.emit('admin_action', { session_id: sessionId, room_type: roomType, action: 'delete_message', message_id: msg.id });
            }, true)); // isDestructive = true
        }

        document.body.appendChild(menu);

        // Cerrar menú si se hace clic fuera
        const closeMenuHandler = (event) => {
            if (!menu.contains(event.target)) {
                menu.remove();
                document.body.removeEventListener('click', closeMenuHandler);
            }
        };
        // Usar setTimeout para que el listener no se active con el mismo clic que abrió el menú
        setTimeout(() => document.body.addEventListener('click', closeMenuHandler), 0);
    }


    // --- 3. EVENT LISTENERS ---
    elements.joinBtn?.addEventListener('click', sendUsername); // Aunque ahora autoJoinWithTempName lo llama internamente
    elements.usernameInput?.addEventListener('keyup', (e) => { if (e.key === 'Enter') sendUsername(); });
    elements.sendBtn?.addEventListener('click', sendChatMessage);
    elements.chatInput?.addEventListener('keyup', (e) => { if (e.key === 'Enter') sendChatMessage(); });

    if (isAdmin && elements.toggleChatBtn) {
        elements.toggleChatBtn.addEventListener('click', () => {
            socket.emit('admin_action', { session_id: sessionId, room_type: roomType, action: 'toggle_chat' });
        });
    }

    // --- 4. SOCKET.IO HANDLERS ---
    socket.on('initial_state', (data) => {
        mySid = data.my_sid;
        if(data.my_username) myUsername = data.my_username; // Sincronizar username si el backend lo envía
        updateHeaderText(myUsername);

        elements.chatBox.innerHTML = ''; // Limpiar chat
        data.chat_history.forEach(msg => addMessageToChat(msg, true));

        if (data.user_lists) { // Actualizar listas de moderación si vienen en el estado inicial
            moderationState = data.user_lists;
        }
        updateChatState(data.state.chat_enabled); // Aplicar estado del chat y estado de mute propio

        if (elements.chatBox) elements.chatBox.scrollTop = elements.chatBox.scrollHeight;
    });

    socket.on('new_message', (msg) => addMessageToChat(msg));
    socket.on('system_message', (data) => addSystemMessage(data.text));
    
    socket.on('chat_state_change', (data) => { // Actualiza el estado general del chat y el estado de mute propio
        updateChatState(data.chat_enabled);
    });

    socket.on('message_deleted', (data) => { // data = {id: message_id_to_delete}
        const msgElement = document.getElementById(`msg-${data.id}`);
        if (msgElement) {
            msgElement.style.transition = 'opacity 0.3s ease, transform 0.3s ease, height 0.3s ease, padding 0.3s ease, margin 0.3s ease';
            msgElement.style.opacity = '0';
            msgElement.style.transform = 'scale(0.9)';
            msgElement.style.paddingTop = '0';
            msgElement.style.paddingBottom = '0';
            msgElement.style.marginTop = '0';
            msgElement.style.marginBottom = '0';
            msgElement.style.height = '0px';
            setTimeout(() => msgElement.remove(), 300);
        }
    });

    socket.on('user_list_update', (data) => { // data = {muted: [...], banned: [...]}
        moderationState = data;
        // Si el usuario actual es admin, puede que quiera ver esta info.
        // También, si el usuario actual fue (des)muteado, actualizar su input.
        updateChatState(elements.toggleChatBtn ? elements.toggleChatBtn.checked : true);
        console.log('Estado de moderación actualizado:', moderationState);
    });

    socket.on('personal_notification', (data) => { // Para notificar al usuario si fue muteado/desmuteado
        addSystemMessage(data.text); // Mostrar como mensaje del sistema
        // Actualizar estado del input si la notificación es sobre mute
        if (data.text.toLowerCase().includes('silenciado')) {
            updateChatState(elements.toggleChatBtn ? elements.toggleChatBtn.checked : true);
        }
    });

    socket.on('force_disconnect', (data) => {
        // No llamar a socket.disconnect() aquí, ya que el servidor lo maneja.
        // Llamarlo aquí puede causar un evento 'disconnect' duplicado o prematuro.
        alert(`Desconectado: ${data.reason}`);
        // Limpiar datos de sesión local relevantes si es necesario
        sessionStorage.removeItem('cinesaUsername'); // Por ejemplo
        window.location.href = '/cartelera';
    });

    socket.on('disconnect', (reason) => {
        // Este evento se dispara cuando el cliente se desconecta por cualquier motivo
        // (solicitado por el servidor, pérdida de red, cierre de pestaña, etc.)
        addSystemMessage(`Has sido desconectado del chat (${reason}).`);
        elements.chatInput.disabled = true;
        elements.sendBtn.disabled = true;
        elements.chatInput.placeholder = 'Desconectado.';
        // No redirigir automáticamente aquí, podría ser una desconexión temporal.
        // La redirección por baneo ya se maneja en 'force_disconnect'.
    });

    // --- 5. INITIALIZATION ---
    // Exportar globalmente el socket para que watch_room.js pueda usarlo
    window.chatApp = {
        socket: socket,
        join: autoJoinWithTempName // Exponer la función de unirse que ahora maneja el username
    };

    // Iniciar conexión y unión al chat
    autoJoinWithTempName();
});