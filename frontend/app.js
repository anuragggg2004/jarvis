const API_BASE = window.API_BASE || 'https://jarvis-backend-gq1f.onrender.com';
const WS_BASE = 'wss://jarvis-backend-gq1f.onrender.com';




// App State
let state = {
    currentFilter: 'all', // all | favorite | unread | collection
    currentCollectionId: null,
    bookmarks: [],
    collections: [],
    currentConversationId: null,
    activeJobPolls: new Set()
};

// DOM Elements
const dbStatus = document.getElementById('dbStatus');
const redisStatus = document.getElementById('redisStatus');
const ollamaStatus = document.getElementById('ollamaStatus');

const statTotal = document.getElementById('statTotal');
const statFavorites = document.getElementById('statFavorites');
const statUnread = document.getElementById('statUnread');
const statTags = document.getElementById('statTags');

const chatMessages = document.getElementById('chatMessages');
const chatForm = document.getElementById('chatForm');
const chatInput = document.getElementById('chatInput');
const chkUseRag = document.getElementById('chkUseRag');
const btnNewChat = document.getElementById('btnNewChat');

const searchBar = document.getElementById('searchBar');
const btnClearSearch = document.getElementById('btnClearSearch');
const btnShowAddBookmark = document.getElementById('btnShowAddBookmark');
const bookmarksGrid = document.getElementById('bookmarksGrid');

const filterAll = document.getElementById('filterAll');
const filterFav = document.getElementById('filterFav');
const filterUnread = document.getElementById('filterUnread');
const collectionList = document.getElementById('collectionList');
const filterIndicator = document.getElementById('filterIndicator');
const filterName = document.getElementById('filterName');
const btnResetFilters = document.getElementById('btnResetFilters');

// Modals
const addBookmarkModal = document.getElementById('addBookmarkModal');
const addBookmarkForm = document.getElementById('addBookmarkForm');
const bookmarkCollectionSelect = document.getElementById('bookmarkCollection');

const createCollectionModal = document.getElementById('createCollectionModal');
const createCollectionForm = document.getElementById('createCollectionForm');
const btnCreateCollection = document.getElementById('btnCreateCollection');

// Details Panel
const detailPanelOverlay = document.getElementById('detailPanelOverlay');
const btnBackToGrid = document.getElementById('btnBackToGrid');
const btnDetailFavorite = document.getElementById('btnDetailFavorite');
const btnDetailRead = document.getElementById('btnDetailRead');
const btnDetailArchive = document.getElementById('btnDetailArchive');
const detailBody = document.getElementById('detailBody');

// Initialization
document.addEventListener('DOMContentLoaded', async () => {
    // 1. Verify Authentication
    const authenticated = await verifyAuth();
    if (!authenticated) return; // auth.js will redirect

    // 2. Fetch Initial System Data
    await checkHealth();
    await fetchStats();
    await fetchCollections();
    await fetchBookmarks();

    // Start background status updates
    setInterval(checkHealth, 30000); // Check health every 30s
    setInterval(fetchStats, 10000);  // Update stats counts every 10s

    // 3. Register Event Listeners
    registerEvents();
    
    // Render initial icons
    lucide.createIcons();
});

// Event Registration
function registerEvents() {
    // Filter click events
    filterAll.addEventListener('click', () => setFilter('all'));
    filterFav.addEventListener('click', () => setFilter('favorite'));
    filterUnread.addEventListener('click', () => setFilter('unread'));
    btnResetFilters.addEventListener('click', () => setFilter('all'));

    // Modal Triggers
    btnShowAddBookmark.addEventListener('click', () => openModal(addBookmarkModal));
    btnCreateCollection.addEventListener('click', () => openModal(createCollectionModal));

    // Modal Close
    document.querySelectorAll('.btn-close-modal').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            closeModal(addBookmarkModal);
            closeModal(createCollectionModal);
        });
    });

    // Form Submissions
    const btnUploadChromeHTML = document.getElementById('btnUploadChromeHTML');
    const chromeHTMLFileInput = document.getElementById('chromeHTMLFileInput');
    if (btnUploadChromeHTML && chromeHTMLFileInput) {
        btnUploadChromeHTML.addEventListener('click', () => {
            chromeHTMLFileInput.click();
        });
        chromeHTMLFileInput.addEventListener('change', handleChromeHTMLImport);
    }

    addBookmarkForm.addEventListener('submit', handleAddBookmark);

    createCollectionForm.addEventListener('submit', handleCreateCollection);
    chatForm.addEventListener('submit', handleChatSubmit);

    // Chat reset
    btnNewChat.addEventListener('click', resetChat);

    // Search events (Debounced hybrid search)
    let searchTimeout;
    searchBar.addEventListener('input', (e) => {
        const query = e.target.value.trim();
        if (query) {
            btnClearSearch.style.display = 'block';
        } else {
            btnClearSearch.style.display = 'none';
        }

        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            if (query) {
                performSearch(query);
            } else {
                fetchBookmarks();
            }
        }, 400);
    });

    btnClearSearch.addEventListener('click', () => {
        searchBar.value = '';
        btnClearSearch.style.display = 'none';
        fetchBookmarks();
    });

    // Details panel closing
    btnBackToGrid.addEventListener('click', () => {
        detailPanelOverlay.classList.remove('active');
    });
    detailPanelOverlay.addEventListener('click', (e) => {
        if (e.target === detailPanelOverlay) {
            detailPanelOverlay.classList.remove('active');
        }
    });
}

// Health and System Check
async function checkHealth() {
    try {
        const res = await fetch(`${API_BASE}/health`);
        const data = await res.json();
        
        updateStatusIndicator(dbStatus, data.database === 'connected');
        updateStatusIndicator(redisStatus, data.redis === 'connected');
        updateStatusIndicator(ollamaStatus, data.status === 'operational');
    } catch {
        updateStatusIndicator(dbStatus, false);
        updateStatusIndicator(redisStatus, false);
        updateStatusIndicator(ollamaStatus, false);
    }
}

function updateStatusIndicator(element, isConnected) {
    if (element) {
        if (isConnected) {
            element.classList.add('active');
        } else {
            element.classList.remove('active');
        }
    }
}

// Stats fetcher
async function fetchStats() {
    try {
        const res = await fetch(`${API_BASE}/stats`, { headers: getAuthHeader() });
        if (res.status === 401) return logout();
        const data = await res.json();
        
        statTotal.innerText = data.total_bookmarks || 0;
        statFavorites.innerText = data.favorites || 0;
        statUnread.innerText = data.unread || 0;
        statTags.innerText = data.unique_tags || 0;
    } catch (err) {
        console.error('Stats error:', err);
    }
}

// Collections fetcher
async function fetchCollections() {
    try {
        const res = await fetch(`${API_BASE}/collections`, { headers: getAuthHeader() });
        const data = await res.json();
        state.collections = data;
        
        renderCollections();
    } catch (err) {
        console.error('Collections error:', err);
    }
}

// Bookmarks fetcher
async function fetchBookmarks() {
    showLoading();
    try {
        let url = `${API_BASE}/bookmarks?limit=100`;
        
        if (state.currentFilter === 'unread') {
            url += '&status=unread';
        } else if (state.currentFilter === 'collection') {
            url += `&collection_id=${state.currentCollectionId}`;
        }

        const res = await fetch(url, { headers: getAuthHeader() });
        let data = await res.json();

        // Manual filtering for Favorites as FastAPI query options don't cover it directly
        if (state.currentFilter === 'favorite') {
            data = data.filter(b => b.is_favorite);
        }

        state.bookmarks = data;
        renderBookmarks();
    } catch (err) {
        console.error('Bookmarks error:', err);
        bookmarksGrid.innerHTML = `<p class="error-msg">Failed to connect to database nodes, Sir.</p>`;
    }
}

// Render Bookmarks Grid
function renderBookmarks() {
    bookmarksGrid.innerHTML = '';
    
    if (state.bookmarks.length === 0) {
        bookmarksGrid.innerHTML = `
            <div class="loading-spinner">
                <i data-lucide="inbox" style="width: 48px; height: 48px; stroke-width: 1;"></i>
                <p>No records found in this vector space, Sir.</p>
            </div>
        `;
        lucide.createIcons();
        return;
    }

    state.bookmarks.forEach(b => {
        const card = document.createElement('div');
        card.className = `bookmark-card ${b.read_status === 'unread' ? 'unread-status' : ''}`;
        card.dataset.id = b.id;

        // Custom details modal on card click
        card.addEventListener('click', (e) => {
            // Avoid opening modal if action button is clicked
            if (e.target.closest('.btn-card-action') || e.target.closest('a')) return;
            openDetailsPanel(b);
        });

        const domainUrl = b.domain || 'node';
        const faviconUrl = `https://www.google.com/s2/favicons?sz=64&domain=${b.url}`;

        card.innerHTML = `
            <div class="card-top">
                <div class="card-fav-domain">
                    <img src="${faviconUrl}" class="card-domain-favicon" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2210%22 height=%2210%22><rect width=%2210%22 height=%2210%22 fill=%22%23628ea6%22/></svg>'">
                    <span>${domainUrl}</span>
                </div>
                <span class="card-badge">${b.category || 'General'}</span>
            </div>
            <div class="card-middle">
                <h4>${escapeHtml(b.title || 'Untitled Node')}</h4>
                <p>${escapeHtml(b.summary || 'Analyzing site components...')}</p>
            </div>
            <div class="card-bottom">
                <div class="card-tags">
                    ${(b.tags || []).slice(0, 3).map(t => `<span class="card-tag">#${escapeHtml(t)}</span>`).join(' ')}
                </div>
                <div class="card-actions">
                    <button class="btn-card-action ${b.is_favorite ? 'favorite-active' : ''}" onclick="toggleCardFavorite('${b.id}', ${b.is_favorite})" title="Toggle Favorite">
                        <i data-lucide="star"></i>
                    </button>
                    <button class="btn-card-action" onclick="toggleCardRead('${b.id}', '${b.read_status}')" title="Mark Read/Unread">
                        <i data-lucide="book-open"></i>
                    </button>
                </div>
            </div>
        `;
        bookmarksGrid.appendChild(card);
    });

    lucide.createIcons();
}

// Render Collections Tree
function renderCollections() {
    collectionList.innerHTML = '';
    bookmarkCollectionSelect.innerHTML = '<option value="">No Collection</option>';

    state.collections.forEach(c => {
        // Render tree lists
        const li = document.createElement('li');
        if (state.currentFilter === 'collection' && state.currentCollectionId === c.id) {
            li.className = 'active-collection';
        }
        li.dataset.id = c.id;
        li.innerHTML = `
            <i data-lucide="${c.icon || 'folder'}" style="color: ${c.color || '#00e5ff'}"></i>
            <span>${escapeHtml(c.name)}</span>
        `;
        
        li.addEventListener('click', () => {
            setFilter('collection', c.id, c.name);
        });
        
        collectionList.appendChild(li);

        // Populate Modal Select Option
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.innerText = c.name;
        bookmarkCollectionSelect.appendChild(opt);
    });
    
    lucide.createIcons();
}

// Filters Settings
function setFilter(filterType, collectionId = null, collectionName = '') {
    state.currentFilter = filterType;
    state.currentCollectionId = collectionId;

    // Toggle active status in lists
    document.querySelectorAll('.filter-list li').forEach(li => li.classList.remove('active-filter'));
    document.querySelectorAll('.collection-list li').forEach(li => li.classList.remove('active-collection'));

    if (filterType === 'all') {
        filterAll.classList.add('active-filter');
        filterIndicator.style.display = 'none';
    } else if (filterType === 'favorite') {
        filterFav.classList.add('active-filter');
        filterIndicator.style.display = 'flex';
        filterName.innerText = 'Favorites';
    } else if (filterType === 'unread') {
        filterUnread.classList.add('active-filter');
        filterIndicator.style.display = 'flex';
        filterName.innerText = 'Unread';
    } else if (filterType === 'collection') {
        filterIndicator.style.display = 'flex';
        filterName.innerText = `Collection: ${collectionName}`;
        const activeNode = document.querySelector(`.collection-list li[data-id="${collectionId}"]`);
        if (activeNode) activeNode.classList.add('active-collection');
    }

    fetchBookmarks();
}

// Hybrid Search executor
async function performSearch(query) {
    showLoading();
    try {
        const res = await fetch(`${API_BASE}/search?q=${encodeURIComponent(query)}`, { headers: getAuthHeader() });
        const data = await res.json();
        
        const ids = data.results.map(r => r.bookmark_id);
        
        if (ids.length === 0) {
            state.bookmarks = [];
            renderBookmarks();
            return;
        }

        const bmRes = await fetch(`${API_BASE}/bookmarks?limit=100`, { headers: getAuthHeader() });
        const allBm = await bmRes.json();
        
        // Filter and sort allBookmarks based on order returned from hybrid search
        state.bookmarks = ids.map(id => allBm.find(b => b.id === id)).filter(b => b !== undefined);
        renderBookmarks();

    } catch (err) {
        console.error('Search error:', err);
    }
}

// Handle Add Bookmark
async function handleAddBookmark(e) {
    e.preventDefault();
    const url = document.getElementById('bookmarkUrl').value.trim();
    const title = document.getElementById('bookmarkTitle').value.trim();
    const tagsRaw = document.getElementById('bookmarkTags').value.trim();
    const collectionId = bookmarkCollectionSelect.value;

    const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : [];

    closeModal(addBookmarkModal);
    addBookmarkForm.reset();

    // Generate a temporary job card
    const tempId = `temp-${Date.now()}`;
    const domain = url.includes('//') ? url.split('/')[2].replace('www.', '') : url;
    
    const card = document.createElement('div');
    card.id = tempId;
    card.className = 'bookmark-card job-pending-card';
    card.innerHTML = `
        <div class="card-top">
            <div class="card-fav-domain">
                <i data-lucide="loader" class="spinner" style="width: 14px; height: 14px;"></i>
                <span>${domain}</span>
            </div>
            <span class="card-badge">Scraping</span>
        </div>
        <div class="card-middle">
            <h4>Processing resource link...</h4>
            <p>Scraping HTML elements and preparing text tokens...</p>
        </div>
        <div class="card-bottom">
            <div class="card-tags"></div>
            <span class="card-tag">Initializing</span>
        </div>
    `;
    bookmarksGrid.insertBefore(card, bookmarksGrid.firstChild);
    lucide.createIcons();

    try {
        const payload = { url, tags, source: 'manual' };
        if (collectionId) {
            payload.collection_ids = [collectionId];
        }

        const res = await fetch(`${API_BASE}/bookmarks`, {
            method: 'POST',
            headers: {
                ...getAuthHeader(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        if (res.status === 409) {
            card.remove();
            alert('This resource has already been archived in your databanks, Sir.');
            return;
        }

        const data = await res.json();
        
        // Upgrade card visual to processing
        card.className = 'bookmark-card job-processing-card';
        card.querySelector('h4').innerText = 'Enriching with Local LLM...';
        card.querySelector('p').innerText = 'Calling llama3.2 to write summarizations, category, and tags...';

        // Poll job status
        pollJob(data.id, tempId);

    } catch (err) {
        console.error('Add bookmark error:', err);
        card.remove();
        alert('I ran into a scraping subsystem failure, Sir.');
    }
}

// Ingestion Job Polling Loop
async function pollJob(jobId, tempCardId) {
    if (state.activeJobPolls.has(jobId)) return;
    state.activeJobPolls.add(jobId);

    const interval = setInterval(async () => {
        try {
            const res = await fetch(`${API_BASE}/ingestion/${jobId}`, { headers: getAuthHeader() });
            const job = await res.json();
            
            if (job.status === 'completed') {
                clearInterval(interval);
                state.activeJobPolls.delete(jobId);
                
                const card = document.getElementById(tempCardId);
                if (card) card.remove();
                
                await fetchBookmarks();
                await fetchStats();
                
                notifyJarvis("Resource node processed successfully, Sir.");
            } else if (job.status === 'failed') {
                clearInterval(interval);
                state.activeJobPolls.delete(jobId);
                
                const card = document.getElementById(tempCardId);
                if (card) card.remove();
                
                alert(`Ingestion Pipeline Failure: ${job.error_message}`);
                await fetchBookmarks();
            }
        } catch (err) {
            console.error('Job polling error:', err);
            clearInterval(interval);
            state.activeJobPolls.delete(jobId);
        }
    }, 2000);
}

// Handle Create Collection
async function handleCreateCollection(e) {
    e.preventDefault();
    const name = document.getElementById('collectionName').value.trim();
    const description = document.getElementById('collectionDesc').value.trim();
    const icon = document.getElementById('collectionIcon').value;
    const color = document.getElementById('collectionColor').value;

    closeModal(createCollectionModal);
    createCollectionForm.reset();

    try {
        const res = await fetch(`${API_BASE}/collections`, {
            method: 'POST',
            headers: {
                ...getAuthHeader(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ name, description, icon, color })
        });
        
        if (res.ok) {
            await fetchCollections();
        }
    } catch (err) {
        console.error('Create collection error:', err);
    }
}

// Handle Chrome Bookmarks HTML Import
async function handleChromeHTMLImport(e) {
    const file = e.target.files[0];
    if (!file) return;

    closeModal(addBookmarkModal);

    const reader = new FileReader();
    reader.onload = async function(event) {
        const text = event.target.result;
        
        // Match all HREF attributes and anchor text in HTML
        const regex = /<a\s+(?:[^>]*?\s+)?href="([^"]*)"[^>]*>(.*?)<\/a>/gi;
        let match;
        const links = [];
        
        while ((match = regex.exec(text)) !== null) {
            const url = match[1];
            const title = match[2];
            if (url && (url.startsWith('http://') || url.startsWith('https://'))) {
                links.push({ url, title });
            }
        }

        if (links.length === 0) {
            alert("No valid bookmark links resolved from the uploaded HTML file, Sir.");
            return;
        }

        const confirmImport = confirm(`System found ${links.length} bookmarks. Proceed to import them in bulk into your JARVIS storage?`);
        if (!confirmImport) return;

        alert(`Initiated batch scraper for ${links.length} nodes. JARVIS will process them in the background. Please check the logs/status board.`);

        // Loop through all links and make non-blocking POST requests to queue ingestion jobs
        for (const link of links) {
            try {
                fetch(`${API_BASE}/bookmarks`, {
                    method: 'POST',
                    headers: {
                        ...getAuthHeader(),
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        url: link.url,
                        title: link.title,
                        source: 'import'
                    })
                });
                // Add a brief delay to prevent overloading Redis/Postgres instantly
                await new Promise(r => setTimeout(r, 150));
            } catch (err) {
                console.error(`Failed to queue bookmark ${link.url}:`, err);
            }
        }
        
        // Refresh bookmarks after starting the batch
        setTimeout(fetchBookmarks, 1000);
    };

    reader.readAsText(file);
    chromeHTMLFileInput.value = '';
}

// Card quick actions helpers

async function toggleCardFavorite(id, currentVal) {
    try {
        await fetch(`${API_BASE}/bookmarks/${id}`, {
            method: 'PATCH',
            headers: {
                ...getAuthHeader(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ is_favorite: !currentVal })
        });
        await fetchBookmarks();
        await fetchStats();
    } catch (err) {
        console.error(err);
    }
}

async function toggleCardRead(id, currentVal) {
    try {
        await fetch(`${API_BASE}/bookmarks/${id}`, {
            method: 'PATCH',
            headers: {
                ...getAuthHeader(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ read_status: currentVal === 'read' ? 'unread' : 'read' })
        });
        await fetchBookmarks();
        await fetchStats();
    } catch (err) {
        console.error(err);
    }
}

// Open Detail View Overlay
async function openDetailsPanel(bookmark) {
    showLoading();
    try {
        const res = await fetch(`${API_BASE}/bookmarks/${bookmark.id}`, { headers: getAuthHeader() });
        const b = await res.json();
        
        detailPanelOverlay.classList.add('active');
        hideLoading();

        // Update actions values
        btnDetailFavorite.className = `btn-icon-accent ${b.is_favorite ? 'favorite-active' : ''}`;
        btnDetailRead.className = `btn-icon-accent ${b.read_status === 'read' ? 'active' : ''}`;

        // Re-attach details panel actions
        btnDetailFavorite.onclick = () => handleDetailFavorite(b);
        btnDetailRead.onclick = () => handleDetailRead(b);
        btnDetailArchive.onclick = () => handleDetailArchive(b);

        detailBody.innerHTML = `
            <h2 class="detail-title">${escapeHtml(b.title || 'Untitled Node')}</h2>
            <a href="${b.url}" target="_blank" class="detail-url">
                <i data-lucide="external-link" style="width: 14px; height: 14px;"></i>
                <span>${escapeHtml(b.url)}</span>
            </a>

            <div class="detail-section">
                <h4>Summary</h4>
                <p>${escapeHtml(b.summary || 'Analysis incomplete, Sir.')}</p>
            </div>

            <div class="detail-section">
                <h4>Key Takeaways</h4>
                <ul class="detail-bullets">
                    ${(b.key_points || []).map(kp => `<li>${escapeHtml(kp)}</li>`).join('')}
                </ul>
            </div>

            <div class="detail-section">
                <h4>Category</h4>
                <span class="card-badge">${escapeHtml(b.category || 'General')}</span>
            </div>

            <div class="detail-section">
                <h4>Tags</h4>
                <div class="card-tags">
                    ${(b.tags || []).map(t => `<span class="card-tag">#${escapeHtml(t)}</span>`).join(' ')}
                </div>
            </div>

            <div class="detail-meta-grid">
                <div class="detail-meta-item">Scrape Source: <strong>${escapeHtml(b.domain || 'Direct')}</strong></div>
                <div class="detail-meta-item">Read Status: <strong>${b.read_status}</strong></div>
                <div class="detail-meta-item">Word Count: <strong>${b.word_count || 0} words</strong></div>
                <div class="detail-meta-item">Date Archived: <strong>${new Date(b.added_at).toLocaleDateString()}</strong></div>
            </div>

            <button class="btn-secondary detail-preview-btn" id="btnTogglePreview">
                Show Document Raw Text Tokens
            </button>
            <div class="content-preview-container" id="previewContainer">Fetching document stream...</div>
        `;
        lucide.createIcons();

        // Attach preview button logic
        const btnTogglePreview = document.getElementById('btnTogglePreview');
        const previewContainer = document.getElementById('previewContainer');
        btnTogglePreview.addEventListener('click', async () => {
            if (previewContainer.style.display === 'block') {
                previewContainer.style.display = 'none';
                btnTogglePreview.innerText = 'Show Document Raw Text Tokens';
            } else {
                previewContainer.style.display = 'block';
                btnTogglePreview.innerText = 'Hide Document Raw Text Tokens';
                
                try {
                    previewContainer.innerText = 'Resolving raw files...';
                    previewContainer.innerText = "ChromaDB Document Cache: \n" + (b.summary || "No preview cached.");
                } catch {
                    previewContainer.innerText = 'Failed to load document preview.';
                }
            }
        });

    } catch (err) {
        console.error('Details panel error:', err);
    }
}

async function handleDetailFavorite(b) {
    await toggleCardFavorite(b.id, b.is_favorite);
    b.is_favorite = !b.is_favorite;
    btnDetailFavorite.className = `btn-icon-accent ${b.is_favorite ? 'favorite-active' : ''}`;
}

async function handleDetailRead(b) {
    const newVal = b.read_status === 'read' ? 'unread' : 'read';
    await toggleCardRead(b.id, b.read_status);
    b.read_status = newVal;
    btnDetailRead.className = `btn-icon-accent ${b.read_status === 'read' ? 'active' : ''}`;
}

async function handleDetailArchive(b) {
    if (confirm('Are you sure you want to archive this resource into the bin, Sir?')) {
        try {
            await fetch(`${API_BASE}/bookmarks/${b.id}`, {
                method: 'DELETE',
                headers: getAuthHeader()
            });
            detailPanelOverlay.classList.remove('active');
            await fetchBookmarks();
            await fetchStats();
        } catch (err) {
            console.error(err);
        }
    }
}

// JARVIS Streaming Chat handler
let chatSocket = null;

async function handleChatSubmit(e) {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;

    chatInput.value = '';
    
    appendChatMessage('user', text);
    
    const assistantMsgId = `assistant-${Date.now()}`;
    appendChatMessage('assistant', '', assistantMsgId);
    const assistantBubble = document.getElementById(assistantMsgId).querySelector('.message-content');
    assistantBubble.classList.add('streaming');

    const token = getToken();
    const useRag = chkUseRag.checked;

    if (!useRag) {
        try {
            const res = await fetch(`${API_BASE}/chat`, {
                method: 'POST',
                headers: {
                    ...getAuthHeader(),
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    message: text,
                    conversation_id: state.currentConversationId,
                    stream: false,
                    use_rag: false
                })
            });
            const data = await res.json();
            state.currentConversationId = data.conversation_id;
            
            assistantBubble.classList.remove('streaming');
            assistantBubble.innerHTML = `<p>${formatMarkdownCitations(data.response)}</p>`;
            
            chatMessages.scrollTop = chatMessages.scrollHeight;

        } catch (err) {
            console.error(err);
            assistantBubble.classList.remove('streaming');
            assistantBubble.innerText = 'My systems are unresponsive at the moment, Sir.';
        }
        return;
    }

    if (chatSocket) {
        chatSocket.close();
    }

    const wsUrl = `${WS_BASE.replace('http', 'ws')}/chat/stream?token=${encodeURIComponent(token)}`;
    chatSocket = new WebSocket(wsUrl);

    let fullText = '';

    chatSocket.onopen = () => {
        chatSocket.send(JSON.stringify({
            message: text,
            conversation_id: state.currentConversationId
        }));
    };

    chatSocket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        
        if (data.content) {
            fullText += data.content;
            assistantBubble.innerHTML = `<p>${formatMarkdownCitations(fullText)}</p>`;
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        if (data.done) {
            state.currentConversationId = data.conversation_id;
            assistantBubble.classList.remove('streaming');
            assistantBubble.innerHTML = `<p>${formatMarkdownCitations(fullText)}</p>`;
            chatMessages.scrollTop = chatMessages.scrollHeight;
            chatSocket.close();
        }

        if (data.error) {
            assistantBubble.classList.remove('streaming');
            assistantBubble.innerText = `[COMMS ERROR]: ${data.error}`;
        }
    };

    chatSocket.onerror = (err) => {
        console.error('WS Error:', err);
        assistantBubble.classList.remove('streaming');
        assistantBubble.innerText = 'Comms link failed. Please check backend connection, Sir.';
    };
}

function appendChatMessage(role, text, id = null) {
    const msg = document.createElement('div');
    msg.className = `message ${role}`;
    if (id) msg.id = id;

    const avatarChar = role === 'user' ? 'U' : 'J';
    msg.innerHTML = `
        <div class="avatar">${avatarChar}</div>
        <div class="message-content">
            <p>${escapeHtml(text)}</p>
        </div>
    `;
    
    chatMessages.appendChild(msg);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    lucide.createIcons();
}

function resetChat() {
    state.currentConversationId = null;
    chatMessages.innerHTML = `
        <div class="message assistant">
            <div class="avatar">J</div>
            <div class="message-content">
                <p>New conversation timeline initialized, Sir. Databanks cleared from chat memory buffer.</p>
            </div>
        </div>
    `;
    lucide.createIcons();
}

function notifyJarvis(message) {
    console.log(`[JARVIS]: ${message}`);
}

function formatMarkdownCitations(text) {
    const citationRegex = /\[source:([a-f0-9\-]{36})\]/gi;
    let formatted = escapeHtml(text);
    
    formatted = formatted.replace(/\n/g, '<br>');

    formatted = formatted.replace(citationRegex, (match, id) => {
        return `<a class="citation" data-id="${id}" onclick="openCitationPanel('${id}')">[Source]</a>`;
    });
    
    return formatted;
}

window.openCitationPanel = async (id) => {
    try {
        const res = await fetch(`${API_BASE}/bookmarks/${id}`, { headers: getAuthHeader() });
        if (res.ok) {
            const b = await res.json();
            openDetailsPanel(b);
        } else {
            alert("This citation reference refers to a resource that is no longer online in your storage grid.");
        }
    } catch (err) {
        console.error(err);
    }
};

// Helper Utilities
function escapeHtml(text) {
    if (!text) return '';
    return text
        .toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function showLoading() {
    // optional loader UI indicator
}

function hideLoading() {
    // optional loader UI indicator
}

function openModal(modal) {
    modal.classList.add('active');
}

function closeModal(modal) {
    modal.classList.remove('active');
}
