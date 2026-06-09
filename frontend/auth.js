// frontend/auth.js

window.API_BASE = window.API_BASE || 'https://jarvis-backend.onrender.com';


function getToken() {
    return localStorage.getItem('jarvis_token');
}

function saveToken(token) {
    localStorage.setItem('jarvis_token', token);
}

function removeToken() {
    localStorage.removeItem('jarvis_token');
}

function getAuthHeader() {
    const token = getToken();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

async function verifyAuth() {
    const token = getToken();
    if (!token) {
        redirectToLogin();
        return false;
    }

    try {
        const response = await fetch(`${window.API_BASE}/auth/verify`, {
            method: 'POST',
            headers: {
                ...getAuthHeader(),
                'Content-Type': 'application/json'
            }
        });

        if (!response.ok) {
            removeToken();
            redirectToLogin();
            return false;
        }

        const data = await response.json();
        return data.valid;
    } catch (error) {
        console.error('Authentication verification failed:', error);
        return false;
    }
}

function redirectToLogin() {
    if (!window.location.pathname.endsWith('login.html')) {
        window.location.href = 'login.html';
    }
}

function logout() {
    removeToken();
    redirectToLogin();
}
