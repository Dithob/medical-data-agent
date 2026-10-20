/* 公共：请求封装（对齐真实项目的 axios 拦截器思路）、提示、导航、格式化、表格分页 */

const NAV = [
  { href: "/index.html", text: "架构总览", key: "index" },
  { href: "/chat.html", text: "智能问数", key: "chat" },
  { href: "/dashboard.html", text: "可视化大屏", key: "dashboard" },
  { href: "/rag.html", text: "知识库问答", key: "rag" },
  { href: "/admin.html", text: "后台配置", key: "admin" },
];

function renderNav(active) {
  const box = document.getElementById("nav");
  if (!box) return;
  box.innerHTML = NAV.map(n =>
    `<a class="nav-item ${n.key === active ? "active" : ""}" href="${n.href}">${n.text}</a>`
  ).join("");
}

/* ---- 请求封装：统一处理 code/msg，非 200 抛错；401 跳登录（demo 无登录，仅提示） ---- */
async function request(path, options = {}) {
  const opts = Object.assign({ headers: {} }, options);
  if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Error("网络连接异常，请检查服务是否已启动");
  }
  if (res.status === 401) { toast("登录已失效", "err"); throw new Error("401"); }
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch (e) { throw new Error("响应解析失败：" + text.slice(0, 120)); }
  if (!res.ok || json.code !== "200") {
    const error = new Error(json.msg || "请求失败"); error.status = res.status; throw error;
  }
  return json.data;
}

const get = (p, options = {}) => request(p, { ...options, method: "GET" });
const post = (p, body, options = {}) => request(p, { ...options, method: "POST", body });

function safeMarkdown(text) {
  if (!window.DOMPurify || !window.marked) return escapeHtml(text);
  return DOMPurify.sanitize(marked.parse(text || ""), {
    ALLOWED_TAGS: ["p","br","strong","em","del","ul","ol","li","blockquote","pre","code","h1","h2","h3","h4","table","thead","tbody","tr","th","td","a","hr"],
    ALLOWED_ATTR: ["href","title"], ALLOW_DATA_ATTR: false,
  });
}

class RequestScope {
  constructor() { this.generation = 0; this.active = null; }
  cancel() {
    const op = this.active;
    this.active = null; this.generation++;
    if (op) {
      op.controller.abort();
      post("/api/chat/cancel", { taskId: op.taskId }).catch(() => {});
    }
    return this.generation;
  }
  begin() {
    this.cancel();
    const op = { generation: this.generation, taskId: uuid("task"), controller: new AbortController() };
    this.active = op; return op;
  }
  current(op) { return this.active === op && op.generation === this.generation; }
  done(op) { if (this.current(op)) this.active = null; }
}

let _toastTimer = null;
function toast(msg, type = "") {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.className = "toast show " + (type || "");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = "toast " + (type || ""); }, 2600);
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* 数字格式化：千分位 / 万 / 亿（对齐大屏 KPI 卡片） */
function formatNumber(v) {
  if (v === null || v === undefined || v === "") return "-";
  if (typeof v === "number") {
    if (!isFinite(v)) return "-";
    const abs = Math.abs(v);
    if (abs >= 1e8) return (v / 1e8).toFixed(2) + " 亿";
    if (abs >= 1e4) return (v / 1e4).toFixed(2) + " 万";
    if (Number.isInteger(v)) return v.toLocaleString("zh-CN");
    return Number(v.toFixed(2)).toLocaleString("zh-CN");
  }
  return String(v);
}

function isNum(v) { return typeof v === "number" && isFinite(v); }

/* 通用结果表格：columns + data + 前端分页（按消息/卡片隔离分页状态） */
function renderTable(container, columns, data, state) {
  state.page = Math.max(1, Math.min(state.page || 1, Math.ceil(data.length / (state.pageSize || 10)) || 1));
  state.pageSize = state.pageSize || 10;
  const total = data.length;
  const start = (state.page - 1) * state.pageSize;
  const rows = data.slice(start, start + state.pageSize);
  if (!total) {
    container.innerHTML = `<div class="empty">SQL 已执行，但无数据</div>`;
    return;
  }
  const head = columns.map(c => `<th>${escapeHtml(c)}</th>`).join("");
  const body = rows.map(r => "<tr>" + r.map(v =>
    `<td>${isNum(v) ? formatNumber(v) : escapeHtml(v)}</td>`).join("") + "</tr>").join("");
  const pages = Math.max(1, Math.ceil(total / state.pageSize));
  container.innerHTML = `
    <div style="overflow-x:auto">
      <table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
    </div>
    <div class="row" style="margin:8px 4px;justify-content:space-between">
      <span class="muted">当前结果 ${total} 行 · 第 ${state.page}/${pages} 页</span>
      <span class="row">
        <button class="sm" data-p="${state.page - 1}" ${state.page <= 1 ? "disabled" : ""}>上一页</button>
        <button class="sm" data-p="${state.page + 1}" ${state.page >= pages ? "disabled" : ""}>下一页</button>
      </span>
    </div>`;
  container.querySelectorAll("button[data-p]").forEach(btn => {
    btn.onclick = () => { state.page = Number(btn.dataset.p); renderTable(container, columns, data, state); };
  });
}

async function downloadFile(url, filename) {
  try {
    const response = await fetch(url);
    if (!response.ok || (response.headers.get("content-type") || "").includes("json")) {
      const body = await response.json(); throw new Error(body.msg || "导出失败");
    }
    const blob = await response.blob();
    const match = (response.headers.get("content-disposition") || "").match(/filename="([^"]+)"/);
    const u = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = u; a.download = (match && match[1]) || filename || "download";
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(u); a.remove(); }, 1000);
  } catch (e) { toast(e.message || "导出失败", "err"); }
}

function uuid(prefix = "t") {
  return prefix + (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now());
}

async function showRuntime() {
  const main = document.querySelector("main");
  if (!main) return;
  const banner = document.createElement("div"); banner.className = "alert warn";
  main.prepend(banner);
  try {
    const runtime = await get("/api/health");
    const range = runtime.dataCoverage.fact_sales;
    banner.textContent = `离线演示 · 规则问数 / 原文抽取 · 演示参考日 ${runtime.asOfDate} · 销售数据 ${range.start}～${range.end}`;
  } catch (e) { banner.textContent = e.message; }
}
showRuntime();
