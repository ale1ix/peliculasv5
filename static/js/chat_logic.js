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
    // NUEVO: Variable para guardar el estado de la moderación
    let moderationState = { muted: [], banned: [] };

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
        const tempUser = `user_${Math.floor(Math.random() * 10000)}`;
        sessionStorage.setItem('cinesaUsername', tempUser);
        socket.emit('join', { 'session_id': sessionId, 'room_type': roomType, 'username': tempUser });
    }

    function sendUsername() {
        const username = elements.usernameInput.value.trim();
        if (username) {
            sessionStorage.setItem('cinesaUsername', username);
            socket.emit('set_username', { session_id: sessionId, room_type: roomType, username: username });
            if(elements.usernameArea) elements.usernameArea.style.display = 'none';
            if(elements.inputArea) elements.inputArea.style.display = 'flex';
            elements.chatInput.focus();
        }
    }

    function sendChatMessage() {
        const message = elements.chatInput.value.trim();
        if (message && !elements.chatInput.disabled) {
            socket.emit('chat_message', { session_id: sessionId, room_type: roomType, message });
            elements.chatInput.value = '';
        }
    }

    function addMessageToChat(msg, isHistory = false) {
        if (!elements.chatBox) return;
        const wrapper = document.createElement('div');
        // NUEVO: Asignar un ID al contenedor del mensaje para poder borrarlo
        wrapper.id = msg.id;
        wrapper.classList.add('message-wrapper', msg.sid === mySid ? 'my-message' : 'other-message');

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

        if (isAdmin && msg.sid !== mySid) {
            const modMenu = document.createElement('span');
            modMenu.classList.add('mod-menu');
            modMenu.textContent = '⋮';
            // Pasamos el objeto de mensaje completo al menú
            modMenu.onclick = (e) => { e.stopPropagation(); showModMenu(e.target, msg); };
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
    }

    // NUEVO: Función de menú de moderación completamente rediseñada
    function showModMenu(target, msg) {
        const existing = document.getElementById('mod-popup-menu');
        if(existing) existing.remove();

        const menu = document.createElement('div');
        menu.id = 'mod-popup-menu';
        const rect = target.getBoundingClientRect();
        menu.style.position = 'absolute';
        menu.style.left = `${window.scrollX + rect.left - 80}px`; // Ajustado para que no se salga
        menu.style.top = `${window.scrollY + rect.bottom + 5}px`;
        menu.style.zIndex = '1001';
        menu.style.background = 'var(--bg-light)';
        menu.style.border = '1px solid var(--border-color)';
        menu.style.borderRadius = '6px';
        menu.style.padding = '5px';
        menu.style.boxShadow = '0 5px 15px rgba(0,0,0,0.3)';

        const createButton = (text, onClick) => {
            const btn = document.createElement('button');
            btn.textContent = text;
            btn.style.cssText = 'background: none; border: none; color: var(--text-primary); cursor: pointer; display: block; width: 100%; text-align: left; padding: 5px 10px;';
            btn.onmouseover = () => btn.style.backgroundColor = 'var(--bg-dark)';
            btn.onmouseout = () => btn.style.backgroundColor = 'transparent';
            btn.onclick = () => { onClick(); menu.remove(); };
            return btn;
        };

        // Lógica de Mute/Unmute
        if (moderationState.muted.includes(msg.username)) {
            menu.appendChild(createButton('Quitar Silencio', () => socket.emit('admin_action', {session_id: sessionId, room_type: roomType, action: 'unmute_user', username: msg.username})));
        } else {
            menu.appendChild(createButton('Silenciar', () => socket.emit('admin_action', {session_id: sessionId, room_type: roomType, action: 'mute_user', username: msg.username})));
        }

        // Lógica de Ban/Unban (solo se puede desbanear desde el panel de admin, aquí solo banear)
        if (!moderationState.banned.includes(msg.username)) {
            menu.appendChild(createButton('Banear', () => {
                if(confirm(`¿Banear permanentemente a '${msg.username}' de esta sesión?`)) {
                    socket.emit('admin_action', {session_id: sessionId, room_type: roomType, action: 'ban_user', username: msg.username, sid: msg.sid});
                }
            }));
        }

        // Botón de borrar mensaje
        const deleteBtn = createButton('Borrar Mensaje', () => socket.emit('admin_action', {session_id: sessionId, room_type: roomType, action: 'delete_message', message_id: msg.id}));
        deleteBtn.style.color = 'var(--danger)';
        menu.appendChild(deleteBtn);

        document.body.appendChild(menu);
        setTimeout(() => document.body.addEventListener('click', () => menu.remove(), { once: true }), 0);
    }

    // --- 3. EVENT LISTENERS ---
    elements.joinBtn?.addEventListener('click', sendUsername);
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
        elements.chatBox.innerHTML = '';
        data.chat_history.forEach(msg => addMessageToChat(msg, true));
        updateChatState(data.state.chat_enabled);
        if (elements.chatBox) elements.chatBox.scrollTop = elements.chatBox.scrollHeight;
    });

    socket.on('new_message', (msg) => addMessageToChat(msg));
    socket.on('system_message', (data) => addSystemMessage(data.text));
    socket.on('chat_state_change', (data) => updateChatState(data.chat_enabled));
    
    // NUEVO: Listener para borrar un mensaje del DOM
    socket.on('message_deleted', (data) => {
        const msgElement = document.getElementById(data.id);
        if (msgElement) {
            msgElement.style.transition = 'opacity 0.3s, transform 0.3s';
            msgElement.style.opacity = '0';
            msgElement.style.transform = 'scale(0.8)';
            setTimeout(() => msgElement.remove(), 300);
        }
    });

    // NUEVO: Listener para actualizar el estado de moderación
    socket.on('user_list_update', (data) => {
        if (isAdmin) {
            moderationState = data;
            console.log('Estado de moderación actualizado:', moderationState);
        }
    });

    socket.on('force_disconnect', (data) => {
        alert(data.reason);
        window.location.href = '/';
    });

    // --- 5. INITIALIZATION ---
    window.chatApp = { socket, join: sendUsername };
    autoJoinWithTempName();
});