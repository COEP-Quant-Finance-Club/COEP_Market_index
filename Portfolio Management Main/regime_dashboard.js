/**
 * QFC Alpha Terminal - 3-State HMM Sector Regime Dashboard JS
 * Dynamic TradingView Lightweight Charts & Candle Smoothing ($k = 1 \dots 21$)
 */

document.addEventListener("DOMContentLoaded", () => {
    const data = window.REGIME_ANALYSIS_DATA;
    if (!data) {
        console.error("REGIME_ANALYSIS_DATA not found!");
        return;
    }

    let activeK = 5;
    let selectedSector = "BANKING";
    let activeFilter = "ALL";
    let searchTerm = "";

    // Chart Handles
    let priceChart = null;
    let candleSeries = null;
    
    let probChart = null;
    let bullProbSeries = null;
    let neutralProbSeries = null;
    let bearProbSeries = null;

    // DOM Elements
    const sectorListContainer = document.getElementById("sectorListContainer");
    const chartSectorTitle = document.getElementById("chartSectorTitle");
    const chartStateBadge = document.getElementById("chartStateBadge");
    const chartStateText = document.getElementById("chartStateText");
    const bullProbVal = document.getElementById("bullProbVal");
    const neutralProbVal = document.getElementById("neutralProbVal");
    const bearProbVal = document.getElementById("bearProbVal");
    const activeKDisplay = document.getElementById("activeKDisplay");
    const visibleSectorsCount = document.getElementById("visibleSectorsCount");
    const leadingRadarList = document.getElementById("leadingRadarList");
    const smoothingBenchmarkContainer = document.getElementById("smoothingBenchmarkContainer");

    // Initialize Lightweight Charts
    function initCharts() {
        const priceContainer = document.getElementById("regimePriceChart");
        const probContainer = document.getElementById("regimeProbChart");

        priceContainer.innerHTML = "";
        probContainer.innerHTML = "";

        // Main Price Chart
        priceChart = LightweightCharts.createChart(priceContainer, {
            layout: {
                backgroundColor: '#121824',
                textColor: '#94a3b8',
                fontSize: 11,
                fontFamily: 'Inter'
            },
            grid: {
                vertLines: { color: '#1e293b' },
                horzLines: { color: '#1e293b' }
            },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            rightPriceScale: { borderColor: '#1e293b' },
            timeScale: { borderColor: '#1e293b', timeVisible: true }
        });

        candleSeries = priceChart.addCandlestickSeries({
            upColor: '#10b981',
            downColor: '#ef4444',
            borderUpColor: '#10b981',
            borderDownColor: '#ef4444',
            wickUpColor: '#10b981',
            wickDownColor: '#ef4444'
        });

        // Subchart (Probabilities)
        probChart = LightweightCharts.createChart(probContainer, {
            layout: {
                backgroundColor: '#121824',
                textColor: '#94a3b8',
                fontSize: 10,
                fontFamily: 'Inter'
            },
            grid: {
                vertLines: { color: '#1e293b' },
                horzLines: { color: '#1e293b' }
            },
            rightPriceScale: { borderColor: '#1e293b' },
            timeScale: { borderColor: '#1e293b', timeVisible: true }
        });

        bullProbSeries = probChart.addLineSeries({ color: '#10b981', lineWidth: 2, title: 'Bull Prob' });
        neutralProbSeries = probChart.addLineSeries({ color: '#f59e0b', lineWidth: 1.5, title: 'Neutral Prob' });
        bearProbSeries = probChart.addLineSeries({ color: '#ef4444', lineWidth: 1.5, title: 'Bear Prob' });
    }

    // Render Sector List Sidebar
    function renderSectorList() {
        const summaries = data.sector_summaries || [];
        sectorListContainer.innerHTML = "";

        const filtered = summaries.filter(s => {
            const matchesSearch = s.sector.toLowerCase().includes(searchTerm.toLowerCase());
            if (!matchesSearch) return false;

            if (activeFilter === "ALL") return true;
            if (activeFilter === "LEADERS") return s.days_in_bull_state > 0;
            return s.current_state.toString() === activeFilter;
        });

        visibleSectorsCount.textContent = `${filtered.length} Sectors`;

        filtered.forEach(s => {
            const item = document.createElement("div");
            item.className = `sector-item ${s.sector === selectedSector ? 'active' : ''}`;
            
            const stateLabels = { 0: "State 0: Bull", 1: "State 1: Neutral", 2: "State 2: Bear" };
            const stateClass = `state-${s.current_state}`;

            item.innerHTML = `
                <div class="sec-info">
                    <h3>${s.sector}</h3>
                    <div class="sec-sub">Idx: ${s.current_val.toFixed(2)} | Prob: ${(s.bull_prob * 100).toFixed(0)}%</div>
                </div>
                <div class="sec-state-badge ${stateClass}">${stateLabels[s.current_state]}</div>
            `;

            item.addEventListener("click", () => {
                selectedSector = s.sector;
                renderSectorList();
                updateChart();
            });

            sectorListContainer.appendChild(item);
        });
    }

    // Update Chart View for Selected Sector and activeK
    function updateChart() {
        const secData = data.sector_details[selectedSector];
        if (!secData || !secData[activeK.toString()]) {
            console.error(`Data missing for sector ${selectedSector} k=${activeK}`);
            return;
        }

        const kObj = secData[activeK.toString()];
        const bars = kObj.bars;

        chartSectorTitle.textContent = selectedSector;
        
        // State Badge
        const curState = kObj.current_state;
        const curProbs = kObj.current_probs;
        
        const stateNames = {
            0: "State 0: Bullish / Expansion",
            1: "State 1: Neutral / Consolidation",
            2: "State 2: Bearish / Contraction"
        };

        chartStateBadge.className = `state-indicator-badge state-${curState}`;
        chartStateText.textContent = stateNames[curState];

        bullProbVal.textContent = `${(curProbs[0] * 100).toFixed(1)}%`;
        neutralProbVal.textContent = `${(curProbs[1] * 100).toFixed(1)}%`;
        bearProbVal.textContent = `${(curProbs[2] * 100).toFixed(1)}%`;

        // Candle bars
        const candleData = bars.map(b => ({
            time: b.t || b.time,
            open: b.c || b.close,
            high: (b.c || b.close) * 1.002,
            low: (b.c || b.close) * 0.998,
            close: b.c || b.close
        }));

        candleSeries.setData(candleData);
        priceChart.timeScale().fitContent();

        // Probabilities
        bullProbSeries.setData(bars.map(b => ({ time: b.t || b.time, value: b.bp !== undefined ? b.bp : b.bull_prob })));
        neutralProbSeries.setData(bars.map(b => ({ time: b.t || b.time, value: b.np !== undefined ? b.np : b.neutral_prob })));
        bearProbSeries.setData(bars.map(b => ({ time: b.t || b.time, value: b.rp !== undefined ? b.rp : b.bear_prob })));
        probChart.timeScale().fitContent();
    }

    // Render Leading Sector Rotation Radar
    function renderLeadingRadar() {
        const summaries = [...(data.sector_summaries || [])];
        summaries.sort((a, b) => b.days_in_bull_state - a.days_in_bull_state);
        
        const topLeaders = summaries.filter(s => s.days_in_bull_state > 0).slice(0, 7);
        leadingRadarList.innerHTML = "";

        if (topLeaders.length === 0) {
            leadingRadarList.innerHTML = `<div class="card-desc">No sectors currently in State 0.</div>`;
            return;
        }

        topLeaders.forEach(s => {
            const row = document.createElement("div");
            row.className = "radar-row";
            row.innerHTML = `
                <span class="sec-name">${s.sector}</span>
                <span class="days-count">${s.days_in_bull_state} Bull Days</span>
            `;
            leadingRadarList.appendChild(row);
        });
    }

    // Render Candle Smoothing Benchmark Table
    function renderSmoothingBenchmark() {
        const benchmarks = data.smoothing_benchmark || {};
        smoothingBenchmarkContainer.innerHTML = `
            <div class="bm-row header">
                <span>Window (k)</span>
                <span>Whipsaw Ratio</span>
                <span>Persistence</span>
            </div>
        `;

        Object.keys(benchmarks).forEach(kKey => {
            const b = benchmarks[kKey];
            const row = document.createElement("div");
            row.className = `bm-row ${parseInt(kKey) === activeK ? 'active' : ''}`;
            row.innerHTML = `
                <span>k = ${b.k} candles</span>
                <span>${(b.avg_whipsaw_ratio * 100).toFixed(1)}%</span>
                <span>${b.avg_persistence_days} days</span>
            `;
            smoothingBenchmarkContainer.appendChild(row);
        });
    }

    // Setup Event Listeners
    function setupEvents() {
        // Candle Smoothing k-selector
        document.querySelectorAll("#kSelectorGroup .k-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                document.querySelectorAll("#kSelectorGroup .k-btn").forEach(b => b.classList.remove("active"));
                e.target.classList.add("active");
                activeK = parseInt(e.target.getAttribute("data-k"));
                activeKDisplay.textContent = `k = ${activeK} Candles`;
                updateChart();
                renderSmoothingBenchmark();
            });
        });

        // Filter Tabs
        document.querySelectorAll("#regimeFilterGroup .tab-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                document.querySelectorAll("#regimeFilterGroup .tab-btn").forEach(b => b.classList.remove("active"));
                e.target.classList.add("active");
                activeFilter = e.target.getAttribute("data-filter");
                renderSectorList();
            });
        });

        // Search Input
        document.getElementById("sectorSearch").addEventListener("input", (e) => {
            searchTerm = e.target.value;
            renderSectorList();
        });
    }

    // Initialize
    initCharts();
    renderSectorList();
    updateChart();
    renderLeadingRadar();
    renderSmoothingBenchmark();
    setupEvents();
});
