"""Offline medical data demo API. Single local process, no external model calls."""

import io
import json
import logging
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from .config import STATIC_DIR, MAX_UPLOAD_BYTES, AS_OF_DATE
from .db import init_all, platform_db, biz_conn
from .documents import DOCUMENTS
from .rag import ENGINE
from .text2sql import run_question
from .executor import execute_sql
from .tasks import TASKS


def now():
    return datetime.now().isoformat(timespec="seconds")


def ok(data=None):
    return {"code": "200", "msg": "success", "data": data}


def fail(message, status=400):
    return JSONResponse(
        {"code": str(status), "msg": message, "data": None}, status_code=status
    )


@asynccontextmanager
async def lifespan(app):
    init_all()
    DOCUMENTS.initialize()
    ENGINE.build()
    yield


app = FastAPI(title="医药数据分析 · 离线演示", version="2.0.0", lifespan=lifespan)


class UploadLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/admin/doc/upload":
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body.extend(event.get("body", b""))
            if len(body) > MAX_UPLOAD_BYTES + 65536:
                return await fail("上传请求超过大小限制", 413)(scope, receive, send)
            if not event.get("more_body", False):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


app.add_middleware(UploadLimitMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return fail(
        "参数校验失败："
        + "; ".join(str(e["loc"][-1]) + ": " + e["msg"] for e in exc.errors()),
        422,
    )


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return fail(str(exc))


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    logging.exception("request failed", exc_info=exc)
    return fail("服务处理失败，请查看本地服务日志", 500)


class ChatSendReq(BaseModel):
    sessionId: int | None = Field(default=None, gt=0)
    module: Literal["DATA", "QA"] = "DATA"
    content: str = Field(min_length=1, max_length=2000)
    taskId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")
    dbConfigId: int = 1
    forceError: bool = False


class TaskReq(BaseModel):
    taskId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    sessionId: int | None = None


class RegenerateReq(BaseModel):
    messageId: int = Field(gt=0)
    module: Literal["DATA", "QA"]
    dbConfigId: int = 1
    taskId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,100}$")


def log_request(module, question, sql, elapsed, status):
    with platform_db() as con:
        con.execute(
            "INSERT INTO ask_log(ts,user_name,module,question,sql_text,elapsed_ms,status) VALUES (?,?,?,?,?,?,?)",
            (now(), "demo_user", module, question, sql, elapsed, status),
        )


def process_chat(req, old=None):
    start = time.monotonic()
    task = None
    sql = ""
    status = "failed"
    try:
        if not req.content.strip():
            raise ValueError("问题内容不能为空")
        if req.module == "DATA" and req.dbConfigId != 1:
            raise ValueError("仅支持内置 SQLite 数据源（ID 1）")
        if req.sessionId:
            with platform_db() as con:
                session = con.execute(
                    "SELECT module FROM chat_session WHERE id=?", (req.sessionId,)
                ).fetchone()
            if not session or session["module"] != req.module:
                raise ValueError("会话不存在或模块不匹配")
        task = TASKS.start(req.taskId or uuid.uuid4().hex, req.sessionId)
        if req.module == "DATA":
            result = run_question(
                req.content, req.dbConfigId, req.forceError, cancel=task.event
            )
            sql = result["sql"]
            status = result["status"]
            extra = {k: v for k, v in result.items() if k != "content"}
            content = result["content"]
            refs = []
        else:
            result = ENGINE.answer(req.content, cancel=task.event)
            content = result["content"]
            refs = result["referDoc"]
            status = result["status"]
            extra = {
                "trace": [{"step": "离线检索", "detail": result["stats"]}],
                "provider": "offline-extractive",
                "status": status,
            }
        elapsed = int((time.monotonic() - start) * 1000)

        def persist():
            with platform_db() as con:
                sid = req.sessionId
                if not sid:
                    sid = con.execute(
                        "INSERT INTO chat_session(module,title,created_at) VALUES (?,?,?)",
                        (req.module, req.content[:30], now()),
                    ).lastrowid
                if old:
                    active = con.execute(
                        "SELECT active FROM chat_message WHERE id=?", (old["id"],)
                    ).fetchone()
                    if not active or not active["active"]:
                        raise ValueError("回答已更新，请刷新后重试")
                    user_id = old["reply_to"]
                else:
                    user_id = con.execute(
                        "INSERT INTO chat_message(session_id,role,content,created_at) VALUES (?,?,?,?)",
                        (sid, "user", req.content, now()),
                    ).lastrowid
                ai_id = con.execute(
                    "INSERT INTO chat_message(session_id,role,content,extra_data,refer_doc,created_at,reply_to,supersedes) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        sid,
                        "assistant",
                        content,
                        json.dumps(extra, ensure_ascii=False),
                        json.dumps(refs, ensure_ascii=False),
                        now(),
                        user_id,
                        old["id"] if old else None,
                    ),
                ).lastrowid
                if old:
                    con.execute(
                        "UPDATE chat_message SET active=0 WHERE id=?", (old["id"],)
                    )
            return ok(
                {
                    "sessionId": sid,
                    "taskId": task.id,
                    "state": "completed",
                    "module": req.module,
                    "userMessage": {
                        "id": user_id,
                        "role": "user",
                        "content": req.content,
                    },
                    "aiMessage": {
                        "id": ai_id,
                        "role": "assistant",
                        "content": content,
                        "extraData": extra,
                        "referDoc": refs,
                        "elapsedMs": elapsed,
                    },
                }
            )

        # Failed regeneration retains the previous answer and does not replace it with an error.
        if old and status == "failed":
            raise ValueError(content)
        return TASKS.publish(task, persist)
    except InterruptedError:
        status = "cancelled"
        return fail("任务已取消", 409)
    except (ValueError, TimeoutError, sqlite3.Error) as exc:
        status = "rejected" if isinstance(exc, ValueError) else "failed"
        return fail(
            str(exc),
            400
            if isinstance(exc, ValueError)
            else 408
            if isinstance(exc, TimeoutError)
            else 500,
        )
    except Exception:
        status = "failed"
        raise
    finally:
        if task:
            TASKS.finish(task)
        log_request(
            req.module, req.content, sql, int((time.monotonic() - start) * 1000), status
        )


@app.post("/api/chat/send")
def chat_send(req: ChatSendReq):
    return process_chat(req)


@app.get("/api/chat/sessions")
def chat_sessions(module: Literal["DATA", "QA"] = "DATA"):
    with platform_db() as con:
        rows = con.execute(
            "SELECT id,title,created_at FROM chat_session WHERE module=? ORDER BY id DESC LIMIT 50",
            (module,),
        ).fetchall()
    return ok([dict(r) for r in rows])


@app.get("/api/chat/messages")
def chat_messages(sessionId: int):
    with platform_db() as con:
        if not con.execute(
            "SELECT id FROM chat_session WHERE id=?", (sessionId,)
        ).fetchone():
            return fail("会话不存在", 404)
        rows = con.execute(
            "SELECT * FROM chat_message WHERE session_id=? AND active=1 ORDER BY COALESCE(reply_to,id), CASE role WHEN 'user' THEN 0 ELSE 1 END,id",
            (sessionId,),
        ).fetchall()
    result = []
    for row in rows:
        m = dict(row)
        m["extraData"] = json.loads(m.pop("extra_data") or "{}")
        m["referDoc"] = json.loads(m.pop("refer_doc") or "[]")
        result.append(m)
    return ok(result)


@app.post("/api/chat/regenerate")
def chat_regenerate(req: RegenerateReq):
    with platform_db() as con:
        row = con.execute(
            "SELECT * FROM chat_message WHERE id=? AND role='assistant' AND active=1",
            (req.messageId,),
        ).fetchone()
        if not row:
            return fail("有效回答不存在", 404)
        old = dict(row)
        user = con.execute(
            "SELECT content FROM chat_message WHERE id=? AND role='user'",
            (old["reply_to"],),
        ).fetchone()
        if not user:
            return fail("对应问题不存在", 404)
    return process_chat(
        ChatSendReq(
            sessionId=old["session_id"],
            module=req.module,
            content=user["content"],
            dbConfigId=req.dbConfigId,
            taskId=req.taskId,
        ),
        old,
    )


@app.post("/api/chat/stop")
@app.post("/api/chat/cancel")
def chat_stop(req: TaskReq):
    return ok(TASKS.cancel(req.taskId))


@app.get("/api/chat/tasks/{task_id}")
def task_status(task_id: str):
    value = TASKS.status(task_id)
    return ok(value) if value else fail("任务不存在或已过期", 404)


@app.get("/api/chat/export/excel")
def chat_export(messageId: int):
    with platform_db() as con:
        row = con.execute(
            "SELECT extra_data FROM chat_message WHERE id=? AND role='assistant'",
            (messageId,),
        ).fetchone()
    if not row:
        return fail("消息不存在", 404)
    extra = json.loads(row[0] or "{}")
    if not extra.get("columns"):
        return fail("该消息没有可导出的查询结果")
    from openpyxl import Workbook

    wb = Workbook()
    sheet = wb.active

    def safe(value):
        return (
            "'" + value
            if isinstance(value, str)
            and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
            else value
        )

    sheet.append([safe(v) for v in extra["columns"]])
    for values in extra.get("data", []):
        sheet.append([safe(v) for v in values])
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="query_{messageId}.xlsx"'
        },
    )


@app.get("/api/chat/pdf/preview/{doc_id}")
def doc_preview(doc_id: int):
    doc = DOCUMENTS.get(doc_id)
    return (
        PlainTextResponse(f"{doc['name']} · 版本 {doc['version']}\n\n{doc['content']}")
        if doc
        else PlainTextResponse("文档版本不存在", status_code=404)
    )


class ChartConfig(BaseModel):
    chartType: Literal["bar", "line", "area", "pie", "radar", "scatter", "gauge"] = (
        "bar"
    )
    xAxisField: str = Field(default="", max_length=100)
    yAxisFields: list[str] = Field(default_factory=list, max_length=10)
    seriesField: str | None = None


class Card(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    title: str = Field(min_length=1, max_length=120)
    sql: str = Field(min_length=1, max_length=20000)
    dbConfigId: Literal[1] = 1
    displayMode: Literal["chart", "table", "number"] = "chart"
    chartConfig: ChartConfig = Field(default_factory=ChartConfig)
    x: int = Field(ge=0, le=11)
    y: int = Field(ge=0, le=1000)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=1, le=30)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.w > 12:
            raise ValueError("卡片超出 12 列边界")
        return self


class DashboardConfig(BaseModel):
    cards: list[Card] = Field(default_factory=list, max_length=50)
    schemaVersion: Literal[1] = 1

    @model_validator(mode="after")
    def unique_layout(self):
        if len({c.id for c in self.cards}) != len(self.cards):
            raise ValueError("卡片 ID 重复")
        for i, a in enumerate(self.cards):
            for b in self.cards[i + 1 :]:
                if (
                    a.x < b.x + b.w
                    and b.x < a.x + a.w
                    and a.y < b.y + b.h
                    and b.y < a.y + a.h
                ):
                    raise ValueError("卡片位置重叠，请调整布局")
        return self


class DashSaveReq(BaseModel):
    id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    refreshMode: Literal["off", "interval"] = "interval"
    config: DashboardConfig = Field(default_factory=DashboardConfig)


class ExecSqlReq(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    dbConfigId: Literal[1] = 1


@app.get("/api/dashboard/list")
def dash_list():
    with platform_db() as con:
        rows = con.execute(
            "SELECT id,name,description,refresh_mode,updated_at FROM dashboard ORDER BY id"
        ).fetchall()
    return ok([dict(r) for r in rows])


@app.get("/api/dashboard/detail/{dash_id}")
def dash_detail(dash_id: int):
    with platform_db() as con:
        row = con.execute("SELECT * FROM dashboard WHERE id=?", (dash_id,)).fetchone()
    if not row:
        return fail("大屏不存在", 404)
    d = dict(row)
    d["config"] = json.loads(d["config"] or "{}")
    return ok(d)


@app.post("/api/dashboard/save")
def dash_save(req: DashSaveReq):
    with platform_db() as con:
        values = (
            req.name,
            req.description,
            req.refreshMode,
            json.dumps(req.config.model_dump(), ensure_ascii=False),
            now(),
        )
        if req.id:
            cur = con.execute(
                "UPDATE dashboard SET name=?,description=?,refresh_mode=?,config=?,updated_at=? WHERE id=?",
                values + (req.id,),
            )
            if not cur.rowcount:
                return fail("大屏不存在", 404)
            did = req.id
        else:
            did = con.execute(
                "INSERT INTO dashboard(name,description,refresh_mode,config,updated_at) VALUES (?,?,?,?,?)",
                values,
            ).lastrowid
    return ok({"id": did})


@app.post("/api/dashboard/delete/{dash_id}")
def dash_delete(dash_id: int):
    with platform_db() as con:
        cur = con.execute("DELETE FROM dashboard WHERE id=?", (dash_id,))
        if not cur.rowcount:
            return fail("大屏不存在", 404)
    return ok({"id": dash_id})


@app.post("/api/dashboard/executeSql")
def dash_execute(req: ExecSqlReq):
    try:
        return ok(execute_sql(req.sql))
    except (ValueError, sqlite3.Error, TimeoutError) as exc:
        return fail(str(exc), 408 if isinstance(exc, TimeoutError) else 400)


@app.get("/api/admin/ds/list")
def ds_list():
    with platform_db() as con:
        rows = con.execute(
            "SELECT id,name,db_type,host,port,db_name,username,status,tested FROM ds_config ORDER BY id"
        ).fetchall()
    builtin = {
        "id": 1,
        "name": "医药供应链示例库",
        "db_type": "SQLite",
        "host": "local",
        "port": "0",
        "db_name": "biz.db",
        "username": "只读",
        "status": "可用",
        "tested": 1,
        "available": True,
    }
    return ok(
        [builtin]
        + [
            {**dict(r), "available": False, "status": "占位，未接入", "tested": 0}
            for r in rows
            if r["id"] != 1
        ]
    )


@app.post("/api/admin/ds/test")
def ds_test(payload: dict):
    if payload.get("id") != 1:
        return ok({"ok": False, "msg": "仅内置 SQLite 数据源可执行；外部数据源未接入"})
    execute_sql("SELECT COUNT(*) FROM fact_sales")
    return ok({"ok": True, "msg": "内置 SQLite 业务库可用"})


@app.post("/api/admin/ds/save")
def ds_save():
    return fail("离线版使用固定数据源，外部数据源配置暂未开放")


@app.get("/api/admin/model/list")
def model_list():
    return ok(
        [
            {
                "id": 1,
                "name": "当前问数引擎",
                "kind": "SQL",
                "endpoint": "本地",
                "model_name": "规则规划器",
                "status": "运行中",
            },
            {
                "id": 2,
                "name": "当前知识库引擎",
                "kind": "QA",
                "endpoint": "本地",
                "model_name": "词面检索 + 原文抽取",
                "status": "运行中",
            },
            {
                "id": 3,
                "name": "LLM / Embedding / Rerank",
                "kind": "可选扩展",
                "endpoint": "未接入",
                "model_name": "未启用",
                "status": "未接入",
            },
        ]
    )


@app.post("/api/admin/model/test")
def model_test():
    return ok({"ok": False, "msg": "离线版未接入外部模型，不能执行模型连接测试"})


@app.post("/api/admin/model/save")
def model_save():
    return fail("离线版未开放外部模型配置")


@app.get("/api/admin/doc/list")
def doc_list():
    return ok(DOCUMENTS.list())


@app.post("/api/admin/doc/upload")
def doc_upload(file: UploadFile = File(...)):
    try:
        raw_name = re.search(
            r'filename\s*=\s*"([^"]*)"',
            file.headers.get("content-disposition", ""),
            re.I,
        )
        if raw_name and re.search(r"[\\/:]", raw_name[1]):
            return fail("文件名不得包含路径")
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            return fail("文件超过 2 MiB", 413)
        with ENGINE.lock:
            rid = DOCUMENTS.save(file.filename, content)
            try:
                ENGINE.build()
            except Exception:
                DOCUMENTS.failed(rid)
                raise
            doc = DOCUMENTS.get(rid)
        return ok(
            {
                "id": rid,
                "version": doc["version"],
                "chunks": doc["chunks"],
                "status": doc["status"],
            }
        )
    finally:
        file.file.close()


@app.get("/api/admin/stats/overview")
def stats():
    with platform_db() as con:
        total = con.execute("SELECT COUNT(*) FROM ask_log").fetchone()[0]
        grouped = con.execute(
            "SELECT module,COUNT(*) c,ROUND(AVG(elapsed_ms)) avg_ms, SUM(CASE WHEN status IN ('executed','empty','evidence_found','成功') THEN 1 ELSE 0 END) ok FROM ask_log GROUP BY module"
        ).fetchall()
        recent = con.execute(
            "SELECT ts,module,question,sql_text,elapsed_ms,status FROM ask_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
    return ok(
        {
            "total": total,
            "byModule": [dict(r) for r in grouped],
            "recent": [dict(r) for r in recent],
            "definition": "SQL 成功仅表示执行完成（含空结果），不代表结果语义正确；QA 无证据单独记录",
        }
    )


@app.get("/api/admin/architecture")
@app.get("/api/health")
def architecture():
    with_conn = biz_conn()
    try:
        coverage = {
            table: dict(
                with_conn.execute(
                    f"SELECT MIN(date_id) start,MAX(date_id) end,COUNT(*) rows FROM {table}"
                ).fetchone()
            )
            for table in ("fact_sales", "fact_purchase", "fact_inventory")
        }
    finally:
        with_conn.close()
    return ok(
        {
            "mode": "offline",
            "asOfDate": AS_OF_DATE.isoformat(),
            "dataCoverage": coverage,
            "chunkUnit": "字符",
            "chunkSize": 128,
            "retriever": "字符特征 + 词面匹配",
            "rerankEnabled": False,
            "llmEnabled": False,
            "sqlProvider": "规则规划器",
            "answerProvider": "原文抽取",
            "taskScope": "单进程，终态保留 10 分钟",
        }
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
