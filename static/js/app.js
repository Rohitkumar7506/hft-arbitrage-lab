const state = {
  selectedStock: null,
  selectedRange: "1M",
  searchResults: [],
  chart: null,
};

const $ = (id) => document.getElementById(id);

function setHidden(element, hidden) {
  element.classList.toggle("hidden", hidden);
}

function showGlobalLoading(message) {
  $("globalLoading").textContent = message;
  setHidden($("globalLoading"), false);
}

function hideGlobalLoading() {
  setHidden($("globalLoading"), true);
}

function showError(message) {
  $("globalError").textContent = message;
  setHidden($("globalError"), false);
}

function clearError() {
  $("globalError").textContent = "";
  setHidden($("globalError"), true);
}

async function requestJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const error = new Error(payload.message || "Market data is currently unavailable.");
    error.payload = payload;
    throw error;
  }
  return payload;
}

const BADGE_CLASSES = { live: "data-live", cached: "data-cached", unavailable: "data-unavailable" };
const BADGE_LABELS = { live: "LIVE API DATA", cached: "CACHED DATA", unavailable: "DATA UNAVAILABLE" };

function setDataBadge(id, status) {
  const badge = $(id);
  badge.classList.remove("data-live", "data-cached", "data-unavailable");
  if (!BADGE_CLASSES[status]) {
    badge.textContent = "";
    setHidden(badge, true);
    return;
  }
  badge.textContent = BADGE_LABELS[status];
  badge.classList.add(BADGE_CLASSES[status]);
  setHidden(badge, false);
}

function formatSavedAt(isoString) {
  const date = new Date(isoString);
  return Number.isNaN(date.getTime()) ? "earlier" : date.toLocaleString();
}

function setButtonBusy(button, busy, busyText) {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

function renderSearchResults(results) {
  state.searchResults = results;
  const section = $("resultsSection");
  const list = $("resultsList");
  $("resultCount").textContent = `${results.length} RESULT${results.length === 1 ? "" : "S"}`;
  list.innerHTML = "";
  setHidden(section, false);

  if (!results.length) {
    list.innerHTML = '<div class="empty-results">No matching stock was found.</div>';
    return;
  }

  results.forEach((stock, index) => {
    const card = document.createElement("article");
    card.className = "result-card";
    card.innerHTML = `
      <div class="result-details">
        <div class="result-name">${escapeHtml(stock.name)}</div>
        <div class="result-meta">
          <strong>${escapeHtml(stock.symbol)}</strong>
          <span>·</span>
          <span>${escapeHtml(stock.exchange)}</span>
          <span>·</span>
          <span>${escapeHtml(stock.country)}</span>
          <span>·</span>
          <span>${escapeHtml(stock.type)}</span>
          ${stock.source === "cache" ? `<span class="data-badge data-cached">${BADGE_LABELS.cached}</span>` : ""}
        </div>
      </div>
      <button class="button button-ghost view-stock" type="button" data-index="${index}">View stock <span class="button-arrow">↗</span></button>
    `;
    card.querySelector(".view-stock").addEventListener("click", () => selectStock(stock));
    list.appendChild(card);
  });
}

async function searchStocks(queryOverride) {
  const input = $("searchInput");
  const query = (queryOverride || input.value).trim();
  if (!query) {
    showError("Enter a stock symbol or company name.");
    input.focus();
    return;
  }

  input.value = query;
  clearError();
  showGlobalLoading("Searching market data...");
  const button = $("searchButton");
  setButtonBusy(button, true, "Searching...");

  try {
    const payload = await requestJson(`/api/search?q=${encodeURIComponent(query)}`);
    renderSearchResults(payload.results || []);
    if (!payload.results || payload.results.length === 0) {
      showError("No matching stock was found.");
    } else if (payload.data_status === "cached" && payload.message) {
      showError(payload.message);
    }
    $("resultsSection").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    renderSearchResults([]);
    showError(error.message);
  } finally {
    hideGlobalLoading();
    setButtonBusy(button, false);
  }
}

function selectStock(stock) {
  state.selectedStock = stock;
  setHidden($("selectedSection"), false);
  $("companyName").textContent = stock.name || "Selected instrument";
  $("instrumentSymbol").textContent = stock.symbol || "N/A";
  $("instrumentExchange").textContent = stock.exchange || "N/A";
  $("instrumentCountry").textContent = stock.country || "N/A";
  $("chartTitle").textContent = `${stock.symbol || "Instrument"} · ${stock.exchange || "Exchange"} price history`;
  setDataBadge("quoteSourceBadge", null);
  setDataBadge("chartSourceBadge", null);
  $("selectedSection").scrollIntoView({ behavior: "smooth", block: "start" });
  loadQuote();
  loadHistory(state.selectedRange);
}

async function loadQuote() {
  if (!state.selectedStock) return;
  try {
    const params = new URLSearchParams({
      symbol: state.selectedStock.symbol,
      exchange: state.selectedStock.exchange === "N/A" ? "" : state.selectedStock.exchange,
    });
    const payload = await requestJson(`/api/quote?${params.toString()}`);
    renderQuote(payload.quote || {}, payload.data_status, payload.cached_at);
  } catch (error) {
    showError(error.message);
    renderQuote({}, "unavailable");
  }
}

function renderQuote(quote, dataStatus, cachedAt) {
  $("companyName").textContent = quote.company || state.selectedStock?.name || "Selected instrument";
  $("instrumentSymbol").textContent = knownQuoteValue(quote.symbol, state.selectedStock?.symbol);
  $("instrumentExchange").textContent = knownQuoteValue(quote.exchange, state.selectedStock?.exchange);
  $("instrumentCountry").textContent = knownQuoteValue(quote.country, state.selectedStock?.country);
  $("quotePrice").textContent = quote.price || "N/A";
  $("quoteCurrency").textContent = quote.currency && quote.currency !== "N/A" ? quote.currency : "Currency N/A";
  $("quoteChange").textContent = quote.change || "N/A";
  $("quoteChangePercent").textContent = quote.change_percent && quote.change_percent !== "N/A" ? `${quote.change_percent}%` : "N/A";
  $("quoteOpen").textContent = quote.open || "N/A";
  $("quoteHighLow").textContent = `${quote.high || "N/A"} / ${quote.low || "N/A"}`;
  $("quotePreviousClose").textContent = quote.previous_close || "N/A";
  $("quoteVolume").textContent = quote.volume || "N/A";
  $("quoteTimestamp").textContent =
    dataStatus === "cached"
      ? `Cached · saved ${formatSavedAt(cachedAt)}`
      : quote.datetime && quote.datetime !== "N/A"
        ? `Quote · ${quote.datetime}`
        : "Latest available quote";
  setDataBadge("quoteSourceBadge", dataStatus);

  const marketStatus = $("marketStatus");
  marketStatus.textContent = quote.market_status || "Unknown";
  marketStatus.className = quote.market_status === "Open" ? "market-open" : quote.market_status === "Closed" ? "market-closed" : "market-unknown";
  applyChangeColor($("quoteChange"), quote.change);
  applyChangeColor($("quoteChangePercent"), quote.change_percent);
}

function knownQuoteValue(value, fallback) {
  return value && value !== "N/A" ? value : fallback || "N/A";
}

async function loadHistory(range) {
  if (!state.selectedStock) return;
  state.selectedRange = range;
  document.querySelectorAll(".range-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.range === range);
  });
  setChartState("Loading historical data...", true);
  setDataBadge("chartSourceBadge", null);
  try {
    const params = new URLSearchParams({
      symbol: state.selectedStock.symbol,
      exchange: state.selectedStock.exchange === "N/A" ? "" : state.selectedStock.exchange,
      range,
    });
    const payload = await requestJson(`/api/history?${params.toString()}`);
    if (!payload.points || payload.points.length === 0) {
      setChartState("No historical data available.", false, true);
      setDataBadge("chartSourceBadge", "unavailable");
      return;
    }
    renderChart(payload.points);
    setDataBadge("chartSourceBadge", payload.data_status);
  } catch (error) {
    setChartState(error.message || "Historical data is currently unavailable for this stock.", false, true);
    setDataBadge("chartSourceBadge", "unavailable");
  }
}

function renderChart(points) {
  setHidden($("chartState"), true);
  setHidden($("chartWrap"), false);
  if (state.chart) state.chart.destroy();
  const context = $("priceChart").getContext("2d");
  const gradient = context.createLinearGradient(0, 0, 0, 340);
  gradient.addColorStop(0, "rgba(85, 224, 179, 0.28)");
  gradient.addColorStop(1, "rgba(85, 224, 179, 0)");
  state.chart = new Chart(context, {
    type: "line",
    data: {
      labels: points.map((point) => point.time),
      datasets: [{
        data: points.map((point) => point.price),
        borderColor: "#55e0b3",
        backgroundColor: gradient,
        borderWidth: 2,
        fill: true,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: "#55e0b3",
        tension: 0.25,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#0b171c",
          borderColor: "rgba(85,224,179,0.35)",
          borderWidth: 1,
          displayColors: false,
          padding: 11,
          titleColor: "#a8b9b9",
          bodyColor: "#55e0b3",
          callbacks: { label: (context) => `  ${formatNumber(context.parsed.y)}` },
        },
      },
      scales: {
        x: {
          grid: { color: "rgba(159,191,196,0.08)" },
          ticks: { color: "#718589", maxTicksLimit: 7, font: { family: "DM Mono", size: 10 } },
          border: { color: "rgba(159,191,196,0.15)" },
        },
        y: {
          grid: { color: "rgba(159,191,196,0.08)" },
          ticks: { color: "#718589", font: { family: "DM Mono", size: 10 }, callback: (value) => formatNumber(value) },
          border: { color: "rgba(159,191,196,0.15)" },
        },
      },
    },
  });
}

function setChartState(message, loading, error) {
  setHidden($("chartWrap"), true);
  setHidden($("chartState"), false);
  $("chartState").className = `chart-state${loading ? " loading" : ""}${error ? " error" : ""}`;
  $("chartState").querySelector("p").textContent = message;
}

async function refreshStock() {
  if (!state.selectedStock) return;
  clearError();
  const button = $("refreshButton");
  setButtonBusy(button, true, "Refreshing...");
  await Promise.all([loadQuote(), loadHistory(state.selectedRange)]);
  setButtonBusy(button, false);
}

async function checkApiStatus() {
  try {
    const payload = await requestJson("/api/status");
    const status = $("apiStatus");
    status.className = `api-status ${payload.reachable ? "status-connected" : "status-disconnected"}`;
    $("apiStatusLabel").textContent = payload.label || "API DISCONNECTED";
  } catch {
    $("apiStatus").className = "api-status status-disconnected";
    $("apiStatusLabel").textContent = "API DISCONNECTED";
  }
}

function applyChangeColor(element, value) {
  element.classList.remove("positive", "negative");
  if (value === undefined || value === null || value === "N/A") return;
  const numeric = Number.parseFloat(String(value).replace("%", "").replace(",", ""));
  if (Number.isNaN(numeric)) return;
  element.classList.add(numeric >= 0 ? "positive" : "negative");
}

function formatNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "N/A";
}

function escapeHtml(value) {
  return String(value ?? "N/A").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}

$("searchForm").addEventListener("submit", (event) => {
  event.preventDefault();
  searchStocks();
});

$("refreshButton").addEventListener("click", refreshStock);

document.querySelectorAll(".quick-symbol").forEach((button) => {
  button.addEventListener("click", () => searchStocks(button.dataset.symbol));
});

document.querySelectorAll(".range-button").forEach((button) => {
  button.addEventListener("click", () => loadHistory(button.dataset.range));
});

checkApiStatus();