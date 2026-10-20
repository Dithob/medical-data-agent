/* Local demo administration. Configuration status reflects actual providers. */
renderNav("admin");
const $ = id => document.getElementById(id);
let statRows = [], statPage = 1;
let docsGeneration = 0;
const statusName = {indexed:"已入库",parsing:"解析中",failed:"失败",executed:"执行完成",empty:"无数据",no_evidence:"无证据",evidence_found:"已检索证据",cancelled:"已取消",rejected:"不支持/参数错误"};
const action = fn => async (...args) => { try { await fn(...args); } catch (e) { toast(e.message, "err"); } };

$("tabs").querySelectorAll("button").forEach(button => {
  button.onclick = action(async () => {
    $("tabs").querySelectorAll("button").forEach(b => b.classList.toggle("primary", b === button));
    document.querySelectorAll("[data-panel]").forEach(panel => { panel.style.display = panel.dataset.panel === button.dataset.tab ? "" : "none"; });
    if (button.dataset.tab === "stats") await loadStats();
    if (button.dataset.tab === "doc") await loadDocs();
  });
});

async function loadDs() {
  const sources = await get("/api/admin/ds/list");
  $("ds-table").innerHTML = `<thead><tr><th>名称</th><th>类型</th><th>状态</th><th>操作</th></tr></thead><tbody>` + sources.map(d =>
    `<tr><td>${escapeHtml(d.name)}</td><td>${escapeHtml(d.db_type)}</td><td>${escapeHtml(d.status)}</td><td><button class="sm" data-test="${d.id}">检查可用性</button></td></tr>`).join("") + "</tbody>";
  $("ds-table").querySelectorAll("button[data-test]").forEach(button => {
    button.onclick = action(async () => {
      const result = await post("/api/admin/ds/test", {id: Number(button.dataset.test)});
      toast(result.msg, result.ok ? "" : "err");
    });
  });
}

async function loadModels() {
  const models = await get("/api/admin/model/list");
  $("model-table").innerHTML = `<thead><tr><th>名称</th><th>引擎</th><th>状态</th></tr></thead><tbody>` + models.map(m =>
    `<tr><td>${escapeHtml(m.name)}</td><td>${escapeHtml(m.model_name)}</td><td>${escapeHtml(m.status)}</td></tr>`).join("") + "</tbody>";
}

async function loadDocs() {
  const generation = ++docsGeneration;
  const docs = await get("/api/admin/doc/list");
  if (generation !== docsGeneration) return;
  $("doc-table").innerHTML = `<thead><tr><th>文档</th><th>当前版本 / 历史版本数</th><th>字节</th><th>片段</th><th>状态</th></tr></thead><tbody>` + docs.map(d =>
    `<tr><td>${escapeHtml(d.doc_name)}</td><td>${d.version} / ${d.versions}</td><td>${d.size}</td><td>${d.chunks}</td><td>${escapeHtml(statusName[d.status] || d.status)}</td></tr>`).join("") + "</tbody>";
}

$("doc-upload").onclick = action(async () => {
  const file = $("doc-file").files[0];
  if (!file) return toast("请选择文件");
  if (file.size > 2 * 1024 * 1024) return toast("文件不得超过 2 MiB", "err");
  const data = new FormData(); data.append("file", file);
  $("doc-upload").disabled = true; $("doc-tip").textContent = "正在解析并构建索引…";
  try {
    const result = await post("/api/admin/doc/upload", data);
    await loadDocs();
    $("doc-tip").textContent = `入库成功：版本 ${result.version}，${result.chunks} 个片段`;
  } catch (e) { $("doc-tip").textContent = e.message; }
  finally { $("doc-upload").disabled = false; }
});

async function loadStats() {
  const data = await get("/api/admin/stats/overview");
  statRows = data.recent || [];
  const sql = data.byModule.find(m => m.module === "DATA");
  $("stat-cards").innerHTML = `<div>总请求：${data.total}</div><div>SQL 执行完成：${sql?.ok || 0} / ${sql?.c || 0}</div><div>最近明细：最多 200 条</div><div>执行成功不代表语义正确</div>`;
  statPage = 1; renderStats();
}

function renderStats() {
  const keyword = $("stat-kw").value.trim(), module = $("stat-module").value;
  const rows = statRows.filter(r => (!module || r.module === module) && (!keyword || r.question.includes(keyword)));
  const pages = Math.max(1, Math.ceil(rows.length / 10)); statPage = Math.max(1, Math.min(pages, statPage));
  $("stat-table").innerHTML = `<div style="overflow:auto"><table><thead><tr><th>时间</th><th>模块</th><th>问题</th><th>SQL</th><th>毫秒</th><th>状态</th></tr></thead><tbody>` + rows.slice((statPage-1)*10,statPage*10).map(r =>
    `<tr><td>${escapeHtml(r.ts)}</td><td>${escapeHtml(r.module)}</td><td>${escapeHtml(r.question)}</td><td><pre style="max-width:320px;white-space:pre-wrap">${escapeHtml(r.sql_text)}</pre></td><td>${r.elapsed_ms}</td><td>${escapeHtml(statusName[r.status] || r.status)}</td></tr>`).join("") + `</tbody></table></div><div class="row"><button id="prev" ${statPage <= 1 ? "disabled" : ""}>上一页</button><span>${statPage}/${pages}</span><button id="next" ${statPage >= pages ? "disabled" : ""}>下一页</button></div>`;
  $("prev").onclick = () => { statPage--; renderStats(); };
  $("next").onclick = () => { statPage++; renderStats(); };
}
$("stat-search").onclick = () => { statPage = 1; renderStats(); };
Promise.all([loadDs(), loadModels(), loadDocs()]).catch(e => toast(e.message, "err"));
