/*
 * ChartRenderer：对齐真实项目里的通用 ECharts 图表组件
 *   - 统一数据入口：{ data, columns, chartType, xAxisField, yAxisFields }
 *   - 共性（tooltip / legend / grid / 颜色 / 缩放）抽公共配置，差异按 chartType 分发
 *   - 数据/尺寸变化 → setOption 更新；容器尺寸变化 → resize；提供 dispose
 */
const PALETTE = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed",
                 "#0891b2", "#db2777", "#65a30d", "#ea580c", "#4b5563"];

function _colIndex(columns, field) {
  const i = columns.indexOf(field);
  if (i < 0) throw new Error(`字段不存在：${field || "（未选择）"}`);
  return i;
}

const liveCharts = new Map();
function disposeCharts(container) {
  for (const [element, renderer] of liveCharts) {
    if (element === container || container.contains(element)) renderer.dispose();
  }
}
window.addEventListener("pagehide", () => { for (const renderer of [...liveCharts.values()]) renderer.dispose(); });

class ChartRenderer {
  constructor(el, cfg) {
    this.el = el;
    this.cfg = Object.assign({ data: [], columns: [], chartType: "bar",
                               xAxisField: "", yAxisFields: [] }, cfg);
    this.chart = null;
    this._ro = null;
    try { this.render(); liveCharts.set(el, this); }
    catch (e) { this.dispose(); throw e; }
  }

  option() {
    let { data, columns, chartType, xAxisField, yAxisFields, seriesField } = this.cfg;
    if (!data.length) return { title: { text: "暂无数据", left: "center", textStyle: { fontSize: 14 } } };
    if (seriesField) {
      if (["pie", "gauge", "radar"].includes(chartType)) throw new Error("多维分组请使用柱状图、折线图或表格");
      const x = _colIndex(columns, xAxisField), group = _colIndex(columns, seriesField);
      const measure = _colIndex(columns, yAxisFields[0]);
      const groups = [...new Set(data.map(r => String(r[group])))];
      const labels = [...new Set(data.map(r => r[x]))];
      const keyed = new Map(data.map(r => [JSON.stringify([r[x], String(r[group])]), r[measure]]));
      data = labels.map(label => [label, ...groups.map(g => keyed.get(JSON.stringify([label, g])) ?? null)]);
      columns = [xAxisField, ...groups]; yAxisFields = groups;
    }
    const xi = _colIndex(columns, xAxisField);
    const yis = (yAxisFields && yAxisFields.length ? yAxisFields : [columns[columns.length - 1]])
      .map(f => _colIndex(columns, f));
    if (data.some(row => yis.some(yi => row[yi] !== null && !isNum(row[yi])))) throw new Error("指标字段必须是数值");
    const base = {
      color: PALETTE,
      tooltip: { trigger: chartType === "pie" ? "item" : "axis" },
      legend: { show: yis.length > 1 || chartType === "pie", top: 4, textStyle: { fontSize: 11 } },
      grid: { left: 48, right: 24, top: 34, bottom: 40, containLabel: true },
    };

    if (chartType === "pie") {
      return Object.assign(base, {
        series: [{
          type: "pie", radius: ["42%", "68%"], center: ["50%", "56%"],
          data: data.map(r => ({ name: String(r[xi]), value: Number(r[yis[0]]) || 0 })),
          label: { fontSize: 11 },
        }],
      });
    }

    if (chartType === "radar") {
      return Object.assign(base, {
        radar: {
          indicator: data.map(r => ({ name: String(r[xi]) })),
          radius: "65%",
        },
        series: [{
          type: "radar",
          data: yis.map((yi, k) => ({
            name: columns[yi],
            value: data.map(r => Number(r[yi]) || 0),
          })),
        }],
      });
    }

    if (chartType === "gauge") {
      const v = data.length ? Number(data[0][yis[0]]) || 0 : 0;
      return Object.assign(base, {
        series: [{
          type: "gauge", min: 0, max: Math.max(100, Math.ceil(v * 1.2)),
          progress: { show: true },
          detail: { formatter: "{value}", fontSize: 20 },
          data: [{ value: v, name: columns[yis[0]] }],
        }],
      });
    }

    if (chartType === "scatter") {
      return Object.assign(base, {
        xAxis: { type: "category", data: data.map(r => String(r[xi])) },
        yAxis: { type: "value" },
        series: yis.map((yi, k) => ({
          name: columns[yi], type: "scatter",
          data: data.map(r => Number(r[yi]) || 0), symbolSize: 10,
        })),
      });
    }

    // bar / line / area：共用直角坐标系
    return Object.assign(base, {
      xAxis: { type: "category", data: data.map(r => String(r[xi])),
               axisLabel: { fontSize: 11, interval: data.length > 14 ? "auto" : 0,
                            rotate: data.length > 8 ? 30 : 0 } },
      yAxis: { type: "value", axisLabel: { fontSize: 11,
               formatter: v => (Math.abs(v) >= 1e4 ? (v / 1e4).toFixed(1) + "万" : v) } },
      dataZoom: data.length > 20 ? [{ type: "inside" }, { type: "slider", height: 14 }] : [],
      series: yis.map((yi) => ({
        name: columns[yi],
        type: chartType === "bar" ? "bar" : "line",
        smooth: chartType === "line",
        areaStyle: chartType === "area" ? { opacity: .18 } : undefined,
        data: data.map(r => r[yi] === null ? null : Number(r[yi])),
        large: data.length > 500,
      })),
    });
  }

  render() {
    if (this.cfg.chartType === "number") return;
    if (!this.chart) {
      this.chart = echarts.init(this.el);
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(this.el);
    }
    this.chart.setOption(this.option(), true);
  }

  update(cfg) { this.cfg = Object.assign(this.cfg, cfg); this.render(); }
  resize() { if (this.chart) this.chart.resize(); }
  dispose() {
    liveCharts.delete(this.el);
    if (this._ro) this._ro.disconnect();
    if (this.chart) { this.chart.dispose(); this.chart = null; }
  }
}

/* 统一入口：按 suggestion 渲染到容器（含 KPI / 表格两种非 ECharts 形态） */
function renderChartBlock(container, columns, data, suggestion, state) {
  disposeCharts(container);
  container.innerHTML = "";
  const type = (suggestion && suggestion.chartType) || "table";
  const types = ["bar", "line", "area", "pie", "radar", "scatter", "gauge", "table"];
  const bar = document.createElement("div");
  bar.className = "row";
  bar.style.marginBottom = "6px";
  bar.innerHTML = `<span class="muted">推荐：${escapeHtml(type)}`
    + (suggestion && suggestion.reason ? `（${escapeHtml(suggestion.reason)}）` : "")
    + `</span><span class="grow"></span>`;
  container.appendChild(bar);
  types.forEach(t => {
    const b = document.createElement("button");
    b.className = "sm"; b.textContent = t;
    if (t === type) b.classList.add("primary");
    b.onclick = () => {
      container.querySelectorAll("button").forEach(x => x.classList.remove("primary"));
      b.classList.add("primary");
      draw(t);
    };
    bar.appendChild(b);
  });

  const body = document.createElement("div");
  container.appendChild(body);

  function draw(t) {
    disposeCharts(body);
    body.innerHTML = "";
    if (t === "table") { renderTable(body, columns, data, state); return; }
    if (t === "number") { renderKpi(body, columns, data); return; }
    const div = document.createElement("div");
    div.className = "chart-canvas";
    body.appendChild(div);
    try {
      new ChartRenderer(div, {
        data, columns, chartType: t,
        xAxisField: (suggestion && suggestion.xAxisField) || columns[0],
        yAxisFields: (suggestion && suggestion.yAxisFields) || [columns[columns.length - 1]],
        seriesField: suggestion && suggestion.seriesField,
      });
    } catch (e) {
      body.innerHTML = `<div class="alert">图表渲染失败：${escapeHtml(e.message)}</div>`;
    }
  }
  draw(type === "number" ? "number" : type);
}

function renderKpi(container, columns, data, field = null) {
  const index = field ? _colIndex(columns, field) : columns.length - 1;
  const v = data.length ? data[0][index] : "-";
  container.innerHTML = `<div class="kpi"><div class="v">${escapeHtml(formatNumber(v))}</div>`
    + `<div class="k">${escapeHtml(columns[index] || "")}</div></div>`;
}
