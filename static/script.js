import {
    auth,
    onAuthStateChanged,
    signOut,
    deleteUser
} from "/static/firebase-auth.js";

let currentStock = null;
let currentPrice = null;
let priceChart = null;
let rsiChart = null;
let macdChart = null;
let gaugeChart = null;
let backtestChart = null;
let currentUserEmail = null;
let currentHorizon = '1d';
let currentAttributions = [];

let portfolio = {
    balance: 10000,
    holdings: {} // ticker: quantity
};

let watchlist = []; // Initialize empty, load later

// Security Utilities
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

async function getAuthHeaders(userInstance = null, forceRefresh = false) {
    const headers = { 'Content-Type': 'application/json' };
    try {
        const u = userInstance || (auth && auth.currentUser);
        if (u) {
            const token = await u.getIdToken(forceRefresh);
            if (token && typeof token === 'string' && token.length > 20 && token !== 'undefined' && token !== 'null') {
                headers['Authorization'] = `Bearer ${token}`;
            } else {
                console.warn("[Auth] Retrieved empty or malformed token from currentUser");
            }
        } else {
            console.warn("[Auth] No currentUser found when generating auth headers");
        }
    } catch (err) {
        console.warn("[Auth] Could not retrieve ID token:", err.message || err);
    }
    return headers;
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    onAuthStateChanged(auth, (user) => {
        if (user) {
            console.log("User logged in:", user.email);
            currentUserEmail = user.email;

            // Load data will allow us to check for custom name later
            // For now, show email derived name as placeholder
            updateGreeting();

            // Load stocks first
            fetchStocks();
            // Load remote data
            loadRemoteData(user.email, user);
        } else {
            console.log("No user logged in, redirecting...");
            window.location.href = '/login';
        }
    });

    // Load stocks immediately (public data)
    fetchStocks();
    updateWatchlistUI();

    // Request notification permission for high confidence alerts
    if ('Notification' in window && Notification.permission !== 'granted' && Notification.permission !== 'denied') {
        setTimeout(() => {
            Notification.requestPermission();
        }, 5000);
    }

    // Set default active tab
    const defaultTab = document.querySelector('.tab-btn[data-tab="chart"]');
    if (defaultTab) defaultTab.click();

    // Setup Search Listener
    const searchInput = document.getElementById('stockSearchInput');
    if (searchInput) {
        searchInput.addEventListener('input', debounce(handleSearch, 300));
        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            if (!searchInput.contains(e.target) && !document.getElementById('searchResults').contains(e.target)) {
                document.getElementById('searchResults').style.display = 'none';
            }
        });
    }

    // Horizon buttons listener
    const horizonButtons = document.querySelectorAll('.horizon-btn');
    horizonButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            horizonButtons.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentHorizon = e.target.getAttribute('data-horizon');
            console.log(`Switched horizon to ${currentHorizon}`);
            // If we have a selected stock, re-fetch prediction for this horizon
            if (currentStock) {
                fetchPrediction(currentStock, currentHorizon);
            }
        });
    });
});

function debounce(func, wait) {
    let timeout;
    return function (...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), wait);
    };
}

async function handleSearch(e) {
    const query = e.target.value.trim();
    const resultsList = document.getElementById('searchResults');

    if (query.length < 2) {
        resultsList.style.display = 'none';
        return;
    }

    try {
        const response = await fetch(`/api/lookup?q=${encodeURIComponent(query)}`);
        const results = await response.json();

        resultsList.innerHTML = '';
        if (results.length > 0) {
            results.forEach(item => {
                const li = document.createElement('li');
                li.innerHTML = `
                    <div style="font-weight: bold;">${escapeHtml(item.symbol)}</div>
                    <small>${escapeHtml(item.shortname)} (${escapeHtml(item.exchange)})</small>
                `;
                li.onclick = () => {
                    selectStock(item.symbol);
                    document.getElementById('stockSearchInput').value = item.symbol;
                    resultsList.style.display = 'none';
                };
                resultsList.appendChild(li);
            });
            resultsList.style.display = 'block';
        } else {
            // Optional: Show "No results"
            resultsList.style.display = 'none';
        }
    } catch (error) {
        console.error("Search error:", error);
    }
}

async function loadRemoteData(email, userInstance = null) {
    try {
        let authHeaders = await getAuthHeaders(userInstance);
        let response = await fetch(`/api/get_data/${encodeURIComponent(email)}?t=${Date.now()}`, {
            headers: authHeaders
        });

        // If 401, retry once with a freshly forced token refresh
        if (response.status === 401) {
            console.info("[Auth] Initial get_data returned 401, attempting forced token refresh...");
            authHeaders = await getAuthHeaders(userInstance, true);
            response = await fetch(`/api/get_data/${encodeURIComponent(email)}?t=${Date.now()}`, {
                headers: authHeaders
            });
        }

        const result = await response.json();

        if (result.status === 'success' && result.data) {
            // Merge or overwrite? Let's overwrite for now as it's the source of truth
            if (result.data.portfolio) {
                portfolio = result.data.portfolio;
                migratePortfolioStructure(); // Ensure data matches new schema
            }
            if (result.data.watchlist) watchlist = result.data.watchlist;

            console.log("Data loaded from server");
        } else if (result.status === 'game_start') {
            console.log("New user, using default/local data");
            loadPortfolioFromLocal();
            loadWatchlistFromLocal();
        }

        // Check Age Verification
        // Ensure profile object exists
        if (!portfolio.profile) portfolio.profile = { verified: false };

        updateGreeting(); // Update header with custom name if loaded

        // Populate profile input
        const nameInput = document.getElementById('profileNameInput');
        if (nameInput) {
            nameInput.value = portfolio.profile.customName || '';
        }

        // Check if verified AND has gender. If not, show modal.
        if (!portfolio.profile.verified || !portfolio.profile.gender) {
            document.getElementById('ageCheckModal').classList.add('active');

            // Pre-fill DOB if we have it (e.g. they verified age but not gender yet)
            if (portfolio.profile.dob) {
                document.getElementById('dobInput').value = portfolio.profile.dob;
            }
        }

        updatePortfolioUI();
        updateProfileUI();
        updateWatchlistUI();
        updateWatchlistButton();
    } catch (error) {
        console.error("Error loading data:", error);
    }
}

async function saveData() {
    if (!currentUserEmail) return;

    const data = {
        portfolio: portfolio,
        watchlist: watchlist
    };

    try {
        let authHeaders = await getAuthHeaders();
        let response = await fetch('/api/save_data', {
            method: 'POST',
            headers: authHeaders,
            body: JSON.stringify({ email: currentUserEmail, data: data })
        });

        // If 401, retry once with a freshly forced token refresh
        if (response.status === 401) {
            console.info("[Auth] Initial save_data returned 401, attempting forced token refresh...");
            authHeaders = await getAuthHeaders(null, true);
            response = await fetch('/api/save_data', {
                method: 'POST',
                headers: authHeaders,
                body: JSON.stringify({ email: currentUserEmail, data: data })
            });
        }

        if (response.ok) {
            console.log("Data saved to server");
        } else {
            console.warn("Server returned status", response.status, "while saving data");
        }
    } catch (error) {
        console.error("Error saving data:", error);
    }
}

window.logout = async () => {
    await saveData(); // Save before exit
    try {
        await signOut(auth);
        window.location.href = '/login';
    } catch (error) {
        console.error("Logout error:", error);
    }
};

// --- Profile & Greeting Logic ---

window.saveCustomName = function () {
    const input = document.getElementById('profileNameInput');
    if (!input) return;

    const newName = input.value.trim();
    if (!portfolio.profile) portfolio.profile = {};

    portfolio.profile.customName = newName;
    saveData(); // Persist

    updateGreeting(); // Refresh header
    showMessageModal("Success", "Display Name Updated!");
};

function updateGreeting() {
    let displayName = "Trader";

    // Priority: Custom > Google > Email
    if (portfolio.profile && portfolio.profile.customName) {
        displayName = portfolio.profile.customName;
    } else if (auth.currentUser && auth.currentUser.displayName) {
        displayName = auth.currentUser.displayName;
    } else if (currentUserEmail) {
        const name = currentUserEmail.split('@')[0];
        displayName = name.charAt(0).toUpperCase() + name.slice(1);
    }

    const headerEl = document.getElementById('portfolioHeader');
    if (headerEl) headerEl.textContent = `Hello, ${displayName}`;
}

window.refreshData = async function () {
    if (!currentStock) return;
    const btn = document.getElementById('refreshBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '...';
        btn.style.cursor = 'wait';
    }

    await selectStock(currentStock);

    if (btn) {
        btn.disabled = false;
        btn.textContent = '↻';
        btn.style.cursor = 'pointer';
    }
};


// Fetch available stocks
async function fetchStocks() {
    try {
        console.log("Fetching stocks...");
        const list = document.getElementById('stockList');
        list.innerHTML = '<li>Loading...</li>';

        const response = await fetch('/api/stocks');
        console.log("Stocks response status:", response.status);
        const stocks = await response.json();
        console.log("Stocks data:", stocks);

        list.innerHTML = '';

        stocks.forEach(stock => {
            const li = document.createElement('li');
            li.textContent = stock;
            li.setAttribute('data-ticker', stock);
            li.onclick = () => selectStock(stock, li);
            if (stock === currentStock) li.classList.add('active');
            list.appendChild(li);
        });
    } catch (error) {
        console.error('Error fetching stocks:', error);
    }
}

// Select a stock
async function selectStock(ticker, element) {
    currentStock = ticker;

    // Highlight the selected ticker across all stock lists (Watchlist and Available Stocks)
    document.querySelectorAll('.stock-list li').forEach(li => {
        const itemTicker = li.getAttribute('data-ticker') || li.textContent.trim();
        if (itemTicker === ticker) {
            li.classList.add('active');
            try {
                li.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
            } catch (e) {}
        } else {
            li.classList.remove('active');
        }
    });

    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) refreshBtn.style.display = 'block';

    updateWatchlistUI();

    const titleEl = document.getElementById('selectedStockTitle');
    if (titleEl) titleEl.textContent = ticker;

    updateWatchlistButton();
    resetBacktestUI();

    await fetchPrediction(ticker, currentHorizon);
}

async function fetchPrediction(ticker, horizon = currentHorizon) {
    const statusIndicator = document.getElementById('statusIndicator');
    if (statusIndicator) {
        statusIndicator.textContent = 'Analyzing stock model...';
        statusIndicator.style.color = '#f59e0b';
    }

    try {
        const response = await fetch(`/api/predict/${encodeURIComponent(ticker)}?horizon=${encodeURIComponent(horizon)}`);
        const data = await response.json();

        if (data.error) {
            alert(data.error);
            if (statusIndicator) {
                statusIndicator.textContent = 'Error loading data';
                statusIndicator.style.color = 'var(--danger-color)';
            }
            return;
        }

        updateDashboard(data);
        const time = new Date().toLocaleTimeString();
        if (statusIndicator) {
            statusIndicator.textContent = 'Live Data - ' + time;
            statusIndicator.style.color = 'var(--text-secondary)';
        }

        fetchSentiment(ticker);
    } catch (error) {
        console.error('Error loading stock data:', error);
        alert("Error: " + error.message + ". If this is a new stock, check if the ticker is valid.");
        if (statusIndicator) {
            statusIndicator.textContent = 'Error: ' + error.message;
            statusIndicator.style.color = 'var(--danger-color)';
        }
    }
}

async function fetchSentiment(ticker) {
    const badge = document.getElementById('sentimentBadge');
    const score = document.getElementById('sentimentScore');
    const list = document.getElementById('newsList');

    try {
        const response = await fetch(`/api/sentiment/${ticker}`);
        const data = await response.json();

        if (data.label === 'Positive') {
            badge.textContent = 'Positive 🟢';
            badge.className = 'sentiment-badge positive';
        } else if (data.label === 'Negative') {
            badge.textContent = 'Negative 🔴';
            badge.className = 'sentiment-badge negative';
        } else {
            badge.textContent = 'Neutral ⚪';
            badge.className = 'sentiment-badge neutral';
        }

        score.textContent = (data.score || 0).toFixed(2);

        list.innerHTML = '';
        const headlines = data.headlines || [];
        if (headlines.length === 0) {
            list.innerHTML = '<li>No recent news headlines available</li>';
            return;
        }

        headlines.slice(0, 5).forEach(news => {
            const li = document.createElement('li');
            const scoreVal = typeof news.score === 'number' ? news.score : 0;
            let badgeClass = 'neutral';
            let badgeText = `${scoreVal >= 0 ? '+' : ''}${scoreVal.toFixed(2)}`;
            if (scoreVal > 0.05) badgeClass = 'positive';
            else if (scoreVal < -0.05) badgeClass = 'negative';

            const btn = document.createElement('button');
            btn.className = 'news-item-btn';
            btn.type = 'button';
            btn.setAttribute('aria-label', `View insight for: ${news.title}`);
            btn.innerHTML = `
                <span class="news-item-title">${escapeHtml(news.title)}</span>
                <span class="news-item-badge ${badgeClass}">${badgeText}</span>
                <span class="news-item-icon" aria-hidden="true">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                </span>
            `;
            btn.addEventListener('click', () => {
                window.openNewsDialog({
                    title: news.title,
                    score: scoreVal,
                    link: news.link || news.url,
                    ticker: ticker
                });
            });
            li.appendChild(btn);
            list.appendChild(li);
        });

    } catch (error) {
        console.error('Sentiment error:', error);
        badge.textContent = 'Error';
    }
}

// Morphing News Dialog Logic
window.openNewsDialog = function (news) {
    const modal = document.getElementById('newsDialogModal');
    if (!modal) return;

    const titleEl = document.getElementById('newsDialogTitle');
    const tickerEl = document.getElementById('newsDialogTicker');
    const badgeEl = document.getElementById('newsDialogBadge');
    const scoreEl = document.getElementById('newsDialogScore');
    const barEl = document.getElementById('newsDialogBar');
    const linkEl = document.getElementById('newsDialogLink');

    if (titleEl) titleEl.textContent = news.title || '--';
    if (tickerEl) tickerEl.textContent = (news.ticker || currentStock || 'NSE').toUpperCase();

    const score = typeof news.score === 'number' ? news.score : 0;
    if (scoreEl) scoreEl.textContent = (score >= 0 ? '+' : '') + score.toFixed(2);

    let badgeText = 'Neutral ⚪';
    let badgeClass = 'sentiment-badge neutral';
    let barColor = 'var(--text-muted)';
    if (score > 0.05) {
        badgeText = 'Positive 🟢';
        badgeClass = 'sentiment-badge positive';
        barColor = 'var(--positive-text)';
    } else if (score < -0.05) {
        badgeText = 'Negative 🔴';
        badgeClass = 'sentiment-badge negative';
        barColor = 'var(--negative-text)';
    }

    if (badgeEl) {
        badgeEl.textContent = badgeText;
        badgeEl.className = badgeClass;
    }

    if (barEl) {
        // Map -1..1 score to 5%..95% width
        const pct = Math.min(Math.max(((score + 1) / 2) * 100, 5), 95);
        barEl.style.width = `${pct}%`;
        barEl.style.background = barColor;
    }

    if (linkEl) {
        const targetUrl = news.link || news.url;
        if (targetUrl && targetUrl !== '#') {
            linkEl.href = targetUrl;
            linkEl.style.display = 'inline-flex';
        } else {
            linkEl.style.display = 'none';
        }
    }

    modal.classList.add('active');
};

window.closeNewsDialog = function () {
    const modal = document.getElementById('newsDialogModal');
    if (modal) modal.classList.remove('active');
};

// Multi-Horizon Switching
window.switchHorizon = async function (horizon) {
    currentHorizon = horizon;

    // Update active horizon UI button state with animated background indicator
    const targetBtn = Array.from(document.querySelectorAll('.horizon-btn')).find(
        btn => btn.getAttribute('data-horizon') === horizon
    );

    document.querySelectorAll('.horizon-btn').forEach(btn => {
        const isActive = btn === targetBtn;
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });

    if (targetBtn) {
        updateHorizonGlider(targetBtn);
    }

    const labelEl = document.getElementById('predictionHorizonLabel');
    if (labelEl) {
        const labelMap = { '1d': '1-Day Forecast', '5d': '5-Day Forecast', '1m': '1-Month Forecast' };
        labelEl.textContent = labelMap[horizon] || `${horizon} Horizon`;
    }

    if (currentStock) {
        const predValue = document.getElementById('predictionValue');
        const predProbContainer = document.querySelector('.prediction-prob');
        if (predValue) {
            predValue.textContent = 'Computing...';
            predValue.className = 'prediction-value training';
        }
        if (predProbContainer) {
            predProbContainer.innerHTML = 'Status: <span id="predictionProb">Evaluating target horizon model...</span>';
        }
        try {
            const response = await fetch(`/api/predict/${currentStock}?horizon=${horizon}`);
            const data = await response.json();
            if (!data.error) {
                updateDashboard(data);
            }
        } catch (e) {
            console.error("Error switching horizon:", e);
        }
    }
};

// Render Top XAI Driver Pills
function renderTopDrivers(drivers) {
    const list = document.getElementById('topDriversList');
    if (!list) return;
    list.innerHTML = '';

    if (!drivers || drivers.length === 0) {
        list.innerHTML = '<span class="xai-pill neutral">No driver data available</span>';
        return;
    }

    drivers.forEach(d => {
        const pill = document.createElement('span');
        const dirClass = d.direction === 'positive' ? 'positive' : 'negative';
        const signSymbol = d.direction === 'positive' ? '▲' : '▼';
        pill.className = `xai-pill ${dirClass}`;
        
        const spanText = document.createElement('span');
        spanText.textContent = `${signSymbol} ${d.name || ''}`;
        
        const smallImpact = document.createElement('small');
        smallImpact.style.fontWeight = '700';
        smallImpact.textContent = ` (${d.impact || ''})`;
        
        pill.appendChild(spanText);
        pill.appendChild(smallImpact);
        list.appendChild(pill);
    });
}

// Open/Close SHAP Feature Breakdown Modal
window.openXaiModal = function () {
    const modal = document.getElementById('xaiModal');
    const tickerEl = document.getElementById('xaiModalTicker');
    const container = document.getElementById('xaiFeatureBars');
    if (tickerEl) tickerEl.textContent = currentStock || '--';

    if (container) {
        container.innerHTML = '';
        if (!currentAttributions || currentAttributions.length === 0) {
            container.innerHTML = '<p style="color: var(--text-secondary); text-align: center; padding: 1rem;">No detailed feature attributions available for this model.</p>';
        } else {
            currentAttributions.forEach(item => {
                const row = document.createElement('div');
                row.className = 'xai-bar-row';
                const isPos = item.direction === 'positive';
                const color = isPos ? '#10b981' : '#f43f5e';
                const pct = Math.min(Math.max(item.pct, 1.5), 100);
                row.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.85rem; margin-bottom: 3px;">
                        <span><strong style="color: #f8fafc;">${escapeHtml(item.name)}</strong> <small style="color: #94a3b8; font-size: 0.75rem;">(${escapeHtml(item.feature)})</small></span>
                        <span style="font-weight: 700; color: ${color}; font-size: 0.85rem;">${escapeHtml(item.impact_str)}</span>
                    </div>
                    <div style="width: 100%; height: 7px; background: rgba(255,255,255,0.08); border-radius: 4px; overflow: hidden; position: relative;">
                        <div style="width: ${pct}%; height: 100%; background: ${color}; border-radius: 4px; transition: width 0.4s ease;"></div>
                    </div>
                `;
                container.appendChild(row);
            });
        }
    }
    if (modal) modal.classList.add('active');
};

window.closeXaiModal = function () {
    const modal = document.getElementById('xaiModal');
    if (modal) modal.classList.remove('active');
};

// Update Dashboard UI
function updateDashboard(data) {
    // Horizon UI Sync
    if (data.horizon_days) {
        const hKey = data.horizon_days === 5 ? '5d' : (data.horizon_days === 20 ? '1m' : '1d');
        document.querySelectorAll('.horizon-btn').forEach(btn => {
            if (btn.getAttribute('data-horizon') === hKey) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
        const labelEl = document.getElementById('predictionHorizonLabel');
        if (labelEl) {
            const labelMap = { '1d': '1-Day Forecast', '5d': '5-Day Forecast', '1m': '1-Month Forecast' };
            labelEl.textContent = labelMap[hKey] || `${data.horizon} Horizon`;
        }
    }

    // Prediction
    const predValue = document.getElementById('predictionValue');
    const predProbContainer = document.querySelector('.prediction-prob');

    const isTraining = !data.prediction || 
                       data.prediction === 'TRAINING' || 
                       data.prediction === 'Training Model...' || 
                       (data.prediction === 'NEUTRAL' && (data.probability === 0 || data.probability === 0.5));

    if (isTraining) {
        predValue.textContent = 'Training...';
        predValue.className = 'prediction-value training';
        if (predProbContainer) {
            predProbContainer.innerHTML = 'Status: <span id="predictionProb">Model is being trained...</span>';
        }
    } else {
        const rawPred = data.prediction.toUpperCase();
        predValue.textContent = rawPred;

        let className = 'prediction-value';
        if (rawPred === 'UP' || rawPred === 'BUY' || rawPred === 'STRONG BUY') {
            className += ' up';
        } else if (rawPred === 'DOWN' || rawPred === 'SELL' || rawPred === 'STRONG SELL') {
            className += ' down';
        } else {
            className += ' neutral';
        }
        predValue.className = className;

        const probPercent = (data.probability * 100).toFixed(2);
        if (predProbContainer) {
            predProbContainer.innerHTML = `Confidence: <span id="predictionProb">${probPercent}</span>%`;
        }
    }

    // XAI Drivers & Attributions
    renderTopDrivers(data.top_drivers || []);
    currentAttributions = data.all_attributions || [];

    // Charts
    if (data.history.close && data.history.close.length > 0) {
        currentPrice = data.history.close[data.history.close.length - 1];
    }
    renderPriceChart(data.history);
    renderRSIChart(data.history);
    renderMACDChart(data.history);
    renderStochChart(data.history);

    if (data.technical_analysis) {
        renderGaugeChart(data.technical_analysis);
    }
}

// --- Delete Account Logic ---
window.openDeleteModal = function () {
    document.getElementById('deleteModal').classList.add('active');
}

window.closeDeleteModal = function () {
    document.getElementById('deleteModal').classList.remove('active');
}

window.confirmDeleteAccount = async function () {
    const user = auth.currentUser;
    if (!user) return;

    const btn = document.querySelector('#deleteModal .btn-danger');
    const originalText = btn.innerText;

    try {
        btn.innerText = "Deleting...";
        btn.disabled = true;

        // 1. Delete data from backend
        const authHeaders = await getAuthHeaders();
        const response = await fetch('/api/delete_data', {
            method: 'POST',
            headers: authHeaders,
            body: JSON.stringify({ email: user.email })
        });

        if (!response.ok) {
            throw new Error("Failed to delete user data");
        }

        // 2. Delete user from Firebase
        await deleteUser(user);

        // 3. Cleanup authentication
        await signOut(auth);

        alert("Account deleted successfully.");
        window.location.href = "/login";

    } catch (error) {
        console.error("Delete account error:", error);
        alert("Error deleting account: " + error.message);
        // If re-login is required (Firebase security), prompt user
        if (error.code === 'auth/requires-recent-login') {
            alert("Please log out and log in again to delete your account.");
        }
        btn.innerText = originalText;
        btn.disabled = false;
        window.closeDeleteModal();
    }
}

// Render Stochastic Chart
function renderStochChart(history) {
    if (!history || !history.dates || !history.stoch_k || history.dates.length === 0) return;
    const ctx = document.getElementById('stochChart').getContext('2d');
    // Check if chart instance exists (need to store it globally or attach to canvas)
    if (window.stochChartInstance) window.stochChartInstance.destroy();

    window.stochChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: history.dates,
            datasets: [
                {
                    label: '%K',
                    data: history.stoch_k,
                    borderColor: '#3b82f6',
                    borderWidth: 2,
                    pointRadius: 0
                },
                {
                    label: '%D',
                    data: history.stoch_d,
                    borderColor: '#f97316',
                    borderWidth: 2,
                    pointRadius: 0
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: true } },
            scales: {
                x: { display: false },
                y: { min: 0, max: 100, grid: { color: 'rgba(148, 163, 184, 0.1)' } }
            }
        }
    });
}

// Render Gauge Chart
function renderGaugeChart(techData) {
    // Inject HTML if missing
    let gaugeCard = document.querySelector('.gauge-card');
    if (!gaugeCard) {
        const sentimentCard = document.querySelector('.sentiment-card');
        if (sentimentCard) {
            gaugeCard = document.createElement('div');
            gaugeCard.className = 'card gauge-card';
            gaugeCard.innerHTML = `
    <h3>Technical Rating</h3>
        <div class="gauge-container" style="position: relative; height: 160px; display: flex; justify-content: center; align-items: center;">
            <canvas id="technicalGauge"></canvas>
            <div id="gaugeLabel" style="position: absolute; bottom: 10px; font-weight: bold; font-size: 1.2rem;">--</div>
        </div>
`;
            sentimentCard.insertAdjacentElement('afterend', gaugeCard);
        }
    }

    const ctx = document.getElementById('technicalGauge').getContext('2d');
    const label = document.getElementById('gaugeLabel');

    if (gaugeChart) gaugeChart.destroy();

    // Map score (-6 to 6) to 0-100 for gauge position
    // -6 -> 0, 6 -> 100
    const score = techData.score;
    const normalizedScore = ((score + 6) / 12) * 100;

    label.textContent = techData.rating;
    label.className = techData.rating.toLowerCase().replace(' ', '-');

    // Color based on rating matching backend thresholds (-6 to +6 scale)
    let color = '#94a3b8'; // Neutral (-1, 0, 1)
    if (score >= 4) color = '#22c55e'; // Strong Buy (4, 5, 6)
    else if (score >= 2) color = '#4ade80'; // Buy (2, 3)
    else if (score <= -4) color = '#ef4444'; // Strong Sell (-4, -5, -6)
    else if (score <= -2) color = '#f87171'; // Sell (-2, -3)

    label.style.color = color;

    gaugeChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Score', 'Remaining'],
            datasets: [{
                data: [normalizedScore, 100 - normalizedScore],
                backgroundColor: [
                    color,
                    '#e2e8f0'
                ],
                borderWidth: 0,
                cutout: '70%',
                circumference: 180,
                rotation: 270
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { enabled: false }
            }
        }
    });
}

// Render Price Chart (Plotly)
function renderPriceChart(history) {
    if (!history || !history.dates || !history.close || history.dates.length === 0) return;
    const trace1 = {
        x: history.dates,
        close: history.close,
        decreasing: { line: { color: '#ef4444' } },
        high: history.high,
        increasing: { line: { color: '#22c55e' } },
        line: { color: 'rgba(31,119,180,1)' },
        low: history.low,
        open: history.open,
        type: 'candlestick',
        xaxis: 'x',
        yaxis: 'y',
        name: 'Price'
    };

    const trace2 = {
        x: history.dates,
        y: history.ema50,
        type: 'scatter',
        mode: 'lines',
        line: { color: '#f59e0b', width: 1.5 },
        name: 'EMA 50'
    };

    const trace3 = {
        x: history.dates,
        y: history.ema200,
        type: 'scatter',
        mode: 'lines',
        line: { color: '#3b82f6', width: 1.5 },
        name: 'EMA 200'
    };

    const data = [trace1, trace2, trace3];

    const layout = {
        dragmode: 'zoom',
        margin: { r: 10, t: 25, b: 40, l: 60 },
        showlegend: true,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: {
            color: '#94a3b8'
        },
        xaxis: {
            autorange: true,
            title: 'Date',
            type: 'date',
            rangeslider: { visible: false },
            gridcolor: 'rgba(148, 163, 184, 0.1)',
            zerolinecolor: 'rgba(148, 163, 184, 0.1)'
        },
        yaxis: {
            autorange: true,
            type: 'linear',
            gridcolor: 'rgba(148, 163, 184, 0.1)',
            zerolinecolor: 'rgba(148, 163, 184, 0.1)'
        },
        legend: {
            font: { color: '#94a3b8' }
        }
    };

    Plotly.newPlot('priceChart', data, layout, { responsive: true });
}

// Render RSI Chart
function renderRSIChart(history) {
    if (!history || !history.dates || !history.rsi || history.dates.length === 0) return;
    const ctx = document.getElementById('rsiChart').getContext('2d');
    if (rsiChart) rsiChart.destroy();

    rsiChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: history.dates,
            datasets: [{
                label: 'RSI',
                data: history.rsi,
                borderColor: '#a855f7',
                borderWidth: 2,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { display: false },
                y: { min: 0, max: 100, grid: { color: 'rgba(148, 163, 184, 0.1)' } }
            }
        }
    });
}

// Render MACD Chart
function renderMACDChart(history) {
    if (!history || !history.dates || !history.macd || history.dates.length === 0) return;
    const ctx = document.getElementById('macdChart').getContext('2d');
    if (macdChart) macdChart.destroy();

    const macdValues = history.macd || [];
    macdChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: history.dates,
            datasets: [{
                label: 'MACD',
                data: macdValues,
                backgroundColor: macdValues.map(v => v >= 0 ? '#4ade80' : '#f87171')
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { display: false },
                y: { grid: { color: 'rgba(148, 163, 184, 0.1)' } }
            }
        }
    });
}

// --- Backtesting Logic ---

async function runBacktest() {
    if (!currentStock) {
        alert("Please select a stock first.");
        return;
    }

    const btn = document.querySelector('#backtestTab .btn-primary');
    btn.textContent = 'Running...';
    btn.disabled = true;

    try {
        const response = await fetch(`/api/backtest/${currentStock}`);
        const data = await response.json();

        if (data.error) {
            alert(data.error);
            return;
        }

        const m = data.metrics;

        const totalReturnEl = document.getElementById('btTotalReturn');
        const marketReturnEl = document.getElementById('btMarketReturn');
        totalReturnEl.textContent = m.total_return.toFixed(2) + '%';
        marketReturnEl.textContent = m.market_return.toFixed(2) + '%';
        document.getElementById('btWinRate').textContent = m.win_rate.toFixed(2) + '%';
        document.getElementById('btTotalTrades').textContent = m.total_trades;
        document.getElementById('btMaxDrawdown').textContent = m.max_drawdown.toFixed(2) + '%';

        // Color-code returns
        totalReturnEl.style.color = m.total_return >= 0 ? '#4ade80' : '#f87171';
        marketReturnEl.style.color = m.market_return >= 0 ? '#4ade80' : '#f87171';
        document.getElementById('btMaxDrawdown').style.color = '#f87171';

        renderBacktestChart(data.chart);

    } catch (error) {
        console.error('Backtest error:', error);
    } finally {
        btn.textContent = 'Run Backtest';
        btn.disabled = false;
    }
}

function renderBacktestChart(data) {
    const ctx = document.getElementById('backtestChart').getContext('2d');
    if (backtestChart) backtestChart.destroy();

    backtestChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.dates,
            datasets: [
                {
                    label: 'Strategy',
                    data: data.strategy,
                    borderColor: '#4ade80',
                    tension: 0.1
                },
                {
                    label: 'Market (Buy & Hold)',
                    data: data.market,
                    borderColor: '#94a3b8',
                    borderDash: [5, 5],
                    tension: 0.1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { grid: { color: 'rgba(148, 163, 184, 0.1)' } }
            }
        }
    });
}

function resetBacktestUI() {
    const ids = ['btTotalReturn', 'btMarketReturn', 'btWinRate', 'btTotalTrades', 'btMaxDrawdown'];
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.textContent = '--'; el.style.color = ''; }
    });
    if (backtestChart) backtestChart.destroy();
}

// --- Portfolio Logic ---

function loadPortfolioFromLocal() {
    const saved = localStorage.getItem('portfolio');
    if (saved) {
        portfolio = JSON.parse(saved);
        migratePortfolioStructure();
    }
    updatePortfolioUI();
}

function migratePortfolioStructure() {
    // Convert old "Ticker": Qty format to "Ticker": { qty, avgPrice }
    for (const [ticker, value] of Object.entries(portfolio.holdings)) {
        if (typeof value === 'number') {
            portfolio.holdings[ticker] = {
                qty: value,
                avgPrice: 0 // Unknown for legacy data
            };
        } else if (typeof value === 'object' && value !== null) {
            // Handle schema mismatch (quantity -> qty, avg_price -> avgPrice)
            if (value.quantity !== undefined && value.qty === undefined) {
                value.qty = value.quantity;
                delete value.quantity; // Clean up
            }
            if (value.avg_price !== undefined && value.avgPrice === undefined) {
                value.avgPrice = value.avg_price;
                delete value.avg_price; // Clean up
            }
        }
    }
}

function savePortfolio() {
    saveData();
    updatePortfolioUI();
    updateProfileUI();
}

// --- Multi-Currency Engine ---
const CURRENCY_CONFIG = {
    'INR': { symbol: '₹', name: 'Indian Rupee', rate: 1.0, locale: 'en-IN' },
    'USD': { symbol: '$', name: 'US Dollar', rate: 1 / 83.50, locale: 'en-US' },
    'EUR': { symbol: '€', name: 'Euro', rate: 1 / 90.80, locale: 'de-DE' },
    'GBP': { symbol: '£', name: 'British Pound', rate: 1 / 105.50, locale: 'en-GB' },
    'AED': { symbol: 'AED ', name: 'UAE Dirham', rate: 1 / 22.74, locale: 'en-AE' },
    'JPY': { symbol: '¥', name: 'Japanese Yen', rate: 1 / 0.55, locale: 'ja-JP' }
};

const CURRENCY_KEYS = ['INR', 'USD', 'EUR', 'GBP', 'AED', 'JPY'];

function getUserCurrency() {
    if (portfolio.profile && portfolio.profile.currency && CURRENCY_CONFIG[portfolio.profile.currency]) {
        return portfolio.profile.currency;
    }
    return 'INR';
}

function formatWithCurrency(inrAmount, targetCurrency = null) {
    const code = targetCurrency || getUserCurrency();
    const config = CURRENCY_CONFIG[code] || CURRENCY_CONFIG['INR'];
    const converted = inrAmount * config.rate;
    const decimals = (code === 'JPY') ? 0 : 2;
    return `${config.symbol}${converted.toLocaleString(config.locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`;
}

window.cycleCurrency = function () {
    const current = getUserCurrency();
    const currentIndex = CURRENCY_KEYS.indexOf(current);
    const nextIndex = (currentIndex + 1) % CURRENCY_KEYS.length;
    const nextCurrency = CURRENCY_KEYS[nextIndex];
    window.setCurrency(nextCurrency);
};

window.setCurrency = function (currencyCode) {
    if (!CURRENCY_CONFIG[currencyCode]) return;
    if (!portfolio.profile) portfolio.profile = {};
    portfolio.profile.currency = currencyCode;
    saveData();
    updatePortfolioUI();
    updateProfileUI();

    const config = CURRENCY_CONFIG[currencyCode];
    if (window.showToast) {
        window.showToast(`Display currency changed to ${config.name} (${config.symbol.trim()})`);
    }
};

function updatePortfolioUI() {
    document.getElementById('portfolioBalance').textContent = formatWithCurrency(portfolio.balance);

    const list = document.getElementById('holdingsList');
    list.innerHTML = '';

    for (const [ticker, data] of Object.entries(portfolio.holdings)) {
        const qty = (typeof data === 'object') ? data.qty : data;
        if (qty > 0) {
            const div = document.createElement('div');
            div.className = 'holding-item';
            
            const spanTicker = document.createElement('span');
            spanTicker.textContent = ticker;
            
            const spanQty = document.createElement('span');
            spanQty.textContent = `${Number(qty)} shares`;
            
            div.appendChild(spanTicker);
            div.appendChild(spanQty);
            list.appendChild(div);
        }
    }
}

// --- Trade Modal Logic ---
let pendingTradeType = null;

function openTradeModal(type) {
    if (!currentStock || !currentPrice) {
        alert("Please wait for stock data to load.");
        return;
    }

    pendingTradeType = type;
    const modal = document.getElementById('tradeModal');
    const title = document.getElementById('modalTitle');
    const priceEl = document.getElementById('modalCurrentPrice');
    const availEl = document.getElementById('modalAvailable');
    const btn = document.getElementById('confirmTradeBtn');

    title.textContent = `${type === 'BUY' ? 'Buy' : 'Sell'} ${currentStock}`;
    priceEl.textContent = `Current Price: ${formatWithCurrency(currentPrice)}`;

    // Show available balance or shares
    let avgPrice = 0;
    if (type === 'BUY') {
        availEl.textContent = `Available Balance: ${formatWithCurrency(portfolio.balance)}`;
        availEl.style.color = 'var(--success-color)';
    } else {
        let holding = portfolio.holdings[currentStock];
        if (typeof holding === 'number') holding = { qty: holding, avgPrice: 0 };
        const shares = holding ? holding.qty : 0;
        avgPrice = holding ? holding.avgPrice : 0;

        availEl.textContent = `Available Shares: ${shares} ${avgPrice > 0 ? '(Avg Buy: ' + formatWithCurrency(avgPrice) + ')' : ''}`;
        availEl.style.color = 'var(--text-secondary)';
    }

    // Reset inputs
    document.getElementById('modalQty').value = 10;
    updateModalTotal();

    // Btn styling
    btn.className = type === 'BUY' ? 'btn-primary' : 'btn-danger';
    btn.textContent = type === 'BUY' ? 'Confirm Buy' : 'Confirm Sell';

    modal.classList.add('active');
}

function closeTradeModal() {
    document.getElementById('tradeModal').classList.remove('active');
    pendingTradeType = null;
}

function updateModalTotal() {
    if (!currentPrice) return;

    // Define elements early
    const balanceAfterEl = document.getElementById('modalBalanceAfter');
    const qtyInput = document.getElementById('modalQty');

    const qty = parseInt(qtyInput.value) || 0;
    const total = qty * currentPrice;
    document.getElementById('modalTotalCost').textContent = `Total: ${formatWithCurrency(total)}`;

    // Calculate Balance After
    let balanceAfter = portfolio.balance;
    let balanceColor = 'var(--text-secondary)'; // Default

    if (pendingTradeType === 'BUY') {
        balanceAfter -= total;
        balanceColor = 'var(--danger-color)';
    } else if (pendingTradeType === 'SELL') {
        balanceAfter += total;

        let holding = portfolio.holdings[currentStock];
        if (typeof holding === 'number') holding = { qty: holding, avgPrice: 0 };

        let avgBuyPrice = 0;
        if (holding) {
            avgBuyPrice = parseFloat(holding.avgPrice || holding.avg_price || 0);
        }

        // If we have history, compare prices
        if (avgBuyPrice > 0) {
            const diff = currentPrice - avgBuyPrice;
            const totalPL = diff * qty;

            let label = "Profit";
            if (diff >= 0) {
                balanceColor = diff > 0.0001 ? 'var(--success-color)' : 'var(--text-secondary)';
                label = "Profit";
            } else {
                balanceColor = 'var(--danger-color)';
                label = "Loss";
            }

            if (balanceAfterEl) {
                balanceAfterEl.textContent = `Balance After: ${formatWithCurrency(balanceAfter)} (${label}: ${formatWithCurrency(Math.abs(totalPL))} | ${formatWithCurrency(Math.abs(diff))}/share)`;
                balanceAfterEl.style.color = balanceColor;
                balanceAfterEl.style.fontWeight = '600';
            }
            return;
        } else {
            balanceColor = 'var(--text-secondary)';
            if (balanceAfterEl) balanceAfterEl.textContent = `Balance After: ${formatWithCurrency(balanceAfter)} (No Hist. Price)`;
        }
    }

    if (balanceAfterEl) {
        balanceAfterEl.textContent = `Balance After: ${formatWithCurrency(balanceAfter)}`;
        balanceAfterEl.style.color = balanceColor;
        balanceAfterEl.style.fontWeight = '600';
    }
}

function confirmTrade() {
    if (!pendingTradeType) return;

    const qty = parseInt(document.getElementById('modalQty').value);

    if (isNaN(qty) || qty <= 0) {
        showMessageModal("Invalid Input", "Please enter a valid quantity greater than 0", true);
        return;
    }

    executeTrade(pendingTradeType, qty);
    closeTradeModal();
}

// --- Message Modal Logic ---
function createTradeSuccessContent(action, qty, stock, price) {
    const fragment = document.createDocumentFragment();

    fragment.append(document.createTextNode(`${action} `));

    const qtySpan = document.createElement('span');
    qtySpan.className = 'highlight-text';
    qtySpan.textContent = typeof qty === 'number' ? qty.toLocaleString('en-IN') : String(qty);
    fragment.append(qtySpan);

    fragment.append(document.createTextNode(' shares of '));

    const stockNode = document.createTextNode(String(stock));
    fragment.append(stockNode);

    fragment.append(document.createTextNode(' at '));

    const priceSpan = document.createElement('span');
    priceSpan.className = 'highlight-text';
    priceSpan.textContent = formatWithCurrency(price);
    fragment.append(priceSpan);

    return fragment;
}

function showMessageModal(title, message, isError = false) {
    const modal = document.getElementById('messageModal');
    const titleEl = document.getElementById('msgModalTitle');
    const contentEl = document.getElementById('msgModalContent');

    if (!modal || !titleEl || !contentEl) return;

    titleEl.textContent = title;
    contentEl.textContent = '';

    if (message instanceof Node) {
        contentEl.appendChild(message);
    } else if (Array.isArray(message)) {
        contentEl.append(...message);
    } else {
        contentEl.textContent = message != null ? String(message) : '';
    }

    modal.classList.add('active');
}

function closeMessageModal() {
    const modal = document.getElementById('messageModal');
    if (modal) modal.classList.remove('active');
}

function executeTrade(type, qty) {
    if (!currentStock || !currentPrice) return;

    const cost = currentPrice * qty;

    if (type === 'BUY') {
        if (portfolio.balance >= cost) {
            portfolio.balance -= cost;

            // Get existing holding data
            let holding = portfolio.holdings[currentStock];
            if (typeof holding === 'number') holding = { qty: holding, avgPrice: 0 };
            if (!holding) holding = { qty: 0, avgPrice: 0 };

            // Calculate Weighted Average Price
            const oldTotalVal = holding.qty * holding.avgPrice;
            const newTotalVal = oldTotalVal + (qty * currentPrice);
            const totalQty = holding.qty + qty;

            holding.avgPrice = newTotalVal / totalQty;
            holding.qty = totalQty;

            portfolio.holdings[currentStock] = holding;

            savePortfolio();
            showMessageModal("Trade Successful", createTradeSuccessContent("Bought", qty, currentStock, currentPrice));
        } else {
            showMessageModal("Trade Failed", "Insufficient funds", true);
        }
    } else if (type === 'SELL') {
        let holding = portfolio.holdings[currentStock];
        if (typeof holding === 'number') holding = { qty: holding, avgPrice: 0 };
        const currentQty = holding ? holding.qty : 0;

        if (currentQty >= qty) {
            portfolio.balance += cost;

            holding.qty -= qty;
            if (holding.qty === 0) {
                delete portfolio.holdings[currentStock]; // Remove if empty
            } else {
                portfolio.holdings[currentStock] = holding;
            }

            savePortfolio();
            showMessageModal("Trade Successful", createTradeSuccessContent("Sold", qty, currentStock, currentPrice));
        } else {
            showMessageModal("Trade Failed", "Insufficient holdings", true);
        }
    }
}

// --- Watchlist Logic ---
// let watchlist = JSON.parse(localStorage.getItem('watchlist')) || []; // Removed, defined at top

function loadWatchlistFromLocal() {
    const saved = localStorage.getItem('watchlist');
    if (saved) watchlist = JSON.parse(saved);
    updateWatchlistUI();
}

function toggleWatchlist() {
    if (!currentStock) return;

    const index = watchlist.indexOf(currentStock);
    if (index === -1) {
        watchlist.push(currentStock);
    } else {
        watchlist.splice(index, 1);
    }

    // localStorage.setItem('watchlist', JSON.stringify(watchlist));
    saveData();
    updateWatchlistUI();
    updateWatchlistButton();
}

function updateWatchlistUI() {
    const list = document.getElementById('watchlist');
    const heading = document.getElementById('watchlistHeading');
    if (!list) return;

    list.innerHTML = '';

    if (watchlist.length === 0) {
        list.innerHTML = '<li class="empty-message">No stocks in watchlist</li>';
        list.classList.add('is-empty');
        list.classList.remove('has-items');
        if (heading) {
            heading.classList.add('is-empty');
            heading.classList.remove('has-items');
        }
        return;
    }

    list.classList.remove('is-empty');
    list.classList.add('has-items');
    if (heading) {
        heading.classList.remove('is-empty');
        heading.classList.add('has-items');
    }

    watchlist.forEach(ticker => {
        const li = document.createElement('li');
        li.textContent = ticker;
        li.setAttribute('data-ticker', ticker);
        li.onclick = () => selectStock(ticker, li);
        if (ticker === currentStock) li.classList.add('active');
        list.appendChild(li);
    });

    checkWatchlistAlerts();
}

function updateWatchlistButton() {
    const btn = document.getElementById('addToWatchlistBtn');
    if (!currentStock) {
        btn.style.display = 'none';
        return;
    }

    btn.style.display = 'inline-block';
    if (watchlist.includes(currentStock)) {
        btn.textContent = '- Remove from Watchlist';
        btn.classList.add('btn-danger');
        btn.classList.remove('btn-secondary');
    } else {
        btn.textContent = '+ Add to Watchlist';
        btn.classList.add('btn-secondary');
        btn.classList.remove('btn-danger');
    }
}

// --- Tabs ---
function switchTab(tabId) {
    console.log("Switching to tab:", tabId);
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));

    document.getElementById(tabId + 'Tab').classList.add('active');

    // Identify the button that was clicked
    const btn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.toLowerCase().includes(tabId));
    if (btn) {
        btn.classList.add('active');
        updateTabGlider(btn);
    }
}

function updateTabGlider(btn) {
    const tabs = btn.closest('.tabs');
    if (!tabs) return;
    tabs.style.setProperty('--tab-left', btn.offsetLeft + 'px');
    tabs.style.setProperty('--tab-width', btn.offsetWidth + 'px');
}

function updateHorizonGlider(btn) {
    const selector = btn.closest('.horizon-selector');
    if (!selector) return;
    selector.style.setProperty('--horizon-left', btn.offsetLeft + 'px');
    selector.style.setProperty('--horizon-width', btn.offsetWidth + 'px');
}

// Initialize gliders on load
document.addEventListener('DOMContentLoaded', () => {
    const activeBtn = document.querySelector('.tab-btn.active');
    if (activeBtn) {
        setTimeout(() => updateTabGlider(activeBtn), 50);
    }
    const activeHorizonBtn = document.querySelector('.horizon-btn.active');
    if (activeHorizonBtn) {
        setTimeout(() => updateHorizonGlider(activeHorizonBtn), 50);
    }
});

// --- Profile Slide-out Logic ---
function openProfile() {
    updateProfileUI();
    document.getElementById('profilePanel').classList.add('active');
}

function closeProfile() {
    document.getElementById('profilePanel').classList.remove('active');
}

function updateProfileUI() {
    // 1. Live Date & Greeting
    const now = new Date();
    const dateOptions = { weekday: 'long', day: 'numeric', month: 'short' };
    const dateStr = now.toLocaleDateString('en-US', dateOptions);
    const dateEl = document.getElementById('profileLiveDate');
    if (dateEl) dateEl.textContent = dateStr;

    // Greeting Name
    let displayName = 'Trader';
    if (portfolio.profile && portfolio.profile.name) {
        displayName = portfolio.profile.name;
    } else if (currentUserEmail) {
        displayName = currentUserEmail.split('@')[0];
        displayName = displayName.charAt(0).toUpperCase() + displayName.slice(1);
    }
    const greetingEl = document.getElementById('profileGreeting');
    if (greetingEl) greetingEl.textContent = `Hello, ${displayName}!`;

    const nameInput = document.getElementById('profileNameInput');
    if (nameInput && portfolio.profile && portfolio.profile.name) {
        nameInput.value = portfolio.profile.name;
    }

    // User Info
    const emailEl = document.getElementById('profileEmail');
    if (emailEl) emailEl.textContent = currentUserEmail || 'Guest';

    const ageEl = document.getElementById('profileAge');
    const genderEl = document.getElementById('profileGender');
    const avatarEl = document.getElementById('profileAvatar');

    if (portfolio.profile && portfolio.profile.dob) {
        const dob = new Date(portfolio.profile.dob);
        const today = new Date();
        let age = today.getFullYear() - dob.getFullYear();
        const m = today.getMonth() - dob.getMonth();
        if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) {
            age--;
        }
        if (ageEl) ageEl.textContent = `Age ${age}`;

        const gender = portfolio.profile.gender || 'Other';
        if (genderEl) genderEl.textContent = `• ${gender}`;

        if (avatarEl) {
            let svgColor = '#60a5fa';
            if (gender === 'Female') svgColor = '#f472b6';
            avatarEl.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="22" height="22" style="color: ${svgColor};">
              <path fill-rule="evenodd" d="M7.5 6a4.5 4.5 0 119 0 4.5 4.5 0 01-9 0zM3.751 20.105a8.25 8.25 0 0116.498 0 .75.75 0 01-.437.695A18.683 18.683 0 0112 22.5c-2.786 0-5.433-.608-7.812-1.7a.75.75 0 01-.437-.695z" clip-rule="evenodd" />
            </svg>`;
        }
    } else {
        if (ageEl) ageEl.textContent = '18+';
        if (genderEl) genderEl.textContent = '• Member';
        if (avatarEl) {
            avatarEl.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="22" height="22" style="color: var(--accent);">
              <path fill-rule="evenodd" d="M7.5 6a4.5 4.5 0 119 0 4.5 4.5 0 01-9 0zM3.751 20.105a8.25 8.25 0 0116.498 0 .75.75 0 01-.437.695A18.683 18.683 0 0112 22.5c-2.786 0-5.433-.608-7.812-1.7a.75.75 0 01-.437-.695z" clip-rule="evenodd" />
            </svg>`;
        }
    }

    // 2. Calculate Stats & Portfolio Net Worth
    const cash = portfolio.balance;
    let investedValue = 0;
    let totalShares = 0;
    let activeHoldingsCount = 0;

    for (const [ticker, data] of Object.entries(portfolio.holdings)) {
        let qty = 0;
        let avgPrice = 0;

        if (typeof data === 'number') {
            qty = data;
            avgPrice = 0;
        } else if (data) {
            qty = data.qty || 0;
            avgPrice = data.avgPrice || 0;
        }

        if (qty > 0) {
            totalShares += qty;
            investedValue += (qty * avgPrice);
            activeHoldingsCount++;
        }
    }

    const netWorth = cash + investedValue;
    const currentCurrency = getUserCurrency();
    const currencyConfig = CURRENCY_CONFIG[currentCurrency] || CURRENCY_CONFIG['INR'];

    // Update Interactive Currency Toggle Badge in Wallet Header
    const currencyToggleBadge = document.getElementById('currencyToggleBadge');
    if (currencyToggleBadge) {
        currencyToggleBadge.textContent = currencyConfig.symbol.trim();
        currencyToggleBadge.title = `Current: ${currencyConfig.name} (${currencyConfig.symbol.trim()}) • Click to switch`;
    }

    // Sync Currency Select Dropdown in Settings Card
    const currencySelectEl = document.getElementById('profileCurrencySelect');
    if (currencySelectEl) {
        currencySelectEl.value = currentCurrency;
    }

    // Update Formatted Elements
    const netWorthEl = document.getElementById('profileNetWorth');
    if (netWorthEl) netWorthEl.textContent = formatWithCurrency(netWorth);

    const inrSubEl = document.getElementById('profileInrSub');
    if (inrSubEl) {
        const secondaryCode = (currentCurrency === 'INR') ? 'USD' : 'INR';
        const secondaryFormatted = formatWithCurrency(netWorth, secondaryCode);
        inrSubEl.textContent = `≈ ${secondaryFormatted} ${secondaryCode} • ${activeHoldingsCount} Active Holding${activeHoldingsCount === 1 ? '' : 's'}`;
    }

    const balanceEl = document.getElementById('profileBalance');
    if (balanceEl) balanceEl.textContent = formatWithCurrency(cash);

    const investedAmountEl = document.getElementById('profileInvestedAmount');
    if (investedAmountEl) investedAmountEl.textContent = formatWithCurrency(investedValue);

    const investedPillEl = document.getElementById('profileInvestedPill');
    if (investedPillEl) investedPillEl.textContent = `+${totalShares} Share${totalShares === 1 ? '' : 's'}`;

    // 3. Risk Level (LTV / Exposure) calculation
    const exposureRatio = netWorth > 0 ? (investedValue / netWorth) : 0;
    const riskPercent = (exposureRatio * 100).toFixed(1);

    const riskStateEl = document.getElementById('profileRiskState');
    const riskBadgeEl = document.getElementById('profileRiskBadge');
    const riskPercentEl = document.getElementById('profileRiskPercent');
    const gaugePathEl = document.getElementById('profileGaugePath');

    if (riskPercentEl) riskPercentEl.textContent = `${riskPercent}%`;

    let stateText = 'Optimal state';
    let badgeText = 'Good';
    let badgeColor = 'rgba(34, 197, 94, 0.15)';
    let badgeTextColor = 'var(--positive-text)';

    if (exposureRatio > 0.70) {
        stateText = 'Elevated Risk';
        badgeText = 'High LTV';
        badgeColor = 'rgba(239, 68, 68, 0.15)';
        badgeTextColor = 'var(--negative-text)';
    } else if (exposureRatio > 0.35) {
        stateText = 'Moderate state';
        badgeText = 'Balanced';
        badgeColor = 'rgba(245, 158, 11, 0.15)';
        badgeTextColor = 'var(--warning-text)';
    }

    if (riskStateEl) riskStateEl.textContent = stateText;
    if (riskBadgeEl) {
        riskBadgeEl.textContent = badgeText;
        riskBadgeEl.style.background = badgeColor;
        riskBadgeEl.style.color = badgeTextColor;
    }

    if (gaugePathEl) {
        // Arc total length is ~126. An offset of 126 is empty (0%), 0 is full (100%).
        const offset = 126 - (126 * Math.min(Math.max(exposureRatio, 0.08), 1));
        gaugePathEl.style.strokeDashoffset = offset;
    }
}

// Sell Holdings from Profile Logic
window.handleProfileSellClick = function () {
    const activeHoldings = [];
    for (const [ticker, data] of Object.entries(portfolio.holdings)) {
        let qty = typeof data === 'number' ? data : (data ? data.qty : 0);
        let avgPrice = typeof data === 'object' && data ? (data.avgPrice || 0) : 0;
        if (qty > 0) {
            activeHoldings.push({ ticker, qty, avgPrice });
        }
    }

    if (activeHoldings.length === 0) {
        closeProfile();
        if (window.showToast) {
            window.showToast("No active stock holdings found in your portfolio to sell.", "warning");
        } else {
            alert("You do not currently hold any shares to sell.");
        }
        return;
    }

    if (activeHoldings.length === 1) {
        closeProfile();
        selectStock(activeHoldings[0].ticker);
        setTimeout(() => {
            openTradeModal('SELL');
        }, 350);
        return;
    }

    // Multiple holdings -> Open Sell Holdings Selector Modal
    closeProfile();
    const modal = document.getElementById('sellHoldingsModal');
    const listEl = document.getElementById('sellHoldingsList');
    if (!modal || !listEl) return;

    listEl.innerHTML = '';
    activeHoldings.forEach(h => {
        const card = document.createElement('div');
        card.className = 'holding-sell-card';
        const formattedAvg = formatWithCurrency(h.avgPrice);
        
        const infoDiv = document.createElement('div');
        infoDiv.className = 'holding-sell-info';
        
        const tickerSpan = document.createElement('span');
        tickerSpan.className = 'holding-sell-ticker';
        tickerSpan.textContent = h.ticker;
        
        const qtySpan = document.createElement('span');
        qtySpan.className = 'holding-sell-qty';
        qtySpan.textContent = `Owned: ${Number(h.qty)} share${h.qty === 1 ? '' : 's'} ${h.avgPrice > 0 ? '• Avg ' + formattedAvg : ''}`;
        
        infoDiv.appendChild(tickerSpan);
        infoDiv.appendChild(qtySpan);
        
        const sellBtn = document.createElement('button');
        sellBtn.className = 'holding-sell-btn';
        sellBtn.textContent = 'Sell Shares';
        sellBtn.onclick = () => window.executeSellSelect(h.ticker);
        
        card.appendChild(infoDiv);
        card.appendChild(sellBtn);
        listEl.appendChild(card);
    });

    modal.classList.add('active');
};

window.executeSellSelect = function (ticker) {
    window.closeSellHoldingsModal();
    selectStock(ticker);
    setTimeout(() => {
        openTradeModal('SELL');
    }, 350);
};

window.closeSellHoldingsModal = function () {
    const modal = document.getElementById('sellHoldingsModal');
    if (modal) modal.classList.remove('active');
};

window.focusWatchlist = function () {
    closeProfile();
    switchTab('dashboard');
    const watchlistEl = document.getElementById('watchlist');
    if (watchlistEl) {
        watchlistEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
};

// Expose functions to window for HTML onclick access
window.switchTab = switchTab;
window.toggleWatchlist = toggleWatchlist;
window.openTradeModal = openTradeModal;
window.closeTradeModal = closeTradeModal;
window.closeMessageModal = closeMessageModal;
window.confirmTrade = confirmTrade;
window.updateModalTotal = updateModalTotal;
window.runBacktest = runBacktest;
window.selectStock = selectStock;
window.openProfile = openProfile;
window.closeProfile = closeProfile;
window.showMessageModal = showMessageModal;
window.createTradeSuccessContent = createTradeSuccessContent;

// --- Age Verification ---
function verifyAge() {
    const dobInput = document.getElementById('dobInput').value;
    const genderInput = document.getElementById('genderInput').value;
    const errorEl = document.getElementById('ageError');

    if (!dobInput || !genderInput) {
        errorEl.textContent = "Please enter your date of birth and select your gender.";
        errorEl.style.display = 'block';
        return;
    }

    const dob = new Date(dobInput);
    const today = new Date();

    let age = today.getFullYear() - dob.getFullYear();
    const m = today.getMonth() - dob.getMonth();

    // Adjust if birthday hasn't happened yet this year
    if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) {
        age--;
    }

    if (age >= 18) {
        // Verified
        if (!portfolio.profile) portfolio.profile = {};
        portfolio.profile.verified = true;
        portfolio.profile.dob = dobInput;
        portfolio.profile.gender = genderInput;

        saveData(); // Save status to backend

        document.getElementById('ageCheckModal').classList.remove('active');

        // Show success message temporarily?
        // alert("Verification Successful");
    } else {
        // Underage
        errorEl.textContent = "You must be 18 or older to use this platform.";
        errorEl.style.display = 'block';

        // Force logout after short delay
        setTimeout(() => {
            alert("Access Denied: Age Requirement Not Met.");
            window.logout();
        }, 1500);
    }
}

window.verifyAge = verifyAge;


// --- Feedback System ---

window.openFeedbackModal = function () {
    document.getElementById('feedbackModal').classList.add('active');
    // Reset form
    document.getElementById('feedbackMessage').value = '';
    window.setFeedbackRating(0);
}

window.closeFeedbackModal = function () {
    document.getElementById('feedbackModal').classList.remove('active');
}

window.setFeedbackRating = function (rating) {
    document.getElementById('selectedRating').value = rating;
    const stars = document.querySelectorAll('#feedbackRating span');
    stars.forEach(star => {
        const val = parseInt(star.getAttribute('data-value'));
        if (val <= rating) {
            star.classList.add('active');
            star.style.color = '#f59e0b';
        } else {
            star.classList.remove('active');
            star.style.color = '#94a3b8'; // var(--text-secondary)
        }
    });
}

window.submitFeedback = async function () {
    const message = document.getElementById('feedbackMessage').value.trim();
    const rating = document.getElementById('selectedRating').value;

    if (!message) {
        alert('Please enter a message.');
        return;
    }

    const btn = document.querySelector('#feedbackModal .btn-primary');
    const originalText = btn.innerText;
    btn.innerText = 'Sending...';
    btn.disabled = true;

    try {
        const response = await fetch('/api/feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: currentUserEmail || 'anonymous',
                message: message,
                rating: parseInt(rating)
            })
        });

        if (response.ok) {
            showMessageModal('Success', 'Thank you for your feedback!');
            window.closeFeedbackModal();
        } else {
            const data = await response.json();
            alert('Error: ' + data.error);
        }
    } catch (error) {
        console.error('Feedback error:', error);
        alert('Failed to send feedback.');
    } finally {
        btn.innerText = originalText;
        btn.disabled = false;
    }
}

// ==========================================================================
// Real-Time SSE Training Progress & Watchlist Alert System
// ==========================================================================

let activeEventSource = null;
let highConfidenceAlerts = new Set();

function startSSETrainingStream(ticker, horizon, onComplete) {
    if (activeEventSource) {
        activeEventSource.close();
        activeEventSource = null;
    }

    const container = document.getElementById('trainingProgressContainer');
    const badge = document.getElementById('progressStepBadge');
    const percentEl = document.getElementById('progressPercent');
    const barFill = document.getElementById('progressBarFill');
    const msgEl = document.getElementById('progressStatusMessage');

    if (container) container.style.display = 'block';
    if (badge) badge.textContent = 'Training';
    if (percentEl) percentEl.textContent = '0%';
    if (barFill) barFill.style.width = '0%';
    if (msgEl) msgEl.textContent = `Connecting live model training stream for ${ticker}...`;

    let eventCompleted = false;

    activeEventSource = new EventSource(`/api/stream_train/${encodeURIComponent(ticker)}?horizon=${horizon}`);

    activeEventSource.onmessage = function (event) {
        try {
            const data = JSON.parse(event.data);
            const step = data.step || 'Training';
            const progress = data.progress || 0;
            const message = data.message || 'Processing...';

            if (badge) badge.textContent = step;
            if (percentEl) percentEl.textContent = `${progress}%`;
            if (barFill) barFill.style.width = `${progress}%`;
            if (msgEl) msgEl.textContent = message;

            if (step === 'Completed' || progress >= 100) {
                eventCompleted = true;
                if (activeEventSource) {
                    activeEventSource.close();
                    activeEventSource = null;
                }
                setTimeout(() => {
                    if (container) container.style.display = 'none';
                    if (onComplete) onComplete();
                }, 600);
            } else if (step === 'Error') {
                eventCompleted = true;
                if (activeEventSource) {
                    activeEventSource.close();
                    activeEventSource = null;
                }
                if (msgEl) msgEl.textContent = `Error: ${message}`;
            }
        } catch (e) {
            console.error("SSE parse error:", e);
        }
    };

    activeEventSource.onerror = function () {
        if (activeEventSource) {
            activeEventSource.close();
            activeEventSource = null;
        }
        if (!eventCompleted) {
            if (container) container.style.display = 'none';
            if (onComplete) onComplete();
        }
    };
}

function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission().then(permission => {
            console.log("Browser Notification permission:", permission);
        });
    }
}

function showAlertToast(alertData) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = 'toast-card';
    toast.innerHTML = `
        <div class="toast-header">
            <div class="toast-title">
                <span>🚀 High Confidence BUY Signal</span>
            </div>
            <button class="toast-close" onclick="this.closest('.toast-card').remove()">&times;</button>
        </div>
        <div class="toast-body">
            <strong>${escapeHtml(alertData.ticker)}</strong> hit <strong>${Number(alertData.confidence)}% Confidence</strong>!
            <div class="toast-driver">Top Driver: ${escapeHtml(alertData.driver)}</div>
        </div>
    `;

    toast.onclick = (e) => {
        if (!e.target.classList.contains('toast-close')) {
            selectStock(alertData.ticker);
        }
    };

    container.appendChild(toast);

    setTimeout(() => {
        if (toast.parentNode) {
            toast.remove();
        }
    }, 8000);
}

function triggerDesktopNotification(alertData) {
    if ('Notification' in window && Notification.permission === 'granted') {
        try {
            new Notification(`🚀 High-Confidence BUY Signal: ${alertData.ticker}`, {
                body: `Confidence: ${alertData.confidence}% | ${alertData.driver}`,
                icon: '/static/favicon.ico'
            });
        } catch (e) {
            console.error("Desktop notification error:", e);
        }
    }
}

async function checkWatchlistAlerts() {
    if (!watchlist || watchlist.length === 0) return;

    try {
        const response = await fetch('/api/watchlist_alerts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ watchlist: watchlist })
        });
        const alerts = await response.json();

        if (Array.isArray(alerts)) {
            alerts.forEach(alertData => {
                const alertKey = `${alertData.ticker}_${alertData.confidence}`;
                if (!highConfidenceAlerts.has(alertKey)) {
                    highConfidenceAlerts.add(alertKey);
                    showAlertToast(alertData);
                    triggerDesktopNotification(alertData);
                }
            });
            updateWatchlistAlertBadges(alerts);
        }
    } catch (e) {
        console.error("Watchlist alert check error:", e);
    }
}

function updateWatchlistAlertBadges(alerts) {
    const alertMap = new Map((alerts || []).map(a => [a.ticker, a.confidence]));
    const items = document.querySelectorAll('#watchlist li');
    items.forEach(li => {
        const ticker = li.getAttribute('data-ticker') || li.textContent.trim().split(' ')[0];
        let badge = li.querySelector('.watchlist-alert-badge');
        if (alertMap.has(ticker)) {
            const conf = alertMap.get(ticker);
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'watchlist-alert-badge';
                li.appendChild(badge);
            }
            badge.textContent = `🔥 ${conf}%`;
        } else if (badge) {
            badge.remove();
        }
    });
}



