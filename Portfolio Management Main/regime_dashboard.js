/**
 * Quant Club - Institutional Sector Index Terminal & HMM Regimes JS
 * Replica of master dashboard.js + HMM Background Canvas Shading & Sensitivity Slider
 */

document.addEventListener('DOMContentLoaded', () => {
  const data = window.REGIME_ANALYSIS_DATA || { sector_summaries: [], sector_details: {} };
  let activeSector = 'ELECTRONICS_EMS';
  let activeK = 5;

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
  const metricCurrentVal = document.getElementById('metric-current-val');
  const metricReturnVal = document.getElementById('metric-return-val');
  const metricStateVal = document.getElementById('metric-state-val');
  const chartContainer = document.getElementById('tv-chart-container');
  const canvas = document.getElementById('hmmBackgroundCanvas');
  const ctx = canvas.getContext('2d');

  const kRangeSlider = document.getElementById('kRangeSlider');
  const kSliderVal = document.getElementById('kSliderVal');

  // Sensitivity Slider Event Listener (IMAGE 2 REPLICA)
  kRangeSlider.addEventListener('input', (e) => {
    activeK = parseInt(e.target.value);
    kSliderVal.innerText = activeK;
    renderChart();
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

  // Render Sector List
  function renderSectorList(filterText = '') {
    sectorListContainer.innerHTML = '';
    const summaryList = data.sector_summaries || [];
    
    const filtered = summaryList.filter(item => {
      return item.sector.toLowerCase().includes(filterText);
    });

    sectorCountBadge.innerText = `${filtered.length} Baskets`;

    if (filtered.length > 0 && !filtered.some(i => i.sector === activeSector)) {
      activeSector = filtered[0].sector;
    }

    filtered.forEach(item => {
      const secName = item.sector;
      const currentVal = item.current_val;
      const retPct = `${((currentVal - 100.0) / 100.0 * 100.0) >= 0 ? '+' : ''}${((currentVal - 100.0) / 100.0 * 100.0).toFixed(2)}%`;
      const isPos = !retPct.includes('-');

      const itemEl = document.createElement('div');
      itemEl.className = `sector-item ${secName === activeSector ? 'active' : ''}`;
      itemEl.innerHTML = `
        <div>
          <div class="sec-name">${secName}</div>
          <div class="sec-stocks-count">Master Index</div>
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
      const activeItem = summaryList.find(i => i.sector === activeSector) || summaryList[0];
      updateHeaderMetrics(activeItem);
    }
  }

  function updateHeaderMetrics(item) {
    if (!item) return;
    const secName = item.sector;
    const currentVal = item.current_val;
    const retPct = `${((currentVal - 100.0) / 100.0 * 100.0) >= 0 ? '+' : ''}${((currentVal - 100.0) / 100.0 * 100.0).toFixed(2)}%`;
    const stateNames = { 0: "State 0: Bullish", 1: "State 1: Neutral", 2: "State 2: Bearish" };

    activeSectorTitle.innerText = secName;
    metricCurrentVal.innerText = currentVal.toLocaleString('en-IN', { minimumFractionDigits: 2 });
    metricReturnVal.innerText = retPct;
    metricReturnVal.className = `metric-val ${!retPct.includes('-') ? 'positive' : 'negative'}`;
    metricStateVal.innerText = stateNames[item.current_state];
  }

  // Init Chart with Transparent Background for Canvas HMM State Shading
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
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    sma20Series = chart.addLineSeries({ color: '#f59e0b', lineWidth: 1.5, title: 'SMA 20' });
    sma50Series = chart.addLineSeries({ color: '#38bdf8', lineWidth: 1.5, title: 'SMA 50' });
    ema200Series = chart.addLineSeries({ color: '#ec4899', lineWidth: 1.5, title: 'EMA 200' });

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

  // Draw HMM Vertical Background State Shading (IMAGE 1 REPLICA)
  function drawHMMBackgroundOverlay() {
    if (!chart || !canvas || !ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const secData = data.sector_details[activeSector];
    if (!secData || !secData[activeK.toString()]) return;

    const bars = secData[activeK.toString()].bars;
    if (!bars || bars.length === 0) return;

    const timeScale = chart.timeScale();
    const stateColors = {
      0: "rgba(34, 197, 94, 0.32)",   /* State 0: Vibrant Bullish Green */
      1: "rgba(245, 158, 11, 0.25)",  /* State 1: Dark Amber Yellow */
      2: "rgba(239, 68, 68, 0.32)"   /* State 2: Vibrant Bearish Red */
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
        x2 = x1 + 10;
      }

      const width = Math.max(1, x2 - x1);
      const stateVal = bar.s !== undefined ? bar.s : bar.state;
      const color = stateColors[stateVal] || stateColors[1];

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
    const secData = data.sector_details[activeSector];
    if (!secData || !secData[activeK.toString()]) return;

    const bars = secData[activeK.toString()].bars || [];
    if (bars.length === 0) return;

    const formattedBars = bars.map((b, idx) => {
      const c = b.c || b.close;
      const prevC = idx > 0 ? (bars[idx-1].c || bars[idx-1].close) : c;
      const o = prevC;
      const h = Math.max(o, c) * 1.001;
      const l = Math.min(o, c) * 0.999;
      return { time: b.t || b.time, open: o, high: h, low: l, close: c };
    });

    const volumeData = bars.map(b => ({
      time: b.t || b.time,
      value: Math.floor(Math.random() * 50000000) + 10000000,
      color: (b.c || b.close) >= (b.o || b.close) ? 'rgba(34, 197, 94, 0.4)' : 'rgba(239, 68, 68, 0.4)'
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
