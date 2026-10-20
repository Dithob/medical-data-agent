"""
业务库 + 平台配置库：SQLite 初始化与示例数据生成。

说明：demo 用 SQLite 单文件模拟「业务数据库」，配置/会话/大屏/日志存另一个文件。
示例业务域为医药供应链（与简历对外口径一致），数据全部为脱敏示例数据。
"""

from __future__ import annotations

import os
import random
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import date, timedelta

from .config import BIZ_DB, PLATFORM_DB, DATA_DIR, UPLOAD_DIR
from .metrics import NEAR_EXPIRY_SQL

# ---------------------------------------------------------------- 业务库


def _biz_schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS dim_drug (
        drug_id        INTEGER PRIMARY KEY,
        drug_name      TEXT,
        category       TEXT,
        spec           TEXT,
        manufacturer   TEXT,
        unit           TEXT,
        is_prescription INTEGER
    );
    CREATE TABLE IF NOT EXISTS dim_store (
        store_id   INTEGER PRIMARY KEY,
        store_name TEXT,
        city       TEXT,
        region     TEXT,
        store_type TEXT
    );
    CREATE TABLE IF NOT EXISTS dim_supplier (
        supplier_id   INTEGER PRIMARY KEY,
        supplier_name TEXT,
        city          TEXT,
        level         TEXT
    );
    CREATE TABLE IF NOT EXISTS fact_sales (
        sale_id     INTEGER PRIMARY KEY,
        date_id     TEXT,
        drug_id     INTEGER,
        store_id    INTEGER,
        quantity    INTEGER,
        amount      REAL,
        cost_amount REAL
    );
    CREATE TABLE IF NOT EXISTS fact_purchase (
        purchase_id INTEGER PRIMARY KEY,
        date_id     TEXT,
        drug_id     INTEGER,
        supplier_id INTEGER,
        quantity    INTEGER,
        amount      REAL
    );
    CREATE TABLE IF NOT EXISTS fact_inventory (
        inv_id       INTEGER PRIMARY KEY,
        date_id      TEXT,
        drug_id      INTEGER,
        store_id     INTEGER,
        stock_qty    INTEGER,
        stock_amount REAL,
        expiry_date  TEXT
    );
    """


CATEGORIES = ["化学药", "中成药", "生物制品", "医疗器械", "保健品"]
REGIONS = ["华东", "华南", "华北", "西南", "华中"]
CITY_BY_REGION = {
    "华东": ["上海", "杭州", "南京", "苏州", "合肥"],
    "华南": ["广州", "深圳", "东莞", "厦门"],
    "华北": ["北京", "天津", "石家庄", "青岛"],
    "西南": ["成都", "重庆", "昆明"],
    "华中": ["武汉", "长沙", "郑州"],
}
SPECS = ["10mg*24片", "0.25g*12粒", "5ml*10支", "100ml/瓶", "1支/盒", "20g/管"]
DRUG_PREFIX = [
    "阿莫西林",
    "布洛芬",
    "氨氯地平",
    "二甲双胍",
    "头孢克洛",
    "奥美拉唑",
    "蒙脱石散",
    "连花清瘟",
    "板蓝根",
    "藿香正气",
    "维生素C",
    "葡萄糖",
    "胰岛素",
    "人血白蛋白",
    "狂犬疫苗",
    "血压计",
    "体温计",
    "口罩",
    "钙片",
    "益生菌",
    "氯雷他定",
    "左氧氟沙星",
    "复方甘草",
    "丹参",
]


def _seed_biz(conn: sqlite3.Connection) -> None:
    rnd = random.Random(42)
    if conn.execute("SELECT COUNT(*) FROM dim_drug").fetchone()[0] > 0:
        return

    drugs = []
    for i in range(1, 61):
        base = DRUG_PREFIX[(i - 1) % len(DRUG_PREFIX)]
        drugs.append(
            (
                i,
                f"{base}{'颗粒' if i % 3 == 0 else ('胶囊' if i % 3 == 1 else '片')}",
                CATEGORIES[(i - 1) % len(CATEGORIES)],
                SPECS[(i - 1) % len(SPECS)],
                f"制药厂{(i - 1) % 12 + 1}",
                ["盒", "瓶", "支", "袋"][(i - 1) % 4],
                1 if i % 4 == 0 else 0,
            )
        )
    conn.executemany("INSERT INTO dim_drug VALUES (?,?,?,?,?,?,?)", drugs)

    stores = []
    sid = 1
    for region, cities in CITY_BY_REGION.items():
        for city in cities:
            stores.append(
                (
                    sid,
                    f"{city}{'旗舰店' if sid % 4 == 0 else f'{sid}号店'}",
                    city,
                    region,
                    "直营" if sid % 3 else "加盟",
                )
            )
            sid += 1
    conn.executemany("INSERT INTO dim_store VALUES (?,?,?,?,?)", stores)

    suppliers = [
        (
            i,
            f"医药供应商{chr(64 + i)}",
            CITY_BY_REGION[REGIONS[i % 5]][0],
            ["A", "B", "C"][i % 3],
        )
        for i in range(1, 13)
    ]
    conn.executemany("INSERT INTO dim_supplier VALUES (?,?,?,?)", suppliers)

    # 固定数据范围；相对日期由 config.AS_OF_DATE 决定，页面明确展示该范围。
    start = date(2025, 1, 1)
    days = [start + timedelta(days=i) for i in range(608)]

    sales, pid = [], 1
    for d in days:
        n = rnd.randint(20, 45)
        for _ in range(n):
            drug = rnd.randint(1, 60)
            store = rnd.randint(1, len(stores))
            qty = rnd.randint(1, 60)
            price = round(rnd.uniform(8, 260), 2)
            amount = round(qty * price, 2)
            sales.append(
                (
                    pid,
                    d.isoformat(),
                    drug,
                    store,
                    qty,
                    amount,
                    round(amount * rnd.uniform(0.55, 0.82), 2),
                )
            )
            pid += 1
    conn.executemany("INSERT INTO fact_sales VALUES (?,?,?,?,?,?,?)", sales)

    purchases, qid = [], 1
    for d in days[::3]:
        for _ in range(rnd.randint(5, 12)):
            qty = rnd.randint(50, 800)
            purchases.append(
                (
                    qid,
                    d.isoformat(),
                    rnd.randint(1, 60),
                    rnd.randint(1, 12),
                    qty,
                    round(qty * rnd.uniform(3, 90), 2),
                )
            )
            qid += 1
    conn.executemany("INSERT INTO fact_purchase VALUES (?,?,?,?,?,?)", purchases)

    inv, iid = [], 1
    for idx in range(20):  # 2025-01 ~ 2026-08 每月末快照
        month = idx % 12 + 1
        year = 2025 + idx // 12
        d = date(year, month, 28)
        for drug in range(1, 61):
            for store in rnd.sample(range(1, len(stores) + 1), 8):
                qty = rnd.randint(20, 1500)
                inv.append(
                    (
                        iid,
                        d.isoformat(),
                        drug,
                        store,
                        qty,
                        round(qty * rnd.uniform(5, 180), 2),
                        (d + timedelta(days=rnd.randint(60, 720))).isoformat(),
                    )
                )
                iid += 1
    conn.executemany("INSERT INTO fact_inventory VALUES (?,?,?,?,?,?,?)", inv)
    conn.commit()


# ---------------------------------------------------------------- 平台库


def _platform_schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS ds_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, db_type TEXT,
        host TEXT, port TEXT, db_name TEXT, username TEXT, password TEXT,
        status TEXT, tested INTEGER DEFAULT 0, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS model_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, kind TEXT,
        endpoint TEXT, model_name TEXT, api_key TEXT, timeout INTEGER,
        status TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS rag_doc (
        id INTEGER PRIMARY KEY AUTOINCREMENT, doc_name TEXT, file_type TEXT,
        size INTEGER, status TEXT, chunks INTEGER, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS chat_session (
        id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT, title TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS chat_message (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, role TEXT,
        content TEXT, extra_data TEXT, refer_doc TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ask_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, user_name TEXT, module TEXT,
        question TEXT, sql_text TEXT, elapsed_ms INTEGER, status TEXT
    );
    CREATE TABLE IF NOT EXISTS dashboard (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT,
        refresh_mode TEXT, config TEXT, updated_at TEXT
    );
    """


def _seed_platform(conn: sqlite3.Connection) -> None:
    from datetime import datetime

    now = datetime.now().isoformat(timespec="seconds")
    if conn.execute("SELECT COUNT(*) FROM ds_config").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO ds_config (name,db_type,host,port,db_name,username,password,status,tested,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "医药供应链主库",
                "SQLite(示例)",
                "local",
                "0",
                "biz.db",
                "-",
                "-",
                "可用",
                1,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO ds_config (name,db_type,host,port,db_name,username,password,status,tested,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "门店销售库",
                "MySQL",
                "10.0.0.21",
                "3306",
                "store_sales",
                "readonly",
                "***",
                "未测试",
                0,
                now,
            ),
        )
    if conn.execute("SELECT COUNT(*) FROM model_config").fetchone()[0] == 0:
        for name, kind, endpoint, mname in [
            ("本地推理模型", "推理", "http://127.0.0.1:8000/v1", "Qwen3-32B"),
            ("本地向量模型", "Embedding", "http://127.0.0.1:9997/v1", "BGE-M3"),
            (
                "重排序模型(未启用)",
                "Rerank",
                "http://127.0.0.1:9998/v1",
                "bge-reranker-v2",
            ),
        ]:
            conn.execute(
                "INSERT INTO model_config (name,kind,endpoint,model_name,api_key,timeout,status,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    name,
                    kind,
                    endpoint,
                    mname,
                    "***",
                    60,
                    "已测试" if "未启用" not in name else "未测试",
                    now,
                ),
            )
    conn.commit()


def _seed_dashboard(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM dashboard").fetchone()[0] > 0:
        return
    import json
    from datetime import datetime

    now = datetime.now().isoformat(timespec="seconds")
    demo_cards = [
        {
            "id": "c1",
            "title": "总销售额(元)",
            "sql": "SELECT SUM(amount) AS total_amount FROM fact_sales",
            "dbConfigId": 1,
            "displayMode": "number",
            "chartConfig": {
                "chartType": "bar",
                "xAxisField": "",
                "yAxisFields": ["total_amount"],
            },
            "x": 0,
            "y": 0,
            "w": 3,
            "h": 3,
        },
        {
            "id": "c2",
            "title": "总销量",
            "sql": "SELECT SUM(quantity) AS total_qty FROM fact_sales",
            "dbConfigId": 1,
            "displayMode": "number",
            "chartConfig": {
                "chartType": "bar",
                "xAxisField": "",
                "yAxisFields": ["total_qty"],
            },
            "x": 3,
            "y": 0,
            "w": 3,
            "h": 3,
        },
        {
            "id": "c3",
            "title": "毛利率(%)",
            "sql": "SELECT ROUND((1 - SUM(cost_amount) / SUM(amount)) * 100, 2) AS gross_margin FROM fact_sales",
            "dbConfigId": 1,
            "displayMode": "number",
            "chartConfig": {
                "chartType": "gauge",
                "xAxisField": "",
                "yAxisFields": ["gross_margin"],
            },
            "x": 6,
            "y": 0,
            "w": 3,
            "h": 3,
        },
        {
            "id": "c4",
            "title": "近效期库存记录数（最新快照）",
            "sql": NEAR_EXPIRY_SQL,
            "dbConfigId": 1,
            "displayMode": "number",
            "chartConfig": {
                "chartType": "bar",
                "xAxisField": "",
                "yAxisFields": ["near_expiry"],
            },
            "x": 9,
            "y": 0,
            "w": 3,
            "h": 3,
        },
        {
            "id": "c5",
            "title": "各月销售额趋势",
            "sql": "SELECT substr(date_id,1,7) AS month, ROUND(SUM(amount),2) AS amount FROM fact_sales GROUP BY month ORDER BY month",
            "dbConfigId": 1,
            "displayMode": "chart",
            "chartConfig": {
                "chartType": "line",
                "xAxisField": "month",
                "yAxisFields": ["amount"],
            },
            "x": 0,
            "y": 3,
            "w": 8,
            "h": 6,
        },
        {
            "id": "c6",
            "title": "各品类销售额占比",
            "sql": "SELECT d.category AS category, ROUND(SUM(f.amount),2) AS amount FROM fact_sales f JOIN dim_drug d ON f.drug_id=d.drug_id GROUP BY d.category",
            "dbConfigId": 1,
            "displayMode": "chart",
            "chartConfig": {
                "chartType": "pie",
                "xAxisField": "category",
                "yAxisFields": ["amount"],
            },
            "x": 8,
            "y": 3,
            "w": 4,
            "h": 6,
        },
        {
            "id": "c7",
            "title": "销量 Top10 药品",
            "sql": "SELECT d.drug_name AS drug_name, SUM(f.quantity) AS qty FROM fact_sales f JOIN dim_drug d ON f.drug_id=d.drug_id GROUP BY d.drug_name ORDER BY qty DESC LIMIT 10",
            "dbConfigId": 1,
            "displayMode": "chart",
            "chartConfig": {
                "chartType": "bar",
                "xAxisField": "drug_name",
                "yAxisFields": ["qty"],
            },
            "x": 0,
            "y": 9,
            "w": 7,
            "h": 6,
        },
        {
            "id": "c8",
            "title": "各区域销售额",
            "sql": "SELECT s.region AS region, ROUND(SUM(f.amount),2) AS amount FROM fact_sales f JOIN dim_store s ON f.store_id=s.store_id GROUP BY s.region ORDER BY amount DESC",
            "dbConfigId": 1,
            "displayMode": "chart",
            "chartConfig": {
                "chartType": "bar",
                "xAxisField": "region",
                "yAxisFields": ["amount"],
            },
            "x": 7,
            "y": 9,
            "w": 5,
            "h": 6,
        },
    ]
    conn.execute(
        "INSERT INTO dashboard (name,description,refresh_mode,config,updated_at) VALUES (?,?,?,?,?)",
        (
            "医药供应链经营看板",
            "示例大屏：销售额 / 毛利 / 品类结构 / 区域分布",
            "interval",
            json.dumps({"cards": demo_cards}, ensure_ascii=False),
            now,
        ),
    )
    conn.commit()


def init_all() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    biz = sqlite3.connect(BIZ_DB)
    biz.executescript(_biz_schema_sql())
    _seed_biz(biz)
    biz.close()
    plat = sqlite3.connect(PLATFORM_DB)
    plat.executescript(_platform_schema_sql())
    columns = {r[1] for r in plat.execute("PRAGMA table_info(chat_message)")}
    for name, spec in [
        ("reply_to", "INTEGER"),
        ("active", "INTEGER DEFAULT 1"),
        ("supersedes", "INTEGER"),
    ]:
        if name not in columns:
            plat.execute(f"ALTER TABLE chat_message ADD COLUMN {name} {spec}")
    plat.execute(
        "UPDATE chat_message SET reply_to=(SELECT MAX(u.id) FROM chat_message u "
        "WHERE u.session_id=chat_message.session_id AND u.role='user' AND u.id<chat_message.id) "
        "WHERE role='assistant' AND reply_to IS NULL"
    )
    import json

    old_sql = "SELECT COUNT(*) AS near_expiry FROM fact_inventory WHERE expiry_date <= '2026-06-30'"
    for row in plat.execute("SELECT id,config FROM dashboard").fetchall():
        cfg = json.loads(row[1] or "{}")
        changed = False
        for card in cfg.get("cards", []):
            if card.get("sql") == old_sql:
                card["sql"] = NEAR_EXPIRY_SQL
                card["title"] = "近效期库存记录数（最新快照）"
                changed = True
        if changed:
            plat.execute(
                "UPDATE dashboard SET config=? WHERE id=?",
                (json.dumps(cfg, ensure_ascii=False), row[0]),
            )
    _seed_platform(plat)
    _seed_dashboard(plat)
    plat.close()


def biz_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(Path(BIZ_DB).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def plat_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(PLATFORM_DB)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def platform_db():
    conn = plat_conn()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
