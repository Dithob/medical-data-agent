/* 智能问数：会话管理 + extraData 解析 + SQL 高亮 + 结果表格分页 + 图表 + Excel 导出 + 链路追踪 */

renderNav("chat");

const SAMPLES = [
  { text: "2025年各品类销售额是多少？" },
  { text: "近12个月销售额趋势" },
  { text: "销量前10的药品" },
  { text: "各区域销售额占比" },
  { text: "2025年10月上海门店销售明细" },
  { text: "各供应商采购金额排名" },
  { text: "2025年毛利率是多少？" },
  { text: "库存金额最高的5个药品" },
  { text: "演示：字段不存在触发自纠错", forceError: true },
];

const state = {
  sessions: [],
  sessionId: null,
  messages: [],
  loading: false,
  taskId: null,
  timer: null,
  controller: null,
  dbConfigId: 1,
  pagination: {},   // 按消息下标隔离分页状态（对齐 tablePaginationMap[index]）
};

const $ = (id) => document.getElementById(id);
const scope = new RequestScope();

/* ---------------- 初始化 ---------------- */
async function loadDataSources() {
  try {
    const list = await get("/api/admin/ds/list");
    $("ds").innerHTML = list.filter(d => d.available).map(d =>
      `<option value="${d.id}">${escapeHtml(d.name)}（${escapeHtml(d.db_type)}）</option>`).join("");
    if (list.length) state.dbConfigId = list[0].id;
  } catch (e) { toast(e.message, "err"); }
}

async function loadSessions() {
  try {
    state.sessions = await get("/api/chat/sessions?module=DATA");
    renderSessions();
  } catch (e) { /* 忽略 */ }
}

function renderSessions() {
  const box = $("sessions");
  if (!state.sessions.length) { box.innerHTML = `<div class="muted">暂无历史会话</div>`; return; }
  box.innerHTML = state.sessions.map(s =>
    `<div class="row" style="justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border)">
       <a href="#" data-sid="${s.id}" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(s.title)}</a>
       <span class="muted" style="font-size:11px">#${s.id}</span>
     </div>`).join("");
  box.querySelectorAll("a[data-sid]").forEach(a => {
    a.onclick = (ev) => { ev.preventDefault(); openSession(Number(a.dataset.sid)); };
  });
}

async function openSession(sid) {
  stop(false);
  const generation = scope.generation;
  try {
    const messages = await get(`/api/chat/messages?sessionId=${sid}`);
    if (generation !== scope.generation) return;
    state.sessionId = sid; state.pagination = {}; state.messages = messages;
    renderMessages();
  } catch (e) { if (generation === scope.generation) toast(e.message, "err"); }
}

/* ---------------- 渲染 ---------------- */
function renderMessages() {
  const box = $("msg-list");
  disposeCharts(box);
  if (!state.messages.length) { box.innerHTML = `<div class="empty">暂无消息</div>`; return; }
  box.innerHTML = "";
  state.messages.forEach((m, idx) => {
    const wrap = document.createElement("div");
    wrap.className = "msg " + (m.role === "user" ? "user" : "assistant");
    wrap.innerHTML = `
      <div class="avatar">${m.role === "user" ? "我" : "AI"}</div>
      <div class="bubble">
        <div class="who">${m.role === "user" ? "我" : "AI 助手"}${m.elapsedMs ? ` · ${m.elapsedMs}ms` : ""}</div>
        <div class="content" data-idx="${idx}"></div>
        <div class="msg-actions"></div>
      </div>`;
    box.appendChild(wrap);
    const contentEl = wrap.querySelector(".content");
    const actions = wrap.querySelector(".msg-actions");

    if (m.role === "user") contentEl.textContent = m.content || "";
    else contentEl.innerHTML = safeMarkdown(m.content || "");

    const extra = m.extraData || {};
    if (m.role === "assistant") {
      if (extra.error) {
        const al = document.createElement("div");
        al.className = "alert";
        al.innerHTML = `<b>SQL 执行失败</b>：${escapeHtml(extra.error)}`;
        contentEl.appendChild(al);
      }
      if (extra.sql) contentEl.appendChild(sqlBlock(extra.sql, idx));
      if (extra.data && extra.data.length) {
        const tb = document.createElement("div");
        tb.className = "result-table";
        contentEl.appendChild(tb);
        state.pagination[idx] = state.pagination[idx] || { page: 1, pageSize: 10 };
        renderTable(tb, extra.columns || [], extra.data, state.pagination[idx]);

        const cb = document.createElement("div");
        cb.className = "chart-box";
        contentEl.appendChild(cb);
        renderChartBlock(cb, extra.columns || [], extra.data, extra.suggestion || {},
                         state.pagination[idx]);

        const exp = document.createElement("button");
        exp.className = "sm"; exp.textContent = "导出当前查询结果";
        exp.onclick = () => downloadFile(`/api/chat/export/excel?messageId=${m.id}`,
                                         `问数结果_${m.id}.xlsx`);
        actions.appendChild(exp);
      } else if (!extra.error && extra.columns) {
        const al = document.createElement("div");
        al.className = "alert warn";
        al.textContent = "【提示】 SQL生成并执行，但数据库中无数据。请检查查询条件（如时间范围）是否有误。";
        contentEl.appendChild(al);
      }
      if (m.id) {
        const rg = document.createElement("button");
        rg.className = "sm"; rg.textContent = "重新回答";
        rg.onclick = () => regenerate(m.id);
        actions.appendChild(rg);
      }
      const tr = document.createElement("button");
      tr.className = "sm"; tr.textContent = "查看链路";
      tr.onclick = () => renderTrace(extra.trace || []);
      actions.appendChild(tr);
      if (extra.trace) renderTrace(extra.trace);
    }
  });
  box.scrollTop = box.scrollHeight;
}

function sqlBlock(sql, idx) {
  const box = document.createElement("div");
  box.className = "sql-box";
  const id = "sql-" + idx;
  let html;
  try { html = hljs.highlight(sql, { language: "sql" }).value; }
  catch (e) { html = escapeHtml(sql); }
  box.innerHTML = `
    <div class="sql-head">
      <span>生成的 SQL（highlight.js 高亮）</span>
      <span class="row">
        <button class="sm" data-act="copy">复制</button>
        <button class="sm" data-act="toggle">折叠</button>
      </span>
    </div>
    <pre id="${id}" style="margin:0"><code class="language-sql">${html}</code></pre>`;
  const pre = box.querySelector("pre");
  box.querySelector('[data-act="copy"]').onclick = () => {
    navigator.clipboard.writeText(sql).then(() => toast("SQL 已复制"),
      () => toast("复制失败", "err"));
  };
  box.querySelector('[data-act="toggle"]').onclick = (e) => {
    const hidden = pre.style.display === "none";
    pre.style.display = hidden ? "" : "none";
    e.target.textContent = hidden ? "折叠" : "展开";
  };
  return box;
}

function renderTrace(trace) {
  const box = $("trace");
  if (!trace || !trace.length) { box.innerHTML = `<div class="muted">无 trace</div>`; return; }
  box.innerHTML = trace.map(t => {
    let detail = "";
    const d = t.detail || {};
    if (typeof d === "string") detail = d;
    else detail = Object.entries(d).map(([k, v]) => {
      const val = Array.isArray(v) ? v.join("、") : (typeof v === "object" ? JSON.stringify(v, null, 1) : v);
      return `${k}：${val}`;
    }).join("\n");
    return `<div class="trace-item"><div class="t-step">${escapeHtml(t.step)}</div>
            <div class="t-detail">${escapeHtml(detail)}</div></div>`;
  }).join("");
}

/* ---------------- 发送 / 停止 / 重新回答 ---------------- */
function setBusy(busy) {
  state.loading = busy;
  $("btn-send").disabled = busy;
  $("btn-stop").style.display = busy ? "" : "none";
}

async function perform(path, body, replace = false) {
  if (state.loading) return;
  const op = scope.begin();
  setBusy(true);
  $("status").textContent = "正在查询…";
  try {
    const res = await post(path, { ...body, taskId: op.taskId }, { signal: op.controller.signal });
    if (!scope.current(op)) return;
    if (replace) {
      const messages = await get(`/api/chat/messages?sessionId=${res.sessionId}`, { signal: op.controller.signal });
      if (!scope.current(op)) return;
      state.messages = messages;
    } else {
      state.messages.push(res.userMessage, res.aiMessage);
    }
    state.sessionId = res.sessionId;
    renderMessages();
    loadSessions();
    $("status").textContent = `完成，耗时 ${res.aiMessage.elapsedMs}ms`;
  } catch (e) {
    if (scope.current(op) && e.name !== "AbortError") {
      $("status").textContent = e.message; toast(e.message, "err");
    }
  } finally {
    if (scope.current(op)) { scope.done(op); setBusy(false); }
  }
}

async function send(text, forceError = false) {
  const content = (text || $("input").value || "").trim();
  if (!content) return toast("请输入问题");
  if (state.loading) return;
  $("input").value = "";
  return perform("/api/chat/send", { sessionId: state.sessionId, module: "DATA", content,
    dbConfigId: Number(state.dbConfigId), forceError: !!forceError });
}

function stop(show = true) {
  scope.cancel();
  setBusy(false);
  $("status").textContent = show ? "已停止，后续响应不会写入当前页面" : "";
}

async function regenerate(messageId) {
  return perform("/api/chat/regenerate", { messageId, module: "DATA", dbConfigId: Number(state.dbConfigId) }, true);
}

/* ---------------- 事件绑定 ---------------- */
$("btn-send").onclick = () => send();
$("btn-stop").onclick = () => stop();
$("btn-new").onclick = () => {
  stop(false);
  state.sessionId = null; state.messages = []; state.pagination = {};
  renderMessages(); $("trace").innerHTML = `<div class="muted">新会话</div>`;
};
$("ds").onchange = (e) => { state.dbConfigId = Number(e.target.value); };
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
$("samples").innerHTML = SAMPLES.map((s, i) =>
  `<button class="sm" data-i="${i}">${escapeHtml(s.text)}</button>`).join("");
$("samples").querySelectorAll("button").forEach(b => {
  b.onclick = () => { const s = SAMPLES[Number(b.dataset.i)]; send(s.forceError ? "2025年销售额" : s.text, s.forceError); };
});

(async function init() {
  await loadDataSources();
  await loadSessions();
})();
