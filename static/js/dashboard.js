/* 低代码可视化大屏：12 列栅格拖拽缩放 + 卡片配置 + JSON 持久化 + 独立刷新与错误隔离 */

renderNav("dashboard");

const COLS = 12, ROW_H = 58, GAP = 10;
const $ = (id) => document.getElementById(id);

const state = {
  dashId: null,
  name: "",
  description: "",
  refreshMode: "interval",
  cards: [],
  editing: false,
  charts: {},        // cardId -> ChartRenderer
  dataSources: [],
  refreshTimer: null,
};

const cardRequests = new Map();
let loadGeneration = 0;
function normalizeLayout() {
  const placed = [];
  for (const card of state.cards) {
    card.w = Math.max(1, Math.min(12, Math.round(card.w) || 4));
    card.h = Math.max(1, Math.min(30, Math.round(card.h) || 5));
    card.x = Math.max(0, Math.min(12 - card.w, Math.round(card.x) || 0));
    card.y = Math.max(0, Math.round(card.y) || 0);
    let overlap;
    while ((overlap = placed.find(c => card.x < c.x+c.w && c.x < card.x+card.w && card.y < c.y+c.h && c.y < card.y+card.h))) {
      card.y = overlap.y + overlap.h;
    }
    placed.push(card);
  }
}
function applyRefreshMode(mode) {
  clearInterval(state.refreshTimer); state.refreshTimer = null;
  state.refreshMode = mode === "interval" ? "interval" : "off";
  $("refresh-mode").value = state.refreshMode;
  if (state.refreshMode === "interval") state.refreshTimer = setInterval(refreshAll, 60000);
}

/* ---------------- 布局计算 ---------------- */
function metrics() {
  const w = $("stack").clientWidth;
  const colW = (w - GAP * (COLS - 1)) / COLS;
  return { colW, rowH: ROW_H, gap: GAP };
}

function place(el, card) {
  if (!el) return;
  const { colW, rowH, gap } = metrics();
  el.style.left = (card.x * (colW + gap)) + "px";
  el.style.top = (card.y * (rowH + gap)) + "px";
  el.style.width = (card.w * colW + (card.w - 1) * gap) + "px";
  el.style.height = (card.h * rowH + (card.h - 1) * gap) + "px";
}

function render() {
  normalizeLayout();
  for (const controller of cardRequests.values()) controller.abort();
  cardRequests.clear();
  const stack = $("stack");
  disposeCharts(stack);
  stack.innerHTML = "";
  Object.values(state.charts).forEach(c => c.dispose && c.dispose());
  state.charts = {};

  const { rowH, gap } = metrics();
  const maxY = state.cards.reduce((m, c) => Math.max(m, c.y + c.h), 4);
  stack.style.minHeight = (maxY * (rowH + gap)) + "px";

  state.cards.forEach(card => {
    const item = document.createElement("div");
    item.className = "grid-item" + (state.editing ? " editing" : "");
    item.dataset.id = card.id;
    item.innerHTML = `
      <div class="gi-head">
        <span>${escapeHtml(card.title)}</span>
        <span class="row" style="gap:4px">
          ${state.editing ? `<button class="sm" data-act="edit">编辑</button>
                             <button class="sm danger" data-act="del">删除</button>` : ""}
          <button class="sm" data-act="refresh">刷新</button>
        </span>
      </div>
      <div class="gi-body" data-body></div>
      ${state.editing ? `<div class="gi-resize"></div>` : ""}`;
    place(item, card);
    stack.appendChild(item);

    item.querySelector('[data-act="refresh"]').onclick = () => refreshCard(card.id);
    const eb = item.querySelector('[data-act="edit"]');
    if (eb) eb.onclick = () => openConfig(card);
    const db = item.querySelector('[data-act="del"]');
    if (db) db.onclick = () => {
      state.cards = state.cards.filter(c => c.id !== card.id);
      render();
    };

    if (state.editing) {
      bindDrag(item, card);
      bindResize(item, card);
    }
    refreshCard(card.id);
  });
}

/* ---------------- 拖拽 / 缩放（原生实现，替代 vue-grid-layout） ---------------- */
function bindDrag(item, card) {
  const head = item.querySelector(".gi-head");
  head.addEventListener("mousedown", (e) => {
    if (e.target.tagName === "BUTTON") return;      // 卡片内部按钮不触发拖拽
    e.preventDefault();
    const { colW, rowH, gap } = metrics();
    const rect = item.getBoundingClientRect();
    const ox = e.clientX - rect.left, oy = e.clientY - rect.top;
    function move(ev) {
      const stackRect = $("stack").getBoundingClientRect();
      let nx = Math.round((ev.clientX - stackRect.left - ox) / (colW + gap));
      let ny = Math.round((ev.clientY - stackRect.top - oy) / (rowH + gap));
      nx = Math.max(0, Math.min(COLS - card.w, nx));
      ny = Math.max(0, ny);
      card.x = nx; card.y = ny;
      place(item, card);
    }
    function up() {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      render();
    }
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
}

function bindResize(item, card) {
  const handle = item.querySelector(".gi-resize");
  handle.addEventListener("mousedown", (e) => {
    e.preventDefault(); e.stopPropagation();
    const { colW, rowH, gap } = metrics();
    const rect = item.getBoundingClientRect();
    const sx = e.clientX, sy = e.clientY, sw = rect.width, sh = rect.height;
    function move(ev) {
      let nw = Math.round((sw + ev.clientX - sx + gap) / (colW + gap));
      let nh = Math.round((sh + ev.clientY - sy + gap) / (rowH + gap));
      nw = Math.max(1, Math.min(COLS - card.x, nw));
      nh = Math.max(1, nh);
      card.w = nw; card.h = nh;
      place(item, card);
      if (state.charts[card.id]) state.charts[card.id].resize();  // 缩放后图表自适应
    }
    function up() {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      render();
    }
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
}

/* ---------------- 卡片取数与渲染 ---------------- */
async function refreshCard(id) {
  const card = state.cards.find(c => c.id === id);
  if (!card) return;
  const item = document.querySelector(`.grid-item[data-id="${id}"]`);
  const body = item && item.querySelector("[data-body]");
  if (!body) return;
  if (cardRequests.has(id)) cardRequests.get(id).abort();
  const controller = new AbortController(); cardRequests.set(id, controller);
  disposeCharts(body); delete state.charts[id];
  body.innerHTML = `<div class="muted" style="padding:12px"><span class="spin"></span>加载中…</div>`;
  try {
    const res = await post("/api/dashboard/executeSql",
      { sql: card.sql, dbConfigId: card.dbConfigId }, { signal: controller.signal });
    if (cardRequests.get(id) !== controller || !body.isConnected) return;
    const columns = res.columns || [], data = res.data || [];
    body.innerHTML = "";
    if (card.displayMode === "number") {
      renderKpi(body, columns, data, card.chartConfig?.yAxisFields?.[0]);
    } else if (card.displayMode === "table") {
      renderTable(body, columns, data, { page: 1, pageSize: 8 });
    } else {
      const div = document.createElement("div");
      div.style.width = "100%";
      div.style.height = "100%";
      div.style.minHeight = "140px";
      body.appendChild(div);
      const cc = card.chartConfig || {};
      state.charts[id] = new ChartRenderer(div, {
        data, columns, chartType: cc.chartType || "bar",
        xAxisField: cc.xAxisField || columns[0],
        yAxisFields: cc.yAxisFields?.length ? cc.yAxisFields : [columns[columns.length - 1]],
        seriesField: cc.seriesField,
      });
    }
    if (res.truncated) { const note = document.createElement("div"); note.className = "muted"; note.textContent = "当前仅展示前 500 行，请缩小范围"; body.appendChild(note); }
  } catch (e) {
    if (e.name === "AbortError" || cardRequests.get(id) !== controller || !body.isConnected) return;
    body.innerHTML = `<div class="alert" style="margin:8px">${escapeHtml(e.message)}
      <div style="margin-top:6px"><button class="sm" onclick="refreshCard('${id}')">重试</button></div></div>`;
  }
}

async function refreshAll() {
  // 逐卡片独立刷新；失败互不影响（对齐 Promise.allSettled 的隔离思路）
  await Promise.all(state.cards.map(c => refreshCard(c.id).catch(() => null)));
}

/* ---------------- 配置弹窗 ---------------- */
let editingCard = null;

function openConfig(card) {
  editingCard = card || {
    id: uuid("c"), title: "新卡片", sql: "SELECT 1 AS v", dbConfigId: state.dataSources[0]?.id || 1,
    displayMode: "chart", chartConfig: { chartType: "bar", xAxisField: "", yAxisFields: [] },
    x: 0, y: 0, w: 4, h: 5,
  };
  $("modal-title").textContent = card ? "编辑卡片" : "新增卡片";
  $("f-title").value = editingCard.title;
  $("f-ds").innerHTML = state.dataSources.map(d =>
    `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join("");
  $("f-ds").value = editingCard.dbConfigId;
  $("f-mode").value = editingCard.displayMode;
  $("f-sql").value = editingCard.sql;
  const cc = editingCard.chartConfig || {};
  $("f-chart").value = cc.chartType || "bar";
  $("f-xpos").value = editingCard.x; $("f-ypos").value = editingCard.y;
  $("f-w").value = editingCard.w; $("f-h").value = editingCard.h;
  $("f-x").innerHTML = "<option value=''>（预览后选择）</option>";
  $("f-y").innerHTML = "<option value=''>（预览后选择）</option>";
  $("f-preview-info").textContent = "";
  $("mask").classList.add("show");
  doPreview();
}

async function doPreview() {
  $("f-preview-info").innerHTML = `<span class="spin"></span>执行中…`;
  try {
    const res = await post("/api/dashboard/executeSql",
      { sql: $("f-sql").value, dbConfigId: Number($("f-ds").value) });
    const cols = res.columns || [];
    $("f-x").innerHTML = cols.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    $("f-y").innerHTML = cols.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    const cc = (editingCard && editingCard.chartConfig) || {};
    $("f-x").value = cols.includes(cc.xAxisField) ? cc.xAxisField : (cols[0] || "");
    $("f-y").value = (cc.yAxisFields && cols.includes(cc.yAxisFields[0]))
      ? cc.yAxisFields[0] : (cols[cols.length - 1] || "");
    $("f-preview-info").textContent = `预览成功：${(res.data || []).length} 行 / ${cols.length} 列`;
  } catch (e) {
    $("f-preview-info").innerHTML = `<span style="color:var(--danger)">${escapeHtml(e.message)}</span>`;
  }
}

/* ---------------- 大屏列表与持久化 ---------------- */
async function loadList() {
  const list = await get("/api/dashboard/list");
  $("dash-list").innerHTML = list.map(d =>
    `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join("");
  if (list.length) await openDash(list[0].id);
}

async function openDash(id) {
  const generation = ++loadGeneration;
  state.dashId = id;
  $("dash-list").value = id;
  try {
    const d = await get(`/api/dashboard/detail/${id}`);
    if (generation !== loadGeneration) return;
    state.name = d.name; state.description = d.description;
    applyRefreshMode(d.refresh_mode); state.cards = (d.config && d.config.cards) || [];
    $("dash-info").textContent = `${d.name} · ${state.cards.length} 张卡片 · 更新于 ${d.updated_at}`;
  } catch (e) {
    if (generation !== loadGeneration) return;
    const local = localStorage.getItem(`dashboard-config-${id}`);
    if (local) {
      try {
        const cached = JSON.parse(local);
        state.cards = cached.config?.cards || cached.cards || [];
        state.name = cached.name || "本地草稿"; state.description = cached.description || "";
        applyRefreshMode(cached.refreshMode || "off");
      } catch (_) { state.cards = []; toast("本地草稿损坏", "err"); }
      toast("接口失败，已从 localStorage 恢复配置", "err");
      $("dash-info").textContent = "（本地兜底配置）";
    } else { toast(e.message, "err"); state.cards = []; }
  }
  render();
}

async function save() {
  if (!state.dashId) { toast("请先选择或新建大屏", "err"); return; }
  normalizeLayout();
  const payload = {
    id: state.dashId, name: state.name || "未命名大屏", description: state.description,
    refreshMode: $("refresh-mode").value,
    config: { cards: state.cards },
  };
  try {
    await post("/api/dashboard/save", payload);
    localStorage.removeItem(`dashboard-config-${state.dashId}`);
    toast("已保存到后端");
  } catch (e) {
    if (e.status && e.status < 500) { toast(e.message, "err"); return; }
    localStorage.setItem(`dashboard-config-${state.dashId}`, JSON.stringify(payload));
    toast("后端保存失败，已临时保存到 localStorage", "err");
  }
}

async function newDash() {
  const name = prompt("大屏名称", "新建大屏");
  if (!name) return;
  try {
    const res = await post("/api/dashboard/save",
      { name, description: "", refreshMode: "interval", config: { cards: [] } });
    await loadList();
    await openDash(res.id);
  } catch (e) { toast(e.message, "err"); }
}

/* ---------------- 事件 ---------------- */
$("btn-edit").onclick = () => {
  state.editing = !state.editing;
  $("btn-edit").textContent = state.editing ? "退出编辑" : "进入编辑";
  render();
};
$("btn-add").onclick = () => { if (!state.editing) { state.editing = true; $("btn-edit").textContent = "退出编辑"; render(); } openConfig(null); };
$("btn-refresh").onclick = refreshAll;
$("btn-save").onclick = save;
$("btn-new-dash").onclick = newDash;
$("dash-list").onchange = (e) => openDash(Number(e.target.value));
$("refresh-mode").onchange = () => applyRefreshMode($("refresh-mode").value);
$("f-preview").onclick = doPreview;
$("f-cancel").onclick = () => $("mask").classList.remove("show");
$("f-ok").onclick = () => {
  editingCard.title = $("f-title").value || "未命名卡片";
  editingCard.dbConfigId = Number($("f-ds").value);
  editingCard.displayMode = $("f-mode").value;
  editingCard.sql = $("f-sql").value;
  editingCard.chartConfig = {
    chartType: $("f-chart").value,
    xAxisField: $("f-x").value,
    yAxisFields: [$("f-y").value],
  };
  editingCard.x = Math.max(0, Number($("f-xpos").value) || 0);
  editingCard.y = Math.max(0, Number($("f-ypos").value) || 0);
  editingCard.w = Math.max(1, Math.min(12, Number($("f-w").value) || 4));
  editingCard.h = Math.max(1, Number($("f-h").value) || 5);
  if (!state.cards.some(c => c.id === editingCard.id)) state.cards.push(editingCard);
  $("mask").classList.remove("show");
  render();
};
window.addEventListener("resize", () => {
  state.cards.forEach(c => place(document.querySelector(`.grid-item[data-id="${c.id}"]`), c));
  Object.values(state.charts).forEach(ch => ch.resize && ch.resize());
});

(async function init() {
  state.dataSources = (await get("/api/admin/ds/list")).filter(d => d.available);
  await loadList();

})().catch(e => toast(e.message, "err"));

window.addEventListener("pagehide", () => { clearInterval(state.refreshTimer); for (const c of cardRequests.values()) c.abort(); });
