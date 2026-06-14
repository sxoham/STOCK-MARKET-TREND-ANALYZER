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

let portfolio = {
    balance: 10000,
    holdings: {} // ticker: quantity
};

let watchlist = []; // Initialize empty, load later

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
            loadRemoteData(user.email);
        } else {
            console.log("No user logged in, redirecting...");
            window.location.href = '/login';
        }
    });

    // Load stocks immediately (public data)
    fetchStocks();

    // Search Listener
});

// Search Listener
const searchInput = document.getElementById('stockSearchInput');
if (searchInput) {
    searchInput.addEventListener('input', debounce(handleSearch, 300));

    // Hide dropdown on click outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.stock-search-container')) {
            document.getElementById('searchResults').style.display = 'none';
        }
    });

    // Enter key
    searchInput.addEventListener('keypress', function (e) {
        if (e.key === 'Enter') {
            const query = this.value.trim().toUpperCase();
            if (query) {
                selectStock(query);
                document.getElementById('searchResults').style.display = 'none';
            }
        }
    });
}
// End of Search Listener logic

// --- Search & Autocomplete ---

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
                    <div style="font-weight: bold;">${item.symbol}</div>
                    <small>${item.shortname} (${item.exchange})</small>
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

async function loadRemoteData(email) {
    try {
        const response = await fetch(`/api/get_data/${email}?t=${Date.now()}`);
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
            // Optional: Try to load from localStorage if we want to migrate, 
            // but simpler to just start fresh or use what's in memory variables
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
        await fetch('/api/save_data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: currentUserEmail, data: data })
        });
        console.log("Data saved to server");
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
            li.onclick = () => selectStock(stock, li);
            list.appendChild(li);
        });
    } catch (error) {
        console.error('Error fetching stocks:', error);
    }
}

// Select a stock
async function selectStock(ticker, element) {
    currentStock = ticker;

    // Update UI selection
    // Update UI selection
    if (element) {
        document.querySelectorAll('.stock-list li').forEach(li => li.classList.remove('active'));
        element.classList.add('active');
    }

    // Show refresh button
    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) refreshBtn.style.display = 'block';

    // Also highlight in watchlist if present
    updateWatchlistUI();

    document.getElementById('selectedStockTitle').textContent = ticker;
    document.getElementById('statusIndicator').textContent = 'Loading data... (May train if new)';
    document.getElementById('statusIndicator').style.color = '#f59e0b'; // Amber for processing

    updateWatchlistButton();

    // Reset Backtest UI
    resetBacktestUI();

    try {
        const response = await fetch(`/api/predict/${ticker}`);
        const data = await response.json();

        if (data.error) {
            alert(data.error);
            document.getElementById('statusIndicator').textContent = 'Error loading data';
            return;
        }

        updateDashboard(data);
        const time = new Date().toLocaleTimeString();
        document.getElementById('statusIndicator').textContent = 'Live Data - ' + time;

        // Fetch Sentiment separately
        fetchSentiment(ticker);

    } catch (error) {
        console.error('Error loading stock data:', error);
        // Alert only if it's a real error, not just a cancel. 
        // For new stocks, it might timeout or standard error.
        alert("Error: " + error.message + ". If this is a new stock, check if the ticker is valid.");
        document.getElementById('statusIndicator').textContent = 'Error: ' + error.message;
        document.getElementById('statusIndicator').style.color = 'var(--danger-color)';
    }
}

async function fetchSentiment(ticker) {
    const badge = document.getElementById('sentimentBadge');
    const score = document.getElementById('sentimentScore');
    const list = document.getElementById('newsList');

    badge.textContent = 'Loading...';
    badge.className = 'sentiment-badge';
    score.textContent = '--';
    list.innerHTML = '';

    try {
        const response = await fetch(`/api/sentiment/${ticker}`);
        const data = await response.json();

        if (data.error) return;

        badge.textContent = data.label;
        badge.className = `sentiment-badge ${data.label}`;
        score.textContent = data.score.toFixed(2);

        data.headlines.forEach(item => {
            const li = document.createElement('li');
            li.innerHTML = `<a href="${item.link}" target="_blank">${item.title}</a>`;
            list.appendChild(li);
        });

    } catch (error) {
        console.error('Sentiment error:', error);
        badge.textContent = 'Error';
    }
}

// Update Dashboard UI
function updateDashboard(data) {
    // Prediction
    const predValue = document.getElementById('predictionValue');
    const predProb = document.getElementById('predictionProb');

    predValue.textContent = data.prediction;
    predValue.className = `prediction-value ${data.prediction.toLowerCase()}`;
    predProb.textContent = (data.probability * 100).toFixed(2);

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
        const response = await fetch('/api/delete_data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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

    // Color based on rating
    let color = '#94a3b8'; // Neutral
    if (score >= 4) color = '#22c55e'; // Strong Buy
    else if (score >= 1) color = '#4ade80'; // Buy
    else if (score <= -4) color = '#ef4444'; // Strong Sell
    else if (score <= -1) color = '#f87171'; // Sell

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
    const ctx = document.getElementById('macdChart').getContext('2d');
    if (macdChart) macdChart.destroy();

    macdChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: history.dates,
            datasets: [{
                label: 'MACD',
                data: history.macd,
                backgroundColor: history.macd.map(v => v >= 0 ? '#4ade80' : '#f87171')
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

        document.getElementById('btTotalReturn').textContent = data.metrics.total_return.toFixed(2) + '%';
        document.getElementById('btMarketReturn').textContent = data.metrics.market_return.toFixed(2) + '%';
        document.getElementById('btWinRate').textContent = data.metrics.win_rate.toFixed(2) + '%';

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
    document.getElementById('btTotalReturn').textContent = '--';
    document.getElementById('btMarketReturn').textContent = '--';
    document.getElementById('btWinRate').textContent = '--';
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
    // localStorage.setItem('portfolio', JSON.stringify(portfolio)); // Legacy
    saveData();
    updatePortfolioUI();
}

function updatePortfolioUI() {
    document.getElementById('portfolioBalance').textContent = '$' + portfolio.balance.toFixed(2);

    const list = document.getElementById('holdingsList');
    list.innerHTML = '';

    for (const [ticker, data] of Object.entries(portfolio.holdings)) {
        // data might be number (if not migrated yet) or object
        const qty = (typeof data === 'object') ? data.qty : data;
        const avgPrice = (typeof data === 'object') ? data.avgPrice : 0;

        if (qty > 0) {
            const div = document.createElement('div');
            div.className = 'holding-item';
            // Optional: Show Avg Price here too? For now just keep it clean
            div.innerHTML = `<span>${ticker}</span> <span>${qty} shares</span>`;
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

    title.textContent = `${type === 'BUY' ? 'Buy' : 'Sell'} ${currentStock} `;
    priceEl.textContent = `Current Price: $${currentPrice.toFixed(2)} `;

    // Show available balance or shares
    let avgPrice = 0;
    if (type === 'BUY') {
        availEl.textContent = `Available Balance: $${portfolio.balance.toFixed(2)} `;
        availEl.style.color = 'var(--success-color)';
    } else {
        let holding = portfolio.holdings[currentStock];
        if (typeof holding === 'number') holding = { qty: holding, avgPrice: 0 };
        const shares = holding ? holding.qty : 0;
        avgPrice = holding ? holding.avgPrice : 0;

        availEl.textContent = `Available Shares: ${shares} ${avgPrice > 0 ? '(Avg Buy: $' + avgPrice.toFixed(2) + ')' : ''}`;
        availEl.style.color = 'var(--text-secondary)';
    }

    // Pass avgPrice to updateModalTotal via global or attr? 
    // Easier to just read from portfolio in updateModalTotal since currentStock is global.

    // Reset inputs
    document.getElementById('modalQty').value = 10;
    updateModalTotal();

    // Btn styling
    btn.className = type === 'BUY' ? 'btn-primary' : 'btn-danger';
    btn.textContent = type === 'BUY' ? 'Confirm Buy' : 'Confirm Sell';

    // modal.style.display = 'block'; // Use class for flex display (centering)
    modal.classList.add('active');
}

function closeTradeModal() {
    // document.getElementById('tradeModal').style.display = 'none';
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
    document.getElementById('modalTotalCost').textContent = `Total: $${total.toFixed(2)} `;

    // Calculate Balance After
    let balanceAfter = portfolio.balance;
    let balanceColor = 'var(--text-secondary)'; // Default

    if (pendingTradeType === 'BUY') {
        balanceAfter -= total;
        // Spending money -> Red indicating outflow/cost
        balanceColor = 'var(--danger-color)';
    } else if (pendingTradeType === 'SELL') {
        balanceAfter += total;

        // P/L Check
        // P/L Check
        let holding = portfolio.holdings[currentStock];
        if (typeof holding === 'number') holding = { qty: holding, avgPrice: 0 };

        let avgBuyPrice = 0;
        if (holding) {
            // robust check for keys
            avgBuyPrice = parseFloat(holding.avgPrice || holding.avg_price || 0);
        }

        console.log(`Debug P/L: Stock=${currentStock}, AvgBuy=${avgBuyPrice}, Curr=${currentPrice}`);

        // If we have history, compare prices
        if (avgBuyPrice > 0) {
            const diff = currentPrice - avgBuyPrice;
            const totalPL = diff * qty;

            let label = "Profit";
            if (diff >= 0) {
                // Floating point or 0 -> Profit (or Break Even treated as Profit side)
                balanceColor = diff > 0.0001 ? 'var(--success-color)' : 'var(--text-secondary)';
                label = "Profit";
            } else {
                balanceColor = 'var(--danger-color)';
                label = "Loss";
            }

            if (balanceAfterEl) {
                balanceAfterEl.textContent = `Balance After: $${balanceAfter.toFixed(2)} (${label}: $${Math.abs(totalPL).toFixed(2)} | $${Math.abs(diff).toFixed(2)}/share)`;
                balanceAfterEl.style.color = balanceColor;
                balanceAfterEl.style.fontWeight = '600';
            }
            return;
        } else {
            // Debugging Fallback: Show why it failed
            balanceColor = 'var(--text-secondary)';
            if (balanceAfterEl) balanceAfterEl.textContent = `Balance After: $${balanceAfter.toFixed(2)} (No Hist. Price)`;
        }
    }

    if (balanceAfterEl) {
        balanceAfterEl.textContent = `Balance After: $${balanceAfter.toFixed(2)}`;
        balanceAfterEl.style.color = balanceColor;
        balanceAfterEl.style.fontWeight = '600';
    }
}

function confirmTrade() {
    if (!pendingTradeType) return;

    const qty = parseInt(document.getElementById('modalQty').value);

    if (isNaN(qty) || qty <= 0) {
        // alert("Please enter a valid quantity greater than 0");
        showMessageModal("Invalid Input", "Please enter a valid quantity greater than 0", true);
        return;
    }

    executeTrade(pendingTradeType, qty);
    closeTradeModal();
}

// --- Message Modal Logic ---
function showMessageModal(title, message, isError = false) {
    const modal = document.getElementById('messageModal');
    const titleEl = document.getElementById('msgModalTitle');
    const contentEl = document.getElementById('msgModalContent');

    titleEl.textContent = title;
    // titleEl.style.color = isError ? 'var(--danger-color)' : 'var(--text-primary)';
    contentEl.innerHTML = message;

    modal.classList.add('active');
}

function closeMessageModal() {
    document.getElementById('messageModal').classList.remove('active');
}

function executeTrade(type, qty) {
    if (!currentStock || !currentPrice) return;

    const cost = currentPrice * qty;

    if (type === 'BUY') {
        if (portfolio.balance >= cost) {
            portfolio.balance -= cost;

            // Get existing holding data
            let holding = portfolio.holdings[currentStock];
            // Normalize (if legacy number or undefined)
            if (typeof holding === 'number') holding = { qty: holding, avgPrice: 0 };
            if (!holding) holding = { qty: 0, avgPrice: 0 };

            // Calculate Weighted Average Price
            // NewAvg = ((OldQty * OldAvg) + (BuyQty * BuyPrice)) / (OldQty + BuyQty)
            const oldTotalVal = holding.qty * holding.avgPrice;
            const newTotalVal = oldTotalVal + (qty * currentPrice);
            const totalQty = holding.qty + qty;

            holding.avgPrice = newTotalVal / totalQty;
            holding.qty = totalQty;

            portfolio.holdings[currentStock] = holding;

            savePortfolio();
            showMessageModal("Trade Successful", `Bought <span class="highlight-text">${qty}</span> shares of ${currentStock} at <span class="highlight-text">$${currentPrice.toFixed(2)}</span>`);
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
            // Avg Price doesn't change on Sell
            if (holding.qty === 0) {
                delete portfolio.holdings[currentStock]; // Remove if empty
            } else {
                portfolio.holdings[currentStock] = holding;
            }

            savePortfolio();
            showMessageModal("Trade Successful", `Sold <span class="highlight-text">${qty}</span> shares of ${currentStock} at <span class="highlight-text">$${currentPrice.toFixed(2)}</span>`);
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
    list.innerHTML = '';

    if (watchlist.length === 0) {
        list.innerHTML = '<li class="empty-message">No stocks in watchlist</li>';
        return;
    }

    watchlist.forEach(ticker => {
        const li = document.createElement('li');
        li.textContent = ticker;
        li.onclick = () => selectStock(ticker, li);
        if (ticker === currentStock) li.classList.add('active');
        list.appendChild(li);
    });
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

// Initialize glider on load
document.addEventListener('DOMContentLoaded', () => {
    const activeBtn = document.querySelector('.tab-btn.active');
    if (activeBtn) {
        // Warning: Element might not have layout yet if mostly hidden or during initial render frame.
        // Small timeout helps ensure layout is settled.
        setTimeout(() => updateTabGlider(activeBtn), 50);
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
    // User Info
    document.getElementById('profileEmail').textContent = currentUserEmail || 'Guest';

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
        ageEl.textContent = `Age: ${age}`;

        // Gender & Avatar
        const gender = portfolio.profile.gender || 'Other';
        genderEl.textContent = gender;

        avatarEl.innerHTML = ''; // Clear text content
        let svgIcon = '';

        if (gender === 'Male') {
            svgIcon = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="48" height="48" style="color: #60a5fa;">
              <path fill-rule="evenodd" d="M7.5 6a4.5 4.5 0 119 0 4.5 4.5 0 01-9 0zM3.751 20.105a8.25 8.25 0 0116.498 0 .75.75 0 01-.437.695A18.683 18.683 0 0112 22.5c-2.786 0-5.433-.608-7.812-1.7a.75.75 0 01-.437-.695z" clip-rule="evenodd" />
            </svg>`;
        } else if (gender === 'Female') {
            svgIcon = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="48" height="48" style="color: #f472b6;">
              <path fill-rule="evenodd" d="M18.685 19.097A9.723 9.723 0 0021.75 12c0-5.385-4.365-9.75-9.75-9.75S2.25 6.615 2.25 12a9.723 9.723 0 003.065 7.097A9.716 9.716 0 0012 21.75a9.716 9.716 0 006.685-2.653zm-12.54-1.285A7.486 7.486 0 0112 15a7.486 7.486 0 015.855 2.812A8.224 8.224 0 0112 20.25a8.224 8.224 0 01-5.855-2.438zM15.75 9a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z" clip-rule="evenodd" />
            </svg>`;
        } else {
            svgIcon = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="48" height="48" style="color: var(--text-secondary);">
              <path fill-rule="evenodd" d="M18.685 19.097A9.723 9.723 0 0021.75 12c0-5.385-4.365-9.75-9.75-9.75S2.25 6.615 2.25 12a9.723 9.723 0 003.065 7.097A9.716 9.716 0 0012 21.75a9.716 9.716 0 006.685-2.653zm-12.54-1.285A7.486 7.486 0 0112 15a7.486 7.486 0 015.855 2.812A8.224 8.224 0 0112 20.25a8.224 8.224 0 01-5.855-2.438zM15.75 9a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z" clip-rule="evenodd" />
            </svg>`;
        }
        avatarEl.innerHTML = svgIcon;
    } else {
        ageEl.textContent = '';
        genderEl.textContent = '';
        avatarEl.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="48" height="48" style="color: var(--text-secondary);">
              <path fill-rule="evenodd" d="M7.5 6a4.5 4.5 0 119 0 4.5 4.5 0 01-9 0zM3.751 20.105a8.25 8.25 0 0116.498 0 .75.75 0 01-.437.695A18.683 18.683 0 0112 22.5c-2.786 0-5.433-.608-7.812-1.7a.75.75 0 01-.437-.695z" clip-rule="evenodd" />
            </svg>`;
    }

    // Calculate Stats
    // Calculate Stats
    const cash = portfolio.balance;
    let investedValue = 0;
    let totalShares = 0;

    for (const [ticker, data] of Object.entries(portfolio.holdings)) {
        // Handle new object structure vs legacy number
        let qty = 0;
        let avgPrice = 0;

        if (typeof data === 'number') {
            qty = data;
            avgPrice = 0; // Unknown legacy price
        } else {
            qty = data.qty;
            avgPrice = data.avgPrice;
        }

        if (qty > 0) {
            totalShares += qty;
            investedValue += (qty * avgPrice);
        }
    }

    document.getElementById('profileBalance').textContent = '$' + cash.toFixed(2);

    // Show Invested Shares
    document.getElementById('profileInvested').textContent = `${totalShares} shares`;

    // Show Invested Amount (Cost Basis)
    const investedAmountEl = document.getElementById('profileInvestedAmount');
    if (investedAmountEl) {
        investedAmountEl.textContent = `$${investedValue.toFixed(2)}`;
    }

    // Total Net Worth (Cash + Invested Cost)
    const netWorth = cash + investedValue;
    document.getElementById('profileNetWorth').textContent = '$' + netWorth.toFixed(2);
}

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


