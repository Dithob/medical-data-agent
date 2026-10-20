"""Browser regression: python tests/browser_regression.py --python <app-python> [--browser <chromium>]."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--python", required=True)
parser.add_argument("--browser")
parser.add_argument("--output", default="test-results")
args = parser.parse_args()
BASE = Path(__file__).resolve().parents[1]
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
results = []
errors = []
external = []


def check(name, condition, detail=None):
    results.append({"name": name, "passed": bool(condition), "detail": detail})
    if not condition:
        raise AssertionError(f"{name}: {detail}")


with tempfile.TemporaryDirectory(prefix="medical-rag-browser-") as data:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = dict(
        os.environ, MEDICAL_RAG_DATA_DIR=data, MEDICAL_RAG_AS_OF_DATE="2026-08-31"
    )
    with (output / "server.log").open("w", encoding="utf8") as log:
        proc = subprocess.Popen(
            [
                args.python,
                "-m",
                "uvicorn",
                "server.app:app",
                "--app-dir",
                str(BASE),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(url + "/api/health", timeout=1).close()
                    break
                except Exception:
                    time.sleep(0.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    **({"executable_path": args.browser} if args.browser else {}),
                )
                context = browser.new_context(
                    viewport={"width": 1440, "height": 1000}, accept_downloads=True
                )

                def network(route):
                    if route.request.url.startswith(url):
                        route.continue_()
                    else:
                        external.append(route.request.url)
                        route.abort()

                context.route("**/*", network)
                page = context.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                for path in [
                    "index.html",
                    "chat.html",
                    "rag.html",
                    "dashboard.html",
                    "admin.html",
                ]:
                    response = page.goto(url + "/" + path, wait_until="networkidle")
                    check("page_" + path, response.status == 200)
                page.goto(url + "/chat.html", wait_until="networkidle")
                page.locator("#input").fill("2025年各品类销售额")
                page.locator("#btn-send").click()
                page.wait_for_function("!state.loading && state.messages.length === 2")
                check(
                    "chat_table_chart",
                    page.locator(".result-table table").count() == 1
                    and page.locator("canvas").count() == 1,
                )
                with page.expect_download() as download:
                    page.get_by_role("button", name="导出当前查询结果").click()
                check(
                    "excel_download",
                    download.value.suggested_filename.endswith(".xlsx"),
                )
                page.evaluate("send('各月份各区域销售额趋势')")
                check(
                    "multiseries",
                    page.evaluate(
                        "[...liveCharts.values()].at(-1).chart.getOption().series.length"
                    )
                    == 5,
                )
                baseline = page.evaluate("liveCharts.size")
                for _ in range(5):
                    page.locator(".chart-box").last.get_by_role(
                        "button", name="bar", exact=True
                    ).click()
                    page.locator(".chart-box").last.get_by_role(
                        "button", name="line", exact=True
                    ).click()
                check("chart_dispose", page.evaluate("liveCharts.size") == baseline)
                page.evaluate("send('销售额<img src=x onerror=\"window.__xss=1\">')")
                check(
                    "user_plain_text",
                    page.evaluate("window.__xss !== 1")
                    and page.locator(".user .content").last.locator("img").count() == 0,
                )
                page.evaluate(
                    """() => {const box=document.createElement('div');box.id='sanitize-probe';document.body.appendChild(box);box.innerHTML=safeMarkdown('<img src=x onerror="window.__xss=1"><svg onload="window.__xss=1"></svg>[bad](javascript:window.__xss=1)<strong>ok</strong>');}"""
                )
                check(
                    "markdown_sanitized",
                    page.evaluate(
                        "window.__xss !== 1 && !document.querySelector('#sanitize-probe img,#sanitize-probe svg,#sanitize-probe [onerror],#sanitize-probe a[href]')"
                    ),
                )
                page.goto(url + "/rag.html", wait_until="networkidle")
                for q in [
                    "冷链药品交接有什么要求？",
                    "药品召回分为几级，时限分别是多少？",
                    "近效期药品怎么分级预警？",
                ]:
                    page.locator("#input").fill(q)
                    page.locator("#btn-send").click()
                    page.wait_for_function("!state.loading")
                answers = page.locator(".assistant .content").all_text_contents()
                check(
                    "rag_three_turns",
                    len(answers) == 3
                    and all(answers)
                    and "三十分钟" in answers[0]
                    and "七十二小时" in answers[1],
                )
                sid = page.evaluate("state.sessionId")
                page.reload(wait_until="networkidle")
                page.evaluate(f"openSession({sid})")
                check(
                    "rag_history",
                    all(page.locator(".assistant .content").all_text_contents())
                    and page.locator(".assistant").count() == 3,
                )
                page.locator(".assistant").last.get_by_role(
                    "button", name="重新回答"
                ).click()
                page.wait_for_function("!state.loading")
                check(
                    "regenerate_no_duplicate",
                    page.locator(".user").count() == 3
                    and page.locator(".assistant").count() == 3,
                )
                page.locator(".assistant").first.get_by_text(
                    "引用片段", exact=False
                ).click()
                page.locator("button[data-doc]").first.click()
                page.wait_for_function(
                    'document.getElementById("doc-body").textContent.includes("版本")'
                )
                check(
                    "reference_preview",
                    "交接" in page.locator("#doc-body").inner_text(),
                )
                page.locator("#doc-close").click()
                for path, module in [("chat.html", "DATA"), ("rag.html", "QA")]:
                    page.goto(url + "/" + path, wait_until="networkidle")
                    payload = page.request.post(
                        url + "/api/chat/send",
                        data={
                            "content": "销售额" if module == "DATA" else "冷链交接",
                            "module": module,
                        },
                    ).json()
                    held = []
                    page.route("**/api/chat/send", lambda route: held.append(route))
                    page.locator("#input").fill(
                        "销售额" if module == "DATA" else "冷链交接"
                    )
                    page.locator("#btn-send").click()
                    page.wait_for_timeout(100)
                    page.locator("#btn-stop").click()
                    try:
                        held[0].fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(payload),
                        )
                    except Exception:
                        pass  # The browser may already have aborted the intercepted request.
                    page.wait_for_timeout(100)
                    check(
                        "cancel_" + module,
                        page.locator(".msg").count() == 0
                        and "已停止" in page.locator("#status").inner_text(),
                    )
                    held.clear()
                    page.locator("#input").fill(
                        "销售额" if module == "DATA" else "冷链交接"
                    )
                    page.locator("#btn-send").click()
                    page.wait_for_timeout(100)
                    page.locator("#btn-new").click()
                    try:
                        held[0].fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(payload),
                        )
                    except Exception:
                        pass
                    page.wait_for_timeout(100)
                    check(
                        "new_session_isolation_" + module,
                        page.locator(".msg").count() == 0
                        and page.evaluate("state.sessionId") is None,
                    )
                    page.unroute("**/api/chat/send")
                page.goto(url + "/dashboard.html", wait_until="networkidle")
                check(
                    "dashboard_initial",
                    page.locator(".grid-item").count() == 8
                    and page.locator(".alert:not(.warn)").count() == 0,
                )
                for _ in range(5):
                    page.evaluate("refreshAll()")
                check("dashboard_chart_dispose", page.evaluate("liveCharts.size") == 4)
                page.locator("#btn-edit").click()
                page.wait_for_timeout(100)
                head = page.locator('.grid-item[data-id="c1"] .gi-head').bounding_box()
                page.mouse.move(head["x"] + 30, head["y"] + 15)
                page.mouse.down()
                page.mouse.move(head["x"] + 30, head["y"] + 15 + 272, steps=8)
                page.mouse.up()
                page.wait_for_timeout(150)
                position = page.evaluate("state.cards.find(c=>c.id==='c1').y")
                handle = page.locator('.grid-item[data-id="c1"] .gi-resize')
                handle.scroll_into_view_if_needed()
                box = handle.bounding_box()
                page.mouse.move(box["x"] + 3, box["y"] + 3)
                page.mouse.down()
                page.mouse.move(box["x"] + 3, box["y"] + 71, steps=5)
                page.mouse.up()
                height = page.evaluate("state.cards.find(c=>c.id==='c1').h")
                page.locator("#refresh-mode").select_option("off")
                page.locator("#btn-save").click()
                page.wait_for_function(
                    'document.getElementById("toast")?.textContent.includes("已保存到后端")'
                )
                page.reload(wait_until="networkidle")
                check(
                    "dashboard_drag_resize_persist",
                    page.evaluate("state.cards.find(c=>c.id==='c1').y") == position
                    and page.evaluate("state.cards.find(c=>c.id==='c1').h") == height
                    and height > 3,
                )
                check(
                    "dashboard_refresh_mode",
                    page.locator("#refresh-mode").input_value() == "off"
                    and page.evaluate("state.refreshTimer === null"),
                )
                page.route("**/api/dashboard/save", lambda route: route.abort())
                page.evaluate("save()")
                check(
                    "dashboard_offline_draft",
                    page.evaluate(
                        "localStorage.getItem(`dashboard-config-${state.dashId}`) !== null"
                    ),
                )
                page.unroute("**/api/dashboard/save")
                page.route("**/api/dashboard/detail/1", lambda route: route.abort())
                page.evaluate("openDash(1)")
                check(
                    "dashboard_draft_restore",
                    page.locator(".grid-item").count() == 8
                    and "本地兜底" in page.locator("#dash-info").inner_text(),
                )
                page.unroute("**/api/dashboard/detail/1")
                # A broken card must leave the other cards usable.
                page.evaluate(
                    "state.cards[0].sql='SELECT missing FROM fact_sales'; refreshAll()"
                )
                check(
                    "dashboard_error_isolation",
                    page.locator(".grid-item .alert").count() == 1
                    and page.evaluate("liveCharts.size") == 4,
                )
                page.screenshot(path=str(output / "dashboard.png"), full_page=True)
                page.goto(url + "/admin.html", wait_until="networkidle")
                page.locator('#ds-table button[data-test="2"]').click()
                page.wait_for_function(
                    'document.getElementById("toast")?.textContent.includes("未接入")'
                )
                check(
                    "ds_status_truth", "未接入" in page.locator("#toast").inner_text()
                )
                page.locator('button[data-tab="doc"]').click()
                page.locator("#doc-file").set_input_files(
                    {
                        "name": "browser-note.txt",
                        "mimeType": "text/plain",
                        "buffer": '斑马运输交接需要双人核验。<img src=x onerror="window.__doc_xss=1">'.encode(),
                    }
                )
                page.locator("#doc-upload").click()
                page.wait_for_function(
                    'document.getElementById("doc-tip").textContent.includes("入库成功")'
                )
                check(
                    "upload_ui",
                    "browser-note.txt" in page.locator("#doc-table").inner_text(),
                )
                page.locator('button[data-tab="stats"]').click()
                page.wait_for_function(
                    'document.getElementById("stat-table").textContent.includes("SELECT")'
                )
                check("stats_sql", "SELECT" in page.locator("#stat-table").inner_text())
                page.goto(url + "/rag.html", wait_until="networkidle")
                page.evaluate("send('斑马运输交接')")
                check(
                    "uploaded_markdown_safe",
                    page.evaluate("window.__doc_xss !== 1")
                    and page.locator(".assistant img").count() == 0,
                )
                check("no_page_errors", not errors, errors)
                check("offline_assets", not external, external)
                browser.close()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            (output / "browser-results.json").write_text(
                json.dumps(
                    {"checks": results, "errors": errors, "externalRequests": external},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf8",
            )
print(f"{len(results)} browser checks passed")
