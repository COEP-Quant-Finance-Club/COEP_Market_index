/**
 * Quant Club - Institutional Sector Index Terminal & 3-State Macro Regimes JS
 * Double-click Sector to view Constituents + Candle Crosshair 2-Day Return Sync
 */

document.addEventListener('DOMContentLoaded', () => {
  const data = window.REGIME_ANALYSIS_DATA || { sector_summaries: [], sector_details: {} };
  let activeSector = 'ELECTRONICS_EMS';
  let activeK = 9;
  let activeRegimeFilter = 'ALL';
  let activeHoverDate = null;
  let activeHoverPrevDate = null;
  let modalSearchTerm = '';

  let chart = null;
  let candlestickSeries = null;
  let volumeSeries = null;
  let sma20Series = null;
  let sma50Series = null;
  let ema200Series = null;

  let showSma20 = true;
  let showSma50 = true;
  let showEma200 = true;
  let isLogScale = false;

  const computedStateCache = {};

  // DOM Elements
  const sectorListContainer = document.getElementById('sector-list-container');
  const sectorSearchInput = document.getElementById('sector-search');
  const sectorCountBadge = document.getElementById('sector-count-badge');
  const activeSectorTitle = document.getElementById('active-sector-name');
  const metricCurrentVal = document.getElementById('metric-current-val');
  const metricReturnVal = document.getElementById('metric-return-val');
  const metricStateVal = document.getElementById('metric-state-val');
  const chartContainer = document.getElementById('tv-chart-container');
  const canvas = document.getElementById('hmmBackgroundCanvas');
  const ctx = canvas.getContext('2d');

  const kRangeSlider = document.getElementById('kRangeSlider');
  const kSliderVal = document.getElementById('kSliderVal');

  const rfBtnAll = document.getElementById('rfBtnAll');
  const rfBtnBull = document.getElementById('rfBtnBull');
  const rfBtnNeutral = document.getElementById('rfBtnNeutral');
  const rfBtnBear = document.getElementById('rfBtnBear');

  // Modal Elements
  const constituentsModalOverlay = document.getElementById('constituentsModalOverlay');
  const btnOpenConstituentsModal = document.getElementById('btnOpenConstituentsModal');
  const btnCloseConstituentsModal = document.getElementById('btnCloseConstituentsModal');
  const modalSectorTitle = document.getElementById('modalSectorTitle');
  const modalDateInfo = document.getElementById('modalDateInfo');
  const modalStockSearch = document.getElementById('modalStockSearch');
  const modalStockCountBadge = document.getElementById('modalStockCountBadge');
  const modalStocksTbody = document.getElementById('modalStocksTbody');

  // Modal Controls
  btnOpenConstituentsModal.addEventListener('click', openConstituentsModal);
  btnCloseConstituentsModal.addEventListener('click', closeConstituentsModal);

  constituentsModalOverlay.addEventListener('click', (e) => {
    if (e.target === constituentsModalOverlay) closeConstituentsModal();
  });

  modalStockSearch.addEventListener('input', (e) => {
    modalSearchTerm = e.target.value.trim().toLowerCase();
    renderConstituentsTable();
  });

  function openConstituentsModal() {
    modalSectorTitle.innerText = `${activeSector} STOCKS`;
    constituentsModalOverlay.classList.add('active');
    renderConstituentsTable();
  }

  function closeConstituentsModal() {
    constituentsModalOverlay.classList.remove('active');
  }

  // Dynamic Fast Rolling Median Filtering (Window k = 1 to 50)
  function computeSmoothedRegimes(sectorName, kWindow) {
    const cacheKey = `${sectorName}_${kWindow}`;
    if (computedStateCache[cacheKey]) {
      return computedStateCache[cacheKey];
    }

    const secDetail = data.sector_details[sectorName];
    if (!secDetail || !secDetail.bars || secDetail.bars.length === 0) return [];

    const bars = secDetail.bars;
    const rawMacro = bars.map(b => b.m !== undefined ? b.m : 1);
    const n = rawMacro.length;
    const smoothed = new Array(n);

    if (kWindow <= 1) {
      for (let i = 0; i < n; i++) smoothed[i] = rawMacro[i];
    } else {
      const half = Math.floor(kWindow / 2);
      for (let i = 0; i < n; i++) {
        const start = Math.max(0, i - half);
        const end = Math.min(n - 1, i + half);
        const windowVals = [];
        for (let j = start; j <= end; j++) {
          windowVals.push(rawMacro[j]);
        }
        windowVals.sort((a, b) => a - b);
        const mid = Math.floor(windowVals.length / 2);
        smoothed[i] = windowVals[mid];
      }
    }

    computedStateCache[cacheKey] = smoothed;
    return smoothed;
  }

  function getSectorCurrentState(sectorName, kWindow) {
    const smoothed = computeSmoothedRegimes(sectorName, kWindow);
    return smoothed.length > 0 ? smoothed[smoothed.length - 1] : 1;
  }

  // Sensitivity Slider Event Listener (k = 1 to 50)
  kRangeSlider.addEventListener('input', (e) => {
    activeK = parseInt(e.target.value);
    kSliderVal.innerText = activeK;
    renderSectorList(sectorSearchInput.value.trim().toLowerCase());
    renderChart();
  });

  // 3 REGIME FILTER BUTTONS EVENT LISTENERS
  const rfBtns = document.querySelectorAll('.rf-btn');
  rfBtns.forEach(btn => {
    btn.addEventListener('click', (e) => {
      rfBtns.forEach(b => b.classList.remove('active'));
      e.target.classList.add('active');
      activeRegimeFilter = e.target.getAttribute('data-regime');
      renderSectorList(sectorSearchInput.value.trim().toLowerCase());
    });
  });

  // Indicator Toggle buttons
  document.getElementById('btn-toggle-sma20').addEventListener('click', (e) => {
    showSma20 = !showSma20;
    e.target.classList.toggle('active', showSma20);
    renderChart();
  });

  document.getElementById('btn-toggle-sma50').addEventListener('click', (e) => {
    showSma50 = !showSma50;
    e.target.classList.toggle('active', showSma50);
    renderChart();
  });

  document.getElementById('btn-toggle-ema200').addEventListener('click', (e) => {
    showEma200 = !showEma200;
    e.target.classList.toggle('active', showEma200);
    renderChart();
  });

  // Scale Toolbar Controls
  document.getElementById('btn-zoom-in').addEventListener('click', () => {
    if (chart) {
      const ts = chart.timeScale();
      const logicalRange = ts.getVisibleLogicalRange();
      if (logicalRange) {
        const span = logicalRange.to - logicalRange.from;
        ts.setVisibleLogicalRange({ from: logicalRange.from + span * 0.15, to: logicalRange.to - span * 0.15 });
      }
    }
  });

  document.getElementById('btn-zoom-out').addEventListener('click', () => {
    if (chart) {
      const ts = chart.timeScale();
      const logicalRange = ts.getVisibleLogicalRange();
      if (logicalRange) {
        const span = logicalRange.to - logicalRange.from;
        ts.setVisibleLogicalRange({ from: logicalRange.from - span * 0.2, to: logicalRange.to + span * 0.2 });
      }
    }
  });

  document.getElementById('btn-scale-auto').addEventListener('click', () => {
    if (chart) {
      chart.priceScale('right').applyOptions({ autoScale: true });
    }
  });

  document.getElementById('btn-scale-log').addEventListener('click', (e) => {
    if (chart) {
      isLogScale = !isLogScale;
      e.target.classList.toggle('active', isLogScale);
      chart.priceScale('right').applyOptions({
        mode: isLogScale ? LightweightCharts.PriceScaleMode.Logarithmic : LightweightCharts.PriceScaleMode.Normal
      });
    }
  });

  document.getElementById('btn-fit-content').addEventListener('click', () => {
    if (chart) {
      chart.timeScale().fitContent();
      chart.priceScale('right').applyOptions({ autoScale: true });
    }
  });

  // Search Filter
  sectorSearchInput.addEventListener('input', (e) => {
    renderSectorList(e.target.value.trim().toLowerCase());
  });

  // Render Sidebar Sector List & Setup Double-Click Handlers
  function renderSectorList(filterText = '') {
    sectorListContainer.innerHTML = '';
    const summaryList = data.sector_summaries || [];

    let bullCount = 0;
    let neutralCount = 0;
    let bearCount = 0;

    summaryList.forEach(item => {
      const state = getSectorCurrentState(item.sector, activeK);
      if (state === 2) bullCount++;
      else if (state === 1) neutralCount++;
      else if (state === 0) bearCount++;
    });

    rfBtnAll.innerText = `ALL (${summaryList.length})`;
    rfBtnBull.innerText = `🟢 Bullish (${bullCount})`;
    rfBtnNeutral.innerText = `🟡 Neutral (${neutralCount})`;
    rfBtnBear.innerText = `🔴 Bearish (${bearCount})`;

    const filtered = summaryList.filter(item => {
      const nameMatch = item.sector.toLowerCase().includes(filterText);
      if (!nameMatch) return false;

      const currentState = getSectorCurrentState(item.sector, activeK);
      if (activeRegimeFilter === 'ALL') return true;
      return currentState.toString() === activeRegimeFilter;
    });

    if (activeRegimeFilter === 'ALL') {
      sectorCountBadge.innerText = `${filtered.length} Baskets`;
    } else if (activeRegimeFilter === '2') {
      sectorCountBadge.innerText = `${filtered.length} Bullish Baskets`;
    } else if (activeRegimeFilter === '1') {
      sectorCountBadge.innerText = `${filtered.length} Neutral Baskets`;
    } else if (activeRegimeFilter === '0') {
      sectorCountBadge.innerText = `${filtered.length} Bearish Baskets`;
    }

    if (filtered.length > 0 && !filtered.some(i => i.sector === activeSector)) {
      activeSector = filtered[0].sector;
    }

    filtered.forEach(item => {
      const secName = item.sector;
      const currentVal = item.current_val;
      const retPct = item.total_return_pct || `${((currentVal - 100.0) / 100.0 * 100.0) >= 0 ? '+' : ''}${((currentVal - 100.0) / 100.0 * 100.0).toFixed(2)}%`;
      const isPos = !retPct.includes('-');

      const itemEl = document.createElement('div');
      itemEl.className = `sector-item ${secName === activeSector ? 'active' : ''}`;
      itemEl.innerHTML = `
        <div>
          <div class="sec-name">${secName}</div>
          <div class="sec-stocks-count">${item.stock_count || ''} Stocks (Dbl-Click)</div>
        </div>
        <div class="sec-return-badge ${isPos ? 'positive' : 'negative'}">
          ${retPct}
        </div>
      `;

      // Single Click: Change Active Sector
      itemEl.addEventListener('click', () => {
        document.querySelectorAll('.sector-item').forEach(el => el.classList.remove('active'));
        itemEl.classList.add('active');
        activeSector = secName;
        updateHeaderMetrics(item);
        renderChart();
      });

      // DOUBLE-CLICK: Open Sector Stocks Modal
      itemEl.addEventListener('dblclick', () => {
        activeSector = secName;
        updateHeaderMetrics(item);
        renderChart();
        openConstituentsModal();
      });

      sectorListContainer.appendChild(itemEl);
    });

    if (summaryList.length > 0) {
      const activeItem = summaryList.find(i => i.sector === activeSector) || summaryList[0];
      updateHeaderMetrics(activeItem);
    }
  }

  function updateHeaderMetrics(item) {
    if (!item) return;
    const secName = item.sector;
    const currentVal = item.current_val;
    const retPct = item.total_return_pct || `${((currentVal - 100.0) / 100.0 * 100.0) >= 0 ? '+' : ''}${((currentVal - 100.0) / 100.0 * 100.0).toFixed(2)}%`;
    const curState = getSectorCurrentState(secName, activeK);
    const stateNames = { 0: "🔴 Bearish (State 0)", 1: "🟡 Neutral (State 1)", 2: "🟢 Bullish (State 2)" };

    activeSectorTitle.innerText = secName;
    metricCurrentVal.innerText = currentVal.toLocaleString('en-IN', { minimumFractionDigits: 2 });
    metricReturnVal.innerText = retPct;
    metricReturnVal.className = `metric-val ${!retPct.includes('-') ? 'positive' : 'negative'}`;
    metricStateVal.innerText = stateNames[curState] || "🟢 Bullish (State 2)";
  }

  // Render Modal Sector Constituent Stocks & Calculate 2-Day Return Sync
  function renderConstituentsTable() {
    const secDetail = data.sector_details[activeSector];
    if (!secDetail) return;

    const constituents = secDetail.constituents || [];
    const bars = secDetail.bars || [];

    // Determine target Date (T) and Previous Date (T-1)
    let curDate = activeHoverDate;
    let prevDate = activeHoverPrevDate;

    if (!curDate && bars.length > 0) {
      curDate = bars[bars.length - 1].t;
      prevDate = bars.length > 1 ? bars[bars.length - 2].t : null;
    }

    modalSectorTitle.innerText = `${activeSector} STOCKS`;
    modalDateInfo.innerText = `Selected Candle Date: ${curDate || 'N/A'} (T) vs ${prevDate || 'N/A'} (T-1) | 2-Day Change Sync`;

    // Filter by stock search term
    const filteredStocks = constituents.filter(stk => {
      const sym = (stk.symbol || '').toLowerCase();
      const name = (stk.name || '').toLowerCase();
      return sym.includes(modalSearchTerm) || name.includes(modalSearchTerm);
    });

    modalStockCountBadge.innerText = `${filteredStocks.length} Stocks`;
    modalStocksTbody.innerHTML = '';

    // Calculate 2-day return for each stock
    const processedRows = filteredStocks.map(stk => {
      const pDict = stk.prices || {};
      const priceT = pDict[curDate];
      const priceT1 = pDict[prevDate];

      let chgPct = null;
      if (priceT !== undefined && priceT1 !== undefined && priceT1 > 0) {
        chgPct = ((priceT - priceT1) / priceT1) * 100.0;
      }

      return {
        symbol: stk.symbol,
        name: stk.name,
        priceT: priceT !== undefined ? priceT : 'N/A',
        priceT1: priceT1 !== undefined ? priceT1 : 'N/A',
        chgPct: chgPct
      };
    });

    // Sort by 2-day return descending
    processedRows.sort((a, b) => {
      if (a.chgPct === null) return 1;
      if (b.chgPct === null) return -1;
      return b.chgPct - a.chgPct;
    });

    processedRows.forEach(r => {
      const tr = document.createElement('tr');

      let returnPill = '<span class="return-pill zero">N/A</span>';
      if (r.chgPct !== null) {
        const valStr = `${r.chgPct >= 0 ? '+' : ''}${r.chgPct.toFixed(2)}%`;
        const cls = r.chgPct > 0 ? 'pos' : (r.chgPct < 0 ? 'neg' : 'zero');
        returnPill = `<span class="return-pill ${cls}">${valStr}</span>`;
      }

      tr.innerHTML = `
        <td class="sym-badge">${r.symbol}</td>
        <td>${r.name}</td>
        <td class="text-right">${typeof r.priceT1 === 'number' ? '₹' + r.priceT1.toLocaleString('en-IN') : 'N/A'}</td>
        <td class="text-right">${typeof r.priceT === 'number' ? '₹' + r.priceT.toLocaleString('en-IN') : 'N/A'}</td>
        <td class="text-right">${returnPill}</td>
      `;

      modalStocksTbody.appendChild(tr);
    });
  }

  // Init Chart with Crosshair Movement Event Syncing Candle Dates
  function initChart() {
    if (chart) chart.remove();

    chart = LightweightCharts.createChart(chartContainer, {
      layout: {
        background: { color: 'transparent' },
        textColor: '#94a3b8',
        fontSize: 12,
        fontFamily: 'Inter, sans-serif'
      },
      grid: {
        vertLines: { color: 'rgba(36, 49, 76, 0.4)', style: LightweightCharts.LineStyle.Dotted },
        horzLines: { color: 'rgba(36, 49, 76, 0.4)', style: LightweightCharts.LineStyle.Dotted },
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: {
          color: '#38bdf8',
          width: 1,
          style: LightweightCharts.LineStyle.Dashed,
          labelBackgroundColor: '#1e293b'
        },
        horzLine: {
          color: '#38bdf8',
          width: 1,
          style: LightweightCharts.LineStyle.Dashed,
          labelBackgroundColor: '#1e293b'
        }
      },
      rightPriceScale: {
        visible: true,
        borderVisible: true,
        borderColor: '#24314c',
        autoScale: true,
        mode: LightweightCharts.PriceScaleMode.Normal,
        scaleMargins: { top: 0.1, bottom: 0.25 },
        entireTextOnly: true
      },
      timeScale: {
        visible: true,
        borderVisible: true,
        borderColor: '#24314c',
        timeVisible: true,
        secondsVisible: false,
        ticksVisible: true,
        rightOffset: 12,
        barSpacing: 6,
        minBarSpacing: 0.5
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: true
      },
      handleScale: {
        axisPressedMouseMove: { time: true, price: true },
        mouseWheel: true,
        pinch: true
      }
    });

    candlestickSeries = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });

    volumeSeries = chart.addHistogramSeries({
      color: '#38bdf8',
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    sma20Series = chart.addLineSeries({ color: '#f59e0b', lineWidth: 1.5, title: 'SMA 20' });
    sma50Series = chart.addLineSeries({ color: '#38bdf8', lineWidth: 1.5, title: 'SMA 50' });
    ema200Series = chart.addLineSeries({ color: '#ec4899', lineWidth: 1.5, title: 'EMA 200' });

    // CROSSHAIR MOVE LISTENER: SYNC CURSOR CANDLE SELECTION TO 2-DAY RETURN CALCULATION
    chart.subscribeCrosshairMove((param) => {
      if (param.time) {
        const secDetail = data.sector_details[activeSector];
        if (secDetail && secDetail.bars) {
          const bars = secDetail.bars;
          const idx = bars.findIndex(b => b.t === param.time);
          if (idx !== -1) {
            activeHoverDate = bars[idx].t;
            activeHoverPrevDate = idx > 0 ? bars[idx - 1].t : null;

            // Re-render modal table if open
            if (constituentsModalOverlay.classList.contains('active')) {
              renderConstituentsTable();
            }
          }
        }
      }
    });

    chart.timeScale().subscribeVisibleTimeRangeChange(() => {
      requestAnimationFrame(drawHMMBackgroundOverlay);
    });

    window.addEventListener('resize', () => {
      resizeCanvas();
      requestAnimationFrame(drawHMMBackgroundOverlay);
    });

    resizeCanvas();
  }

  function resizeCanvas() {
    const wrapper = document.getElementById('chart-wrapper');
    if (wrapper) {
      canvas.width = wrapper.clientWidth;
      canvas.height = wrapper.clientHeight;
    }
  }

  // Draw 3-State HMM Vertical Background Shading Boxes
  function drawHMMBackgroundOverlay() {
    if (!chart || !canvas || !ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const secDetail = data.sector_details[activeSector];
    if (!secDetail || !secDetail.bars || secDetail.bars.length === 0) return;

    const bars = secDetail.bars;
    const smoothedStates = computeSmoothedRegimes(activeSector, activeK);
    const timeScale = chart.timeScale();
    
    // 🔴 State 0: Bearish (Red), 🟡 State 1: Neutral (Yellow), 🟢 State 2: Bullish (Green)
    const stateColors = {
      0: "rgba(255, 23, 68, 0.28)",
      1: "rgba(255, 235, 59, 0.18)",
      2: "rgba(0, 230, 118, 0.28)"
    };

    for (let i = 0; i < bars.length; i++) {
      const bar = bars[i];
      const nextBar = bars[i + 1];

      const x1 = timeScale.timeToCoordinate(bar.t);
      if (x1 === null || x1 < -50 || x1 > canvas.width + 50) continue;

      let x2;
      if (nextBar) {
        x2 = timeScale.timeToCoordinate(nextBar.t);
      }
      if (x2 === null || x2 === undefined) {
        x2 = x1 + 10;
      }

      const width = Math.max(1, x2 - x1);
      const stateVal = smoothedStates[i] !== undefined ? smoothedStates[i] : 1;
      const color = stateColors[stateVal] !== undefined ? stateColors[stateVal] : stateColors[1];

      ctx.fillStyle = color;
      ctx.fillRect(x1, 0, width, canvas.height);
    }
  }

  function calculateSMA(dataArray, period) {
    const sma = [];
    for (let i = 0; i < dataArray.length; i++) {
      if (i < period - 1) continue;
      let sum = 0;
      for (let j = 0; j < period; j++) sum += dataArray[i - j].close;
      sma.push({ time: dataArray[i].time, value: sum / period });
    }
    return sma;
  }

  function calculateEMA(dataArray, period) {
    const ema = [];
    const k = 2 / (period + 1);
    let prevEma = null;

    for (let i = 0; i < dataArray.length; i++) {
      const close = dataArray[i].close;
      if (i < period - 1) {
        continue;
      } else if (i === period - 1) {
        let sum = 0;
        for (let j = 0; j < period; j++) sum += dataArray[j].close;
        prevEma = sum / period;
        ema.push({ time: dataArray[i].time, value: prevEma });
      } else {
        prevEma = close * k + prevEma * (1 - k);
        ema.push({ time: dataArray[i].time, value: prevEma });
      }
    }
    return ema;
  }

  function renderChart() {
    const secDetail = data.sector_details[activeSector];
    if (!secDetail || !secDetail.bars || secDetail.bars.length === 0) return;

    const bars = secDetail.bars;

    const formattedBars = bars.map(b => ({
      time: b.t,
      open: b.o,
      high: b.h,
      low: b.l,
      close: b.c
    }));

    const volumeData = bars.map(b => ({
      time: b.t,
      value: b.v,
      color: b.c >= b.o ? 'rgba(34, 197, 94, 0.4)' : 'rgba(239, 68, 68, 0.4)'
    }));

    candlestickSeries.setData(formattedBars);
    volumeSeries.setData(volumeData);

    if (showSma20) {
      sma20Series.setData(calculateSMA(formattedBars, 20));
    } else {
      sma20Series.setData([]);
    }

    if (showSma50) {
      sma50Series.setData(calculateSMA(formattedBars, 50));
    } else {
      sma50Series.setData([]);
    }

    if (showEma200) {
      ema200Series.setData(calculateEMA(formattedBars, 200));
    } else {
      ema200Series.setData([]);
    }

    chart.timeScale().fitContent();
    chart.priceScale('right').applyOptions({ autoScale: true });

    setTimeout(drawHMMBackgroundOverlay, 50);
  }

  // Init Application
  initChart();
  renderSectorList();
  renderChart();
});
