/**
 * Quant Club - Institutional 3-State HMM Sector Terminal JS
 * Replicates exact minimal aesthetic of quant_club_sector_terminal.html
 * Canvas Overlay Vertical HMM State Shading (Image 1) & Sensitivity Slider (Image 2)
 */

document.addEventListener("DOMContentLoaded", () => {
    const data = window.REGIME_ANALYSIS_DATA;
    if (!data) {
        console.error("REGIME_ANALYSIS_DATA not loaded!");
        return;
    }

    let activeK = 5;
    let selectedSector = "ELECTRONICS_EMS";
    let searchTerm = "";

    // Chart & Series Handles
    let chart = null;
    let candleSeries = null;
    let volumeSeries = null;

    // DOM Elements
    const kRangeSlider = document.getElementById("kRangeSlider");
    const kSliderVal = document.getElementById("kSliderVal");
    const sectorSearch = document.getElementById("sectorSearch");
    const sectorListContainer = document.getElementById("sectorListContainer");
    const canvas = document.getElementById("hmmBackgroundCanvas");
    const ctx = canvas.getContext("2d");

    // Header Stats
    const currentSectorTitle = document.getElementById("currentSectorTitle");
    const statCurVal = document.getElementById("statCurVal");
    const statTotReturn = document.getElementById("statTotReturn");
    const statStockCount = document.getElementById("statStockCount");
    const statActiveState = document.getElementById("statActiveState");

    // Initialize TradingView Lightweight Chart
    function initChart() {
        const tvContainer = document.getElementById("tvChartContainer");
        tvContainer.innerHTML = "";

        chart = LightweightCharts.createChart(tvContainer, {
            layout: {
                backgroundColor: 'transparent', // Transparent background to show canvas shading underneath
                textColor: '#94a3b8',
                fontSize: 11,
                fontFamily: 'Inter'
            },
            grid: {
                vertLines: { color: 'rgba(36, 49, 76, 0.5)' },
                horzLines: { color: 'rgba(36, 49, 76, 0.5)' }
            },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            rightPriceScale: { borderColor: '#24314c' },
            timeScale: { borderColor: '#24314c', timeVisible: true }
        });

        candleSeries = chart.addCandlestickSeries({
            upColor: '#22c55e',
            downColor: '#ef4444',
            borderUpColor: '#22c55e',
            borderDownColor: '#ef4444',
            wickUpColor: '#22c55e',
            wickDownColor: '#ef4444'
        });

        volumeSeries = chart.addHistogramSeries({
            color: '#26a69a',
            priceFormat: { type: 'volume' },
            priceScaleId: '',
            scaleMargins: { top: 0.8, bottom: 0 }
        });

        // Resize Canvas and Redraw Shading on TimeScale changes
        chart.timeScale().subscribeVisibleTimeRangeChange(() => {
            requestAnimationFrame(drawHMMBackgroundOverlay);
        });

        window.addEventListener("resize", () => {
            resizeCanvas();
            requestAnimationFrame(drawHMMBackgroundOverlay);
        });

        resizeCanvas();
    }

    function resizeCanvas() {
        const viewport = document.getElementById("chartViewport");
        if (viewport) {
            canvas.width = viewport.clientWidth;
            canvas.height = viewport.clientHeight;
        }
    }

    // Render Canvas HMM State Vertical Background Color Bands (IMAGE 1 REPLICA)
    function drawHMMBackgroundOverlay() {
        if (!chart || !canvas || !ctx) return;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        const secData = data.sector_details[selectedSector];
        if (!secData || !secData[activeK.toString()]) return;

        const bars = secData[activeK.toString()].bars;
        if (!bars || bars.length === 0) return;

        const timeScale = chart.timeScale();
        const stateColors = {
            0: "rgba(34, 197, 94, 0.22)",   /* State 0: Bullish Green */
            1: "rgba(245, 158, 11, 0.18)",  /* State 1: Neutral Yellow */
            2: "rgba(239, 68, 68, 0.22)"   /* State 2: Bearish Red */
        };

        for (let i = 0; i < bars.length; i++) {
            const bar = bars[i];
            const nextBar = bars[i + 1];

            const x1 = timeScale.timeToCoordinate(bar.t || bar.time);
            if (x1 === null || x1 < -50 || x1 > canvas.width + 50) continue;

            let x2;
            if (nextBar) {
                x2 = timeScale.timeToCoordinate(nextBar.t || nextBar.time);
            }
            if (x2 === null || x2 === undefined) {
                x2 = x1 + 8; // Default width for last bar
            }

            const width = Math.max(1, x2 - x1);
            const color = stateColors[bar.s !== undefined ? bar.s : bar.state] || stateColors[1];

            ctx.fillStyle = color;
            ctx.fillRect(x1, 0, width, canvas.height);
        }
    }

    // Render Sidebar Sector List (IMAGE 3 REPLICA)
    function renderSidebar() {
        const summaries = data.sector_summaries || [];
        sectorListContainer.innerHTML = "";

        const filtered = summaries.filter(s => 
            s.sector.toLowerCase().includes(searchTerm.toLowerCase())
        );

        filtered.forEach(s => {
            const item = document.createElement("div");
            item.className = `sector-item ${s.sector === selectedSector ? 'active' : ''}`;
            
            const stateLabels = { 0: "State 0: Bull", 1: "State 1: Neutral", 2: "State 2: Bear" };
            const stateClass = `state-${s.current_state}`;

            item.innerHTML = `
                <div class="sec-info">
                    <span class="sec-name">${s.sector}</span>
                    <span class="sec-count">Master Index</span>
                </div>
                <span class="sec-return-badge ${stateClass}">${stateLabels[s.current_state]}</span>
            `;

            item.addEventListener("click", () => {
                selectedSector = s.sector;
                renderSidebar();
                updateChart();
            });

            sectorListContainer.appendChild(item);
        });
    }

    // Update Chart Data & Stats for Selected Sector
    function updateChart() {
        const secData = data.sector_details[selectedSector];
        if (!secData || !secData[activeK.toString()]) return;

        const kObj = secData[activeK.toString()];
        const bars = kObj.bars;

        currentSectorTitle.textContent = selectedSector;

        const latestBar = bars[bars.length - 1];
        const curVal = latestBar.c || latestBar.close;
        const totReturn = ((curVal - 100.0) / 100.0) * 100.0;
        const stateNames = { 0: "State 0: Bullish", 1: "State 1: Neutral", 2: "State 2: Bearish" };

        statCurVal.textContent = curVal.toFixed(2);
        statTotReturn.textContent = `${totReturn >= 0 ? '+' : ''}${totReturn.toFixed(2)}%`;
        statTotReturn.className = `stat-val ${totReturn >= 0 ? 'green' : 'red'}`;
        statActiveState.textContent = stateNames[kObj.current_state];

        // Format Candles
        const candleData = bars.map((b, idx) => {
            const c = b.c || b.close;
            const prevC = idx > 0 ? (bars[idx-1].c || bars[idx-1].close) : c;
            const o = prevC;
            const h = Math.max(o, c) * 1.001;
            const l = Math.min(o, c) * 0.999;
            return { time: b.t || b.time, open: o, high: h, low: l, close: c };
        });

        // Synthetic volume for visual display
        const volumeData = bars.map(b => ({
            time: b.t || b.time,
            value: Math.floor(Math.random() * 50000000) + 10000000,
            color: (b.c || b.close) >= (b.o || b.close) ? 'rgba(34, 197, 94, 0.4)' : 'rgba(239, 68, 68, 0.4)'
        }));

        candleSeries.setData(candleData);
        volumeSeries.setData(volumeData);
        chart.timeScale().fitContent();

        // Draw HMM Background Overlay
        setTimeout(drawHMMBackgroundOverlay, 50);
    }

    // Setup Slider & Search Events
    function setupEvents() {
        // Sensitivity Slider Change (IMAGE 2 REPLICA)
        kRangeSlider.addEventListener("input", (e) => {
            activeK = parseInt(e.target.value);
            kSliderVal.textContent = activeK;
            updateChart();
        });

        // Search Input
        sectorSearch.addEventListener("input", (e) => {
            searchTerm = e.target.value;
            renderSidebar();
        });
    }

    // Initialize Terminal
    initChart();
    renderSidebar();
    updateChart();
    setupEvents();
});
