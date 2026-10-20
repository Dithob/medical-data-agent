"""Deterministic offline query planner. Unsupported semantics request clarification."""

import re
import sqlite3
from calendar import monthrange
from datetime import date, timedelta
from typing import TypedDict, Any
from .config import AS_OF_DATE
from .db import biz_conn, DRUG_PREFIX
from .executor import execute_sql, is_readonly
from .metrics import SALES_METRICS, INVENTORY_SNAPSHOT
from .schema_store import STORE


class QueryIntent(TypedDict):
    fact: str
    metric_sql: str
    metric_label: str
    dims: list
    filters: list
    params: list
    time: tuple | None
    topn: int | None
    asc: bool
    detail: bool
    trend: bool
    pie: bool
    raw: str


DIM_RULES = [
    (("品类", "类别", "分类"), "d.category", "品类", "dim_drug"),
    (("区域", "大区"), "s.region", "区域", "dim_store"),
    (("城市",), "s.city", "城市", "dim_store"),
    (("门店类型", "直营", "加盟"), "s.store_type", "门店类型", "dim_store"),
    (("门店", "店铺"), "s.store_name", "门店", "dim_store"),
    (("药品", "商品", "品种", "品名"), "d.drug_name", "药品", "dim_drug"),
    (("厂家", "厂商", "生产企业"), "d.manufacturer", "生产企业", "dim_drug"),
    (("规格",), "d.spec", "规格", "dim_drug"),
    (("供应商",), "sup.supplier_name", "供应商", "dim_supplier"),
]
TIME_WORDS = ("月份", "逐月", "按月", "每月", "各月", "趋势", "走势", "月度")
CITIES = (
    "上海",
    "杭州",
    "南京",
    "苏州",
    "合肥",
    "广州",
    "深圳",
    "东莞",
    "厦门",
    "北京",
    "天津",
    "石家庄",
    "青岛",
    "成都",
    "重庆",
    "昆明",
    "武汉",
    "长沙",
    "郑州",
)
REGIONS = ("华东", "华南", "华北", "西南", "华中")
CATEGORIES = ("化学药", "中成药", "生物制品", "医疗器械", "保健品")


def parse_time(question, today=None):
    today = today or AS_OF_DATE
    q = re.sub(r"\s+", "", question)
    ym = re.search(r"(20\d{2})年", q)
    year = int(ym[1]) if ym else today.year
    if "去年" in q or "上年" in q:
        year = today.year - 1
    m = re.search(r"(?:20\d{2}年|去年|今年)?(\d{1,2})月(?!份)", q)
    # 月度 quantities such as 12个月 must not be interpreted as explicit months.
    if m:
        month = int(m[1])
        if not 1 <= month <= 12:
            raise ValueError("月份必须在 1～12 之间")
        return (
            f"{year}-{month:02d}-01",
            f"{year}-{month:02d}-{monthrange(year, month)[1]}",
        )
    qm = re.search(r"(?:第)?([一二三四1-4])季度|Q([1-4])", q, re.I)
    if qm:
        raw = qm[1] or qm[2]
        quarter = "一二三四".index(raw) + 1 if raw in "一二三四" else int(raw)
        first, last = (quarter - 1) * 3 + 1, quarter * 3
        return (
            f"{year}-{first:02d}-01",
            f"{year}-{last:02d}-{monthrange(year, last)[1]}",
        )
    if "季度" in q:
        raise ValueError("请指定第一至第四季度")
    if "上半年" in q:
        return f"{year}-01-01", f"{year}-06-30"
    if "下半年" in q:
        return f"{year}-07-01", f"{year}-12-31"
    m = re.search(r"(?:最近|近)(\d+)个月", q)
    if m:
        n = int(m[1])
        if not 1 <= n <= 120:
            raise ValueError("最近月份范围需在 1～120 之间")
        end = today.replace(day=1) - timedelta(days=1)
        index = end.year * 12 + end.month - 1 - (n - 1)
        y, mo = divmod(index, 12)
        return f"{y}-{mo + 1:02d}-01", end.isoformat()
    if "上个月" in q or "上月" in q:
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1).isoformat(), end.isoformat()
    if any(w in q for w in ("本月", "这个月", "当月")):
        return today.replace(day=1).isoformat(), today.isoformat()
    if ym or any(w in q for w in ("去年", "上年", "今年", "本年")):
        return f"{year}-01-01", (
            today.isoformat() if not ym and year == today.year else f"{year}-12-31"
        )
    return None


def parse_intent(question) -> QueryIntent:
    q = question.strip()
    if not any(
        w in q
        for w in (
            "销售",
            "销量",
            "卖了",
            "采购",
            "库存",
            "效期",
            "毛利",
            "利润",
            "成本",
            "单价",
        )
    ):
        raise ValueError(
            "当前支持销售、采购和库存查询，请指定指标，例如“2025年各品类销售额”"
        )
    if any(
        w in q for w in ("同比", "环比", "周转", "预测", "客单价", "客流", "客户数")
    ):
        raise ValueError(
            "当前数据或离线规则不足以计算该指标；可查询销售额、销量、平均销售单价、采购额或库存金额"
        )
    if sum(w in q for w in ("销售额", "销量", "毛利率", "采购额", "库存金额")) > 1:
        raise ValueError("当前请一次查询一个指标，多指标分析需分别提问")
    if "采购" in q and any(w in q for w in ("销售", "销量", "库存")):
        raise ValueError("请分别查询采购、销售和库存，当前不支持跨事实表联合指标")
    fact = (
        "fact_purchase"
        if "采购" in q
        else "fact_inventory"
        if any(w in q for w in ("库存", "效期"))
        else "fact_sales"
    )
    if fact == "fact_purchase":
        label, metric = (
            ("采购量", "SUM(f.quantity)")
            if any(w in q for w in ("采购量", "采购数量"))
            else ("采购额", "ROUND(SUM(f.amount),2)")
        )
    elif fact == "fact_inventory":
        if "批次" in q:
            raise ValueError("示例库没有真实批次标识；可查询近效期库存记录数或库存数量")
        label, metric = (
            ("近效期库存记录数", "COUNT(*)")
            if "近效期" in q
            else ("已过期库存记录数", "COUNT(*)")
            if "过期" in q
            else ("库存量", "SUM(f.stock_qty)")
            if any(w in q for w in ("库存量", "库存数量"))
            else ("库存金额", "ROUND(SUM(f.stock_amount),2)")
        )
    else:
        label = (
            "毛利率(%)"
            if "毛利率" in q
            else "毛利"
            if any(w in q for w in ("毛利", "利润"))
            else "成本"
            if "成本" in q
            else "平均销售单价"
            if "单价" in q
            else "销量"
            if any(w in q for w in ("销量", "销售数量", "销售量", "卖了多少"))
            else "销售额"
        )
        metric = SALES_METRICS[label]
    dims = []
    for keys, expr, name, table in DIM_RULES:
        if not any(k in q for k in keys):
            continue
        if fact == "fact_purchase" and table == "dim_store":
            if name == "城市":
                expr, name, table = "sup.city", "供应商城市", "dim_supplier"
            else:
                raise ValueError(
                    "采购记录没有门店或区域字段；可按供应商及供应商城市查询"
                )
        if table == "dim_supplier" and fact != "fact_purchase":
            raise ValueError("销售/库存记录没有供应商字段")
        if name == "门店" and any(d[1] == "门店类型" for d in dims):
            continue
        dims.append((expr, name, table))
    trend = any(w in q for w in TIME_WORDS)
    if trend:
        dims.insert(0, ("substr(f.date_id,1,7)", "月份", ""))
    if len(dims) > 2:
        raise ValueError("当前最多支持两个分组维度，请简化问题")
    filters, params = [], []

    def add(expr, values):
        matches = [v for v in values if v in q]
        if matches:
            filters.append(expr + " IN (" + ",".join("?" for _ in matches) + ")")
            params.extend(matches)

    add("sup.city" if fact == "fact_purchase" else "s.city", CITIES)
    add("s.region", REGIONS)
    if fact == "fact_purchase" and any(r in q for r in REGIONS):
        raise ValueError("采购记录没有区域字段，请指定供应商城市")
    add("d.category", CATEGORIES)
    if any(w in q for w in ("直营", "加盟")):
        add("s.store_type", ("直营", "加盟"))
    drugs = sorted({d for d in DRUG_PREFIX if d in q}, key=len, reverse=True)
    if drugs:
        filters.append("(" + " OR ".join("d.drug_name LIKE ?" for _ in drugs) + ")")
        params.extend(d + "%" for d in drugs)
    if "处方药" in q:
        filters.append("d.is_prescription = ?")
        params.append(0 if "非处方药" in q else 1)
    m = re.search(
        r"(?:前|top)\s*(\d+)|(?:最高|最多|最低|最少)的?\s*(\d+)\s*(?:个|名)|(?:倒数)\s*(\d+)",
        q,
        re.I,
    )
    topn = (
        int(next(g for g in m.groups() if g))
        if m
        else 10
        if any(w in q for w in ("排名", "排行"))
        else None
    )
    if topn is not None and not 1 <= topn <= 500:
        raise ValueError("排名数量需在 1～500 之间")
    detail = any(w in q for w in ("明细", "清单", "列表", "列出", "哪些", "详细"))
    return QueryIntent(
        fact=fact,
        metric_sql=metric,
        metric_label=label,
        dims=dims,
        filters=filters,
        params=params,
        time=parse_time(q),
        topn=topn,
        asc=any(w in q for w in ("最低", "最少", "倒数")),
        detail=detail,
        trend=trend,
        pie=any(w in q for w in ("占比", "构成", "结构", "比例")),
        raw=q,
    )


def build_query(intent, inject_error=False):
    fact = intent["fact"]
    metric = intent["metric_sql"]
    if inject_error:
        metric = (
            metric.replace("f.amount", "f.sale_amount")
            .replace("f.quantity", "f.sale_qty")
            .replace("f.stock_amount", "f.stock_amt")
        )
    if intent["detail"]:
        cols = ['f.date_id AS "日期"', 'd.drug_name AS "药品"']
        cols.append(
            'sup.supplier_name AS "供应商"'
            if fact == "fact_purchase"
            else 's.store_name AS "门店"'
        )
        cols += (
            [
                'f.stock_qty AS "库存量"',
                'f.stock_amount AS "库存金额"',
                'f.expiry_date AS "效期"',
            ]
            if fact == "fact_inventory"
            else [
                f'f.quantity AS "{"采购量" if fact == "fact_purchase" else "销量"}"',
                f'f.amount AS "{"采购额" if fact == "fact_purchase" else "销售额"}"',
            ]
        )
    else:
        cols = [f'{expr} AS "{name}"' for expr, name, _ in intent["dims"]] + [
            f'{metric} AS "{intent["metric_label"]}"'
        ]
    sql = (
        "SELECT "
        + ", ".join(cols)
        + "\nFROM "
        + fact
        + " f\nJOIN dim_drug d ON f.drug_id=d.drug_id\n"
    )
    sql += (
        "JOIN dim_supplier sup ON f.supplier_id=sup.supplier_id\n"
        if fact == "fact_purchase"
        else "JOIN dim_store s ON f.store_id=s.store_id\n"
    )
    where = list(intent["filters"])
    params = list(intent["params"])
    if intent["time"]:
        where.append("f.date_id BETWEEN ? AND ?")
        params.extend(intent["time"])
    if fact == "fact_inventory" and not intent["trend"]:
        cutoff = (
            min(intent["time"][1], AS_OF_DATE.isoformat())
            if intent["time"]
            else AS_OF_DATE.isoformat()
        )
        where.append(INVENTORY_SNAPSHOT)
        params.append(cutoff)
    if fact == "fact_inventory" and "近效期" in intent["raw"]:
        where.append("f.expiry_date BETWEEN ? AND ?")
        params.extend(
            [AS_OF_DATE.isoformat(), (AS_OF_DATE + timedelta(days=180)).isoformat()]
        )
    elif fact == "fact_inventory" and "过期" in intent["raw"]:
        where.append("f.expiry_date < ?")
        params.append(AS_OF_DATE.isoformat())
    if where:
        sql += "WHERE " + " AND ".join(where) + "\n"
    if not intent["detail"] and intent["dims"]:
        sql += "GROUP BY " + ", ".join(x[0] for x in intent["dims"]) + "\n"
    if intent["detail"]:
        sql += (
            "ORDER BY f.date_id DESC, f."
            + {
                "fact_sales": "sale_id",
                "fact_purchase": "purchase_id",
                "fact_inventory": "inv_id",
            }[fact]
        )
    elif intent["trend"]:
        sql += "ORDER BY " + ", ".join(x[0] for x in intent["dims"])
    else:
        sql += f'ORDER BY "{intent["metric_label"]}" ' + (
            "ASC" if intent["asc"] else "DESC"
        )
    if intent["topn"]:
        sql += f"\nLIMIT {intent['topn']}"
    return sql, params


def display_sql(sql, params):
    for value in params:
        literal = (
            str(value)
            if isinstance(value, (int, float))
            else "'" + str(value).replace("'", "''") + "'"
        )
        sql = sql.replace("?", literal, 1)
    return sql


def build_sql(intent, inject_error=False):
    sql, params = build_query(intent, inject_error)
    return display_sql(sql, params)


def self_correct(sql, error):
    m = re.search(r"no such column:\s*f\.(\w+)", error)
    if not m:
        return None
    mapping = {
        "sale_amount": "amount",
        "sale_qty": "quantity",
        "stock_amt": "stock_amount",
    }
    good = mapping.get(m[1])
    if not good or ("fact_inventory" in sql and good != "stock_amount"):
        return None
    return re.sub(r"\bf\." + re.escape(m[1]) + r"\b", "f." + good, sql)


def recommend_chart(intent, columns, rows):
    dims = intent["dims"]
    kind = (
        "table"
        if intent["detail"] or len(rows) > 100
        else "number"
        if not dims
        else "line"
        if intent["trend"]
        else "pie"
        if (intent["pie"] or len(rows) <= 8) and len(dims) == 1
        else "bar"
    )
    return {
        "chartType": kind,
        "xAxisField": dims[0][1] if dims else "",
        "yAxisFields": [intent["metric_label"]]
        if intent["metric_label"] in columns
        else [],
        "seriesField": dims[1][1] if len(dims) > 1 else None,
        "reason": "按查询维度与指标展示",
    }


def run_question(question, db_config_id=1, force_error=False, cancel=None):
    if db_config_id != 1:
        raise ValueError("仅支持内置 SQLite 数据源（ID 1）")
    intent = parse_intent(question)
    schema = STORE.retrieve(question)
    trace = [
        {
            "step": "问题理解",
            "detail": {
                "指标": intent["metric_label"],
                "事实表": intent["fact"],
                "维度": [d[1] for d in intent["dims"]],
                "时间范围": intent["time"],
                "演示参考日": AS_OF_DATE.isoformat(),
            },
        },
        {
            "step": "元数据召回展示",
            "detail": {
                "说明": "离线 SQL 由规则生成；此召回仅用于展示",
                "tables": schema["tables"],
            },
        },
    ]
    sql, params = build_query(intent, force_error)
    trace.append({"step": "SQL 生成", "detail": {"sql": display_sql(sql, params)}})
    result = {
        "columns": [],
        "data": [],
        "returnedRows": 0,
        "hasMore": False,
        "truncated": False,
    }
    error = ""
    for attempt in range(2):
        try:
            result = execute_sql(sql, params, cancel=cancel)
            break
        except sqlite3.Error as exc:
            error = str(exc)
            fixed = self_correct(sql, error)
            if fixed and attempt == 0:
                trace.append(
                    {
                        "step": "自纠错重写",
                        "detail": {
                            "error": error,
                            "from": display_sql(sql, params),
                            "to": display_sql(fixed, params),
                        },
                    }
                )
                sql = fixed
                error = ""
                continue
            break
    rows, columns = result["data"], result["columns"]
    if rows and all(all(v is None for v in row) for row in rows):
        rows = []
        result.update(data=[], returnedRows=0)
    with_conn = biz_conn()
    try:
        coverage = dict(
            with_conn.execute(
                f"SELECT MIN(date_id) start,MAX(date_id) end FROM {intent['fact']}"
            ).fetchone()
        )
    finally:
        with_conn.close()
    suggestion = recommend_chart(intent, columns, rows) if not error else {}
    if error:
        content = "查询未完成：" + error
    elif not rows:
        content = "所选条件下没有可用数据。请核对时间和筛选条件。"
    elif len(rows) == 1 and len(columns) == 1:
        content = f"查询完成：{columns[0]}为 {rows[0][0]}。"
    else:
        content = f"查询完成，返回 {len(rows)} 行结果。"
    content += f" 示例数据范围：{coverage['start']}～{coverage['end']}；演示参考日：{AS_OF_DATE}。"
    if intent["fact"] == "fact_inventory" and not intent["trend"]:
        content += " 库存按所选截止日之前的最新可用快照统计。"
    if result["truncated"]:
        content += " 结果超过 500 行，当前仅展示和导出前 500 行，请缩小范围。"
    trace.append(
        {
            "step": "SQL 执行",
            "detail": {
                "rows": len(rows),
                "error": error,
                "truncated": result["truncated"],
            },
        }
    )
    return {
        **result,
        "sql": display_sql(sql, params),
        "suggestion": suggestion,
        "charts": [suggestion] if suggestion else [],
        "error": error,
        "trace": trace,
        "content": content,
        "asOfDate": AS_OF_DATE.isoformat(),
        "dataCoverage": coverage,
        "status": "failed" if error else "empty" if not rows else "executed",
        "provider": "offline-rules",
    }
