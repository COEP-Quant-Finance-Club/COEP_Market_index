
document.addEventListener('DOMContentLoaded', () => {
  const data = window.SECTOR_INDEX_DATA || { summary: [], daily: {}, fourhour: {} };
  let currentTimeframe = 'daily';
  let activeSector = 'ELECTRONICS_EMS';

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

  // DOM Elements
  const sectorListContainer = document.getElementById('sector-list-container');
  const sectorSearchInput = document.getElementById('sector-search');
  const sectorCountBadge = document.getElementById('sector-count-badge');
  const activeSectorTitle = document.getElementById('active-sector-name');
  const activeTimeframeBadge = document.getElementById('active-timeframe-badge');
  const metricCurrentVal = document.getElementById('metric-current-val');
  const metricReturnVal = document.getElementById('metric-return-val');
  const metricConstituentsVal = document.getElementById('metric-constituents-val');
  const chartContainer = document.getElementById('tv-chart-container');
  const fileInput = document.getElementById('csv-file-input');

  // Timeframe Buttons
  const tfBtns = document.querySelectorAll('.tf-btn');
  tfBtns.forEach(btn => {
    btn.addEventListener('click', (e) => {
      tfBtns.forEach(b => b.classList.remove('active'));
      e.target.classList.add('active');
      currentTimeframe = e.target.getAttribute('data-tf');
      activeTimeframeBadge.innerText = currentTimeframe === 'daily' ? 'DAILY OHLCV' : '4-HOUR OHLCV';
      renderChart();
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

  // TradingView Style Scale Toolbar Controls
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

  // Helper to extract key values regardless of schema
  function getItemSecName(item) { return item['Sector Name'] || item['sector'] || ''; }
  function getItemCurrentVal(item) { return item['Current Index Value'] || item['current_val'] || 100.0; }
  function getItemRetPct(item) { 
    if (item['Total Sector Return %']) return String(item['Total Sector Return %']);
    const val = item['total_return_pct'];
    if (val === undefined || val === null) return "+0.00%";
    const str = typeof val === 'number' ? `${val >= 0 ? '+' : ''}${val.toFixed(2)}%` : String(val);
    return str;
  }
  function getItemStockCount(item) { return item['Constituents Count'] || item['stock_count'] || 0; }

  // Render Sector List
  function renderSectorList(filterText = '') {
    sectorListContainer.innerHTML = '';
    const summaryList = data.summary || [];
    
    const filtered = summaryList.filter(item => {
      return getItemSecName(item).toLowerCase().includes(filterText);
    });

    sectorCountBadge.innerText = `${filtered.length} Baskets`;

    if (filtered.length > 0 && !filtered.some(i => getItemSecName(i) === activeSector)) {
      activeSector = getItemSecName(filtered[0]);
    }

    filtered.forEach(item => {
      const secName = getItemSecName(item);
      const currentVal = getItemCurrentVal(item);
      const retPct = getItemRetPct(item);
      const stocksCount = getItemStockCount(item);
      const isPos = !retPct.includes('-');

      const itemEl = document.createElement('div');
      itemEl.className = `sector-item ${secName === activeSector ? 'active' : ''}`;
      itemEl.innerHTML = `
        <div>
          <div class="sec-name">${secName}</div>
          <div class="sec-stocks-count">${stocksCount} Stocks</div>
        </div>
        <div class="sec-return-badge ${isPos ? 'positive' : 'negative'}">
          ${retPct}
        </div>
      `;

      itemEl.addEventListener('click', () => {
        document.querySelectorAll('.sector-item').forEach(el => el.classList.remove('active'));
        itemEl.classList.add('active');
        activeSector = secName;
        updateHeaderMetrics(item);
        renderChart();
      });

      sectorListContainer.appendChild(itemEl);
    });

    if (summaryList.length > 0) {
      const activeItem = summaryList.find(i => getItemSecName(i) === activeSector) || summaryList[0];
      updateHeaderMetrics(activeItem);
    }
  }

  function updateHeaderMetrics(item) {
    if (!item) return;
    const secName = getItemSecName(item);
    const currentVal = getItemCurrentVal(item);
    const retPct = getItemRetPct(item);
    const stocksCount = getItemStockCount(item);

    activeSectorTitle.innerText = secName;
    metricCurrentVal.innerText = (typeof currentVal === 'number' ? currentVal : parseFloat(currentVal)).toLocaleString('en-IN', { minimumFractionDigits: 2 });
    metricReturnVal.innerText = retPct;
    metricReturnVal.className = `metric-val ${!retPct.includes('-') ? 'positive' : 'negative'}`;
    metricConstituentsVal.innerText = `${stocksCount} Stocks`;
  }

  // Init Chart with Full TradingView Scale & Zooming Controls
  function initChart() {
    if (chart) chart.remove();

    chart = LightweightCharts.createChart(chartContainer, {
      layout: {
        background: { color: '#0b0f19' },
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
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    sma20Series = chart.addLineSeries({ color: '#f59e0b', lineWidth: 1.5, title: 'SMA 20' });
    sma50Series = chart.addLineSeries({ color: '#38bdf8', lineWidth: 1.5, title: 'SMA 50' });
    ema200Series = chart.addLineSeries({ color: '#ec4899', lineWidth: 1.5, title: 'EMA 200' });
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
    if (!chart) initChart();

    let rawRecords = [];
    if (data[currentTimeframe] && data[currentTimeframe][activeSector]) {
      rawRecords = data[currentTimeframe][activeSector];
    }

    if (!rawRecords || rawRecords.length === 0) return;

    const candleData = [];
    const volData = [];

    // Map time based on timeframe to prevent duplicate timestamps in Lightweight Charts
    const seenTimes = new Set();

    rawRecords.forEach(r => {
      let t;
      if (currentTimeframe === 'fourhour') {
        // Unix timestamp in seconds for 4-Hour intraday candles
        const d = new Date(r.time.replace(' ', 'T'));
        if (isNaN(d.getTime())) return;
        t = Math.floor(d.getTime() / 1000);
      } else {
        // YYYY-MM-DD string for Daily candles
        t = r.time.split(' ')[0];
      }

      if (seenTimes.has(t)) return; // Deduplicate
      seenTimes.add(t);

      const o = parseFloat(r.open !== undefined ? r.open : (r.Open !== undefined ? r.Open : r.close));
      const h = parseFloat(r.high !== undefined ? r.high : (r.High !== undefined ? r.High : r.close));
      const l = parseFloat(r.low !== undefined ? r.low : (r.Low !== undefined ? r.Low : r.close));
      const c = parseFloat(r.close !== undefined ? r.close : (r.Close !== undefined ? r.Close : 0));
      const v = parseFloat(r.volume !== undefined ? r.volume : (r.Volume !== undefined ? r.Volume : 0));

      if (isNaN(c) || c <= 0) return;

      candleData.push({
        time: t,
        open: isNaN(o) ? c : o,
        high: isNaN(h) ? c : h,
        low: isNaN(l) ? c : l,
        close: c,
      });

      volData.push({
        time: t,
        value: isNaN(v) ? 0 : v,
        color: c >= (isNaN(o) ? c : o) ? 'rgba(34, 197, 94, 0.35)' : 'rgba(239, 68, 68, 0.35)'
      });
    });

    candlestickSeries.setData(candleData);
    volumeSeries.setData(volData);

    if (showSma20) sma20Series.setData(calculateSMA(candleData, 20)); else sma20Series.setData([]);
    if (showSma50) sma50Series.setData(calculateSMA(candleData, 50)); else sma50Series.setData([]);
    if (showEma200) ema200Series.setData(calculateEMA(candleData, 200)); else ema200Series.setData([]);

    chart.timeScale().fitContent();
  }

  // Parse custom uploaded CSV
  fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      const text = event.target.result;
      const lines = text.trim().split('\n');
      if (lines.length < 2) return;

      const customRecords = [];
      for (let i = 1; i < lines.length; i++) {
        const parts = lines[i].split(',').map(p => p.trim().replace(/"/g, ''));
        if (parts.length < 5) continue;

        const dateStr = parts[0];
        customRecords.push({
          time: dateStr,
          Open: parseFloat(parts[1]),
          High: parseFloat(parts[2]),
          Low: parseFloat(parts[3]),
          Close: parseFloat(parts[4]),
          Volume: parts[5] ? parseFloat(parts[5]) : 0
        });
      }

      const customName = file.name.replace('.csv', '').toUpperCase();
      data.daily[customName] = customRecords;
      activeSector = customName;

      activeSectorTitle.innerText = customName;
      metricCurrentVal.innerText = customRecords[customRecords.length - 1].Close.toFixed(2);
      metricReturnVal.innerText = 'CUSTOM CSV';
      metricConstituentsVal.innerText = `${customRecords.length} Rows`;

      renderChart();
    };
    reader.readAsText(file);
  });

  // Window resize handler
  window.addEventListener('resize', () => {
    if (chart) chart.applyOptions({ width: chartContainer.clientWidth, height: chartContainer.clientHeight });
  });

  // Initial Load
  const firstSummary = data.summary[0];
  if (firstSummary) {
    activeSector = firstSummary['Sector Name'];
    updateHeaderMetrics(firstSummary);
  }

  renderSectorList();
  initChart();
  renderChart();
});
