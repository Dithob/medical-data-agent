/* RAG 知识库问答：打字机 + 停止生成(taskId) + 重新回答 + 复制 + 参考文档溯源与预览 */

renderNav("rag");
const $ = (id) => document.getElementById(id);
const scope = new RequestScope();
let typingDone = null;

const SAMPLES = [
  "处方药销售有什么要求？",
  "药品储存的温湿度要求是什么？",
  "近效期药品怎么分级预警？",
  "供应商评估为 C 级会怎么处理？",
  "药品召回分为几级，时限分别是多少？",
  "冷链药品交接有什么要求？",
];

const state = {
  sessionId: null, messages: [], loading: false, taskId: null,
  controller: null, typeTimer: null,
};

/* ---------------- 会话 ---------------- */
async function loadSessions() {
  const list = await get("/api/chat/sessions?module=QA");
  $("sessions").innerHTML = list.length
    ? list.map(s => `<div class="row" style="padding:5px 0;border-bottom:1px solid var(--border)">
        <a href="#" data-sid="${s.id}" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(s.title)}</a></div>`).join("")
    : `<div class="muted">暂无历史会话</div>`;
  $("sessions").querySelectorAll("a[data-sid]").forEach(a => {
    a.onclick = (ev) => { ev.preventDefault(); openSession(Number(a.dataset.sid)); };
  });
}

async function openSession(sid) {
  stop(false);
  const generation = scope.generation;
  try {
    const messages = await get(`/api/chat/messages?sessionId=${sid}`);
    if (generation !== scope.generation) return;
    state.sessionId = sid; state.messages = messages; renderMessages();
  } catch (e) { if (generation === scope.generation) toast(e.message, "err"); }
}

/* ---------------- 渲染 + 打字机 ---------------- */
function renderMessages(animateId = null) {
  finishTyping();
  let animation = Promise.resolve();
  const box = $("msg-list");
  if (!state.messages.length) { box.innerHTML = `<div class="empty">暂无消息</div>`; return; }
  box.innerHTML = "";
  state.messages.forEach(m => {
    const wrap = document.createElement("div");
    wrap.className = "msg " + (m.role === "user" ? "user" : "assistant");
    wrap.innerHTML = `<div class="avatar">${m.role === "user" ? "我" : "AI"}</div>
      <div class="bubble"><div class="who">${m.role === "user" ? "我" : "AI 助手"}</div>
      <div class="content"></div><div class="msg-actions"></div></div>`;
    box.appendChild(wrap);
    const c = wrap.querySelector(".content");
    if (m.role === "assistant") {
      if (m.id === animateId) animation = typewriter(c, m.content || "");
      else c.innerHTML = safeMarkdown(m.content || "");
      const acts = wrap.querySelector(".msg-actions");
      const cp = document.createElement("button");
      cp.className = "sm"; cp.textContent = "复制";
      cp.onclick = () => copyText(m.content || "");
      acts.appendChild(cp);
      if (m.id) {
        const rg = document.createElement("button");
        rg.className = "sm"; rg.textContent = "重新回答";
        rg.onclick = () => regenerate(m.id);
        acts.appendChild(rg);
      }
      if (m.referDoc && m.referDoc.length) {
        const rd = document.createElement("span");
        rd.className = "tag";
        rd.style.cursor = "pointer";
        rd.textContent = `引用片段 ${m.referDoc.length} 条`;
        rd.onclick = () => showRefs(m.referDoc);
        acts.appendChild(rd);
      }
    } else {
      c.textContent = m.content;
    }
  });
  box.scrollTop = box.scrollHeight;
  return animation;
}

function finishTyping() {
  clearInterval(state.typeTimer);
  if (typingDone) { const done = typingDone; typingDone = null; done(); }
}

function typewriter(el, text) {
  if (text.length > 3000) { el.innerHTML = safeMarkdown(text); return Promise.resolve(); }
  return new Promise(resolve => {
    typingDone = resolve;
    let index = 0;
    const step = Math.max(4, Math.ceil(text.length / 90));
    state.typeTimer = setInterval(() => {
      index += step;
      // Partial markdown is plain text. Parse and sanitize once at completion.
      el.textContent = text.slice(0, index);
      if (index >= text.length) { el.innerHTML = safeMarkdown(text); finishTyping(); }
      $("msg-list").scrollTop = $("msg-list").scrollHeight;
    }, 20);
  });
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => toast("已复制"), () => fallbackCopy(text));
  } else fallbackCopy(text);
}
function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); toast("已复制"); } catch (e) { toast("复制失败", "err"); }
  ta.remove();
}

function showRefs(refs) {
  const html = refs.map((r, i) =>
    `<div style="padding:8px 0;border-bottom:1px solid var(--border)">
       <div class="row" style="justify-content:space-between">
         <b>[${i + 1}] ${escapeHtml(r.docName)}</b>
         <span class="row"><span class="tag gray">相似度 ${r.score}</span>
         <button class="sm" data-doc="${r.docId}">预览原文</button></span>
       </div>
       <div class="muted" style="margin-top:4px">${escapeHtml(r.snippet.slice(0, 120))}…</div>
     </div>`).join("");
  $("doc-title").textContent = "参考文档（referDoc）";
  $("doc-body").innerHTML = html;
  $("doc-body").querySelectorAll("button[data-doc]").forEach(b => {
    b.onclick = () => previewDoc(Number(b.dataset.doc));
  });
  $("mask").classList.add("show");
}

async function previewDoc(docId) {
  $("doc-title").textContent = "文档原文预览（真实项目为 PDF Blob + iframe）";
  $("doc-body").textContent = "加载中…";
  try {
    const res = await fetch(`/api/chat/pdf/preview/${docId}`);
    $("doc-body").textContent = await res.text();
  } catch (e) { $("doc-body").textContent = "文档加载失败"; }
}

/* ---------------- 发送 / 停止 / 重新回答 ---------------- */
function setBusy(busy) {
  state.loading = busy;
  $("btn-send").disabled = busy;
  $("btn-stop").style.display = busy ? "" : "none";
}

async function perform(path, body, replace = false) {
  if (state.loading) return;
  const op = scope.begin(); setBusy(true);
  $("btn-stop").textContent = "停止生成";
  $("status").textContent = "正在检索知识库…";
  try {
    const res = await post(path, { ...body, taskId: op.taskId }, { signal: op.controller.signal });
    if (!scope.current(op)) return;
    if (replace) {
      const messages = await get(`/api/chat/messages?sessionId=${res.sessionId}`, { signal: op.controller.signal });
      if (!scope.current(op)) return;
      state.messages = messages;
    } else state.messages.push(res.userMessage, res.aiMessage);
    state.sessionId = res.sessionId;
    renderStats(res.aiMessage.extraData && res.aiMessage.extraData.trace);
    loadSessions().catch(e => toast(e.message, "err"));
    $("btn-stop").textContent = "停止显示";
    await renderMessages(res.aiMessage.id);
    if (scope.current(op)) $("status").textContent = `完成，耗时 ${res.aiMessage.elapsedMs}ms`;
  } catch (e) {
    if (scope.current(op) && e.name !== "AbortError") { $("status").textContent = e.message; toast(e.message, "err"); }
  } finally {
    if (scope.current(op)) { scope.done(op); setBusy(false); }
  }
}

async function send(text) {
  const content = (text || $("input").value || "").trim();
  if (!content) return toast("请输入问题");
  if (state.loading) return;
  $("input").value = "";
  return perform("/api/chat/send", { sessionId: state.sessionId, module: "QA", content });
}

function renderStats(trace) {
  const t = (trace || []).find(x => x.step === "离线检索");
  if (!t) return;
  const s = t.detail || {};
  $("stats").innerHTML = `
    <table>
      <tr><td class="muted">知识库片段总量</td><td>${s.totalChunks}</td></tr>
      <tr><td class="muted">召回候选</td><td>${s.candidates}</td></tr>
      <tr><td class="muted">阈值过滤后</td><td>${s.afterThreshold}（阈值 ${s.threshold}）</td></tr>
      <tr><td class="muted">TopK 截取（未启用重排）</td><td>${s.afterRerank}（rerankKeep ${s.rerankKeep}）</td></tr>
      <tr><td class="muted">参与生成</td><td>${s.used}</td></tr>
    </table>`;
}

function stop(show = true) {
  scope.cancel(); finishTyping(); setBusy(false);
  $("status").textContent = show ? "已停止，后续响应不会写入当前页面" : "";
}

async function regenerate(messageId) {
  return perform("/api/chat/regenerate", { messageId, module: "QA" }, true);
}

/* ---------------- 事件 ---------------- */
$("btn-send").onclick = () => send();
$("btn-stop").onclick = () => stop();
$("btn-new").onclick = () => { stop(false); state.sessionId = null; state.messages = []; renderMessages(); };
$("input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
$("samples").innerHTML = SAMPLES.map((s, i) =>
  `<button class="sm" data-i="${i}">${escapeHtml(s)}</button>`).join("");
$("samples").querySelectorAll("button").forEach(b => {
  b.onclick = () => send(SAMPLES[Number(b.dataset.i)]);
});
$("doc-close").onclick = () => $("mask").classList.remove("show");

loadSessions().catch(e => toast(e.message, "err"));
