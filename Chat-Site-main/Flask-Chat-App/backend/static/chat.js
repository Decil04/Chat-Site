let lastMessageTime = 0;

function createMessageElement(message, isOwn) {
    const div = document.createElement('div');
    div.className = `message mb-3 p-3 rounded ${isOwn ? 'own-message bg-secondary' : 'bg-dark'}`;
    
    const header = document.createElement('div');
    header.className = 'mb-1 d-flex justify-content-between';
    
    const user = document.createElement('small');
    user.className = 'text-muted';
    user.textContent = `User ${message.user_id}`;
    
    const time = document.createElement('small');
    time.className = 'text-muted ms-2';
    time.textContent = new Date(message.timestamp).toLocaleTimeString();
    
    header.appendChild(user);
    header.appendChild(time);
    
    const content = document.createElement('div');
    content.textContent = message.message;
    
    div.appendChild(header);
    div.appendChild(content);
    
    return div;
}

async function fetchMessages() {
    try {
        const response = await fetch(`/api/messages/${ROOM_ID}`);
        const data = await response.json();
        
        if (!response.ok) throw new Error(data.error);
        
        const container = document.getElementById('messages');
        const shouldScroll = container.scrollTop + container.clientHeight >= container.scrollHeight - 50;
        
        data.messages.forEach(message => {
            if (new Date(message.timestamp) > lastMessageTime) {
                const isOwn = message.user_id === getCookie('session').slice(0, 6);
                container.appendChild(createMessageElement(message, isOwn));
                lastMessageTime = new Date(message.timestamp);
            }
        });
        
        if (shouldScroll) {
            container.scrollTop = container.scrollHeight;
        }
    } catch (error) {
        showError(error.message);
    }
}

async function sendMessage(message) {
    try {
        const response = await fetch(`/api/messages/${ROOM_ID}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ message })
        });
        
        const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        
        await fetchMessages();
    } catch (error) {
        showError(error.message);
    }
}

function showError(message) {
    const toast = document.getElementById('error-toast');
    toast.querySelector('.toast-body').textContent = message;
    new bootstrap.Toast(toast).show();
}

function copyRoomLink() {
    navigator.clipboard.writeText(window.location.href)
        .then(() => alert('Room link copied to clipboard!'))
        .catch(() => alert('Failed to copy room link'));
}

function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
}

document.getElementById('message-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = document.getElementById('message-input');
    const message = input.value.trim();
    
    if (message) {
        input.value = '';
        await sendMessage(message);
    }
});

// Initial load and polling
fetchMessages();
setInterval(fetchMessages, 2000);
