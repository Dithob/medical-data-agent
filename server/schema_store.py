"""
数据字典 / Schema 检索。

对应真实项目中的「数据字典元数据增强 → 富文本拼接 → 向量化 → 召回」。
demo 里向量化退化为「字符 bigram 余弦 + 关键词命中加权」的轻量检索（零依赖、离线可跑），
但**链路与接口保持与真实项目一致**：召回表名 + 字段名 + 分数，供 SQL 生成前做字段合法性校验。
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Tuple

# ---------------------------------------------------------------- 数据字典

SCHEMA: Dict[str, dict] = {
    "fact_sales": {
        "cn": "销售事实表",
        "desc": "门店每日药品销售明细，含销量、销售额与成本，用于销售分析与毛利分析",
        "fields": {
            "sale_id": {"cn": "销售记录ID", "type": "INTEGER"},
            "date_id": {"cn": "销售日期", "type": "TEXT", "sample": "2025-03-12"},
            "drug_id": {"cn": "药品ID", "type": "INTEGER"},
            "store_id": {"cn": "门店ID", "type": "INTEGER"},
            "quantity": {"cn": "销售数量", "type": "INTEGER"},
            "amount": {"cn": "销售金额(元)", "type": "REAL"},
            "cost_amount": {"cn": "销售成本(元)", "type": "REAL"},
        },
    },
    "fact_purchase": {
        "cn": "采购事实表",
        "desc": "向供应商采购药品的记录，含采购量与采购金额，用于采购与供应商分析",
        "fields": {
            "purchase_id": {"cn": "采购记录ID", "type": "INTEGER"},
            "date_id": {"cn": "采购日期", "type": "TEXT", "sample": "2025-03-12"},
            "drug_id": {"cn": "药品ID", "type": "INTEGER"},
            "supplier_id": {"cn": "供应商ID", "type": "INTEGER"},
            "quantity": {"cn": "采购数量", "type": "INTEGER"},
            "amount": {"cn": "采购金额(元)", "type": "REAL"},
        },
    },
    "fact_inventory": {
        "cn": "库存事实表",
        "desc": "门店月末药品库存快照，含库存量、库存金额与效期，用于库存与近效期分析",
        "fields": {
            "inv_id": {"cn": "库存记录ID", "type": "INTEGER"},
            "date_id": {"cn": "库存快照日期", "type": "TEXT", "sample": "2025-01-28"},
            "drug_id": {"cn": "药品ID", "type": "INTEGER"},
            "store_id": {"cn": "门店ID", "type": "INTEGER"},
            "stock_qty": {"cn": "库存数量", "type": "INTEGER"},
            "stock_amount": {"cn": "库存金额(元)", "type": "REAL"},
            "expiry_date": {"cn": "有效期至", "type": "TEXT", "sample": "2026-05-01"},
        },
    },
    "dim_drug": {
        "cn": "药品维度表",
        "desc": "药品主数据，含药品名称、品类、规格、生产企业与是否处方药",
        "fields": {
            "drug_id": {"cn": "药品ID", "type": "INTEGER"},
            "drug_name": {"cn": "药品名称", "type": "TEXT", "sample": "阿莫西林胶囊"},
            "category": {"cn": "药品品类", "type": "TEXT",
                         "enum": ["化学药", "中成药", "生物制品", "医疗器械", "保健品"]},
            "spec": {"cn": "规格", "type": "TEXT", "sample": "10mg*24片"},
            "manufacturer": {"cn": "生产企业", "type": "TEXT", "sample": "制药厂1"},
            "unit": {"cn": "单位", "type": "TEXT", "enum": ["盒", "瓶", "支", "袋"]},
            "is_prescription": {"cn": "是否处方药", "type": "INTEGER", "enum": [0, 1]},
        },
    },
    "dim_store": {
        "cn": "门店维度表",
        "desc": "门店主数据，含门店名称、城市、所属区域与门店类型",
        "fields": {
            "store_id": {"cn": "门店ID", "type": "INTEGER"},
            "store_name": {"cn": "门店名称", "type": "TEXT", "sample": "上海1号店"},
            "city": {"cn": "所在城市", "type": "TEXT", "sample": "上海"},
            "region": {"cn": "所属区域", "type": "TEXT",
                       "enum": ["华东", "华南", "华北", "西南", "华中"]},
            "store_type": {"cn": "门店类型", "type": "TEXT", "enum": ["直营", "加盟"]},
        },
    },
    "dim_supplier": {
        "cn": "供应商维度表",
        "desc": "供应商主数据，含供应商名称、所在城市与等级",
        "fields": {
            "supplier_id": {"cn": "供应商ID", "type": "INTEGER"},
            "supplier_name": {"cn": "供应商名称", "type": "TEXT", "sample": "医药供应商A"},
            "city": {"cn": "所在城市", "type": "TEXT", "sample": "上海"},
            "level": {"cn": "供应商等级", "type": "TEXT", "enum": ["A", "B", "C"]},
        },
    },
}

# 业务术语 → 字段（对应真实项目里的「业务规则过滤 + 常见枚举值」增强）
TERM_HINTS = {
    "品类": "dim_drug.category", "类别": "dim_drug.category", "分类": "dim_drug.category",
    "药品": "dim_drug.drug_name", "商品": "dim_drug.drug_name", "品种": "dim_drug.drug_name",
    "厂家": "dim_drug.manufacturer", "厂商": "dim_drug.manufacturer", "生产企业": "dim_drug.manufacturer",
    "规格": "dim_drug.spec", "处方药": "dim_drug.is_prescription",
    "区域": "dim_store.region", "大区": "dim_store.region",
    "城市": "dim_store.city", "门店": "dim_store.store_name", "店铺": "dim_store.store_name",
    "门店类型": "dim_store.store_type",
    "供应商": "dim_supplier.supplier_name", "等级": "dim_supplier.level",
    "销售额": "fact_sales.amount", "销量": "fact_sales.quantity", "成本": "fact_sales.cost_amount",
    "采购额": "fact_purchase.amount", "采购量": "fact_purchase.quantity",
    "库存": "fact_inventory.stock_amount", "效期": "fact_inventory.expiry_date",
}


# ---------------------------------------------------------------- 检索


def _ngrams(text: str, n: int = 2) -> set:
    """中文字符集合与英文数字 bigram 特征；不是模型 Embedding。"""
    cleaned = re.sub(r"[\s，。、；：（）()【】\[\]]+", "", text.lower())
    grams = set()
    for seg in re.findall(r"[\u4e00-\u9fa5]|[a-z0-9_]+", cleaned):
        if len(seg) == 1:
            grams.add(seg)
        else:
            for i in range(len(seg) - 1):
                grams.add(seg[i:i + 2])
            grams.add(seg)
    return grams


def _cosine(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / math.sqrt(len(a) * len(b))


def _keyword_bonus(question: str, text: str) -> float:
    """关键词直接命中加成，模拟「混合检索」里的稀疏信号。"""
    bonus = 0.0
    for term in TERM_HINTS:
        if term in question and term in text:
            bonus += 0.12
    return min(bonus, 0.45)


class SchemaStore:
    """表级 + 字段级两级文档，各自独立召回。"""

    def __init__(self) -> None:
        self.table_docs: List[Tuple[str, str, set]] = []   # (table, text, grams)
        self.field_docs: List[Tuple[str, str, str, set]] = []  # (table, field, text, grams)
        for table, meta in SCHEMA.items():
            fields_txt = "，".join(
                f"{f}({v['cn']}" + (f"，取值：{'/'.join(map(str, v['enum']))}" if v.get('enum') else "")
                + (f"，示例：{v['sample']}" if v.get('sample') else "") + ")"
                for f, v in meta["fields"].items()
            )
            text = f"{table} {meta['cn']} {meta['desc']}。字段：{fields_txt}"
            self.table_docs.append((table, text, _ngrams(text)))
            for field, v in meta["fields"].items():
                extra = ""
                if v.get("enum"):
                    extra += f"，常见枚举值：{'/'.join(map(str, v['enum']))}"
                if v.get("sample"):
                    extra += f"，示例：{v['sample']}"
                ftext = f"{meta['cn']} {table}.{field} 含义：{v['cn']} 类型：{v['type']}{extra}"
                self.field_docs.append((table, field, ftext, _ngrams(ftext)))

    def retrieve(self, question: str, top_tables: int = 4, top_fields: int = 12):
        qg = _ngrams(question)
        tables = []
        for table, text, grams in self.table_docs:
            score = _cosine(qg, grams) + _keyword_bonus(question, text)
            tables.append({"table": table, "cn": SCHEMA[table]["cn"], "score": round(score, 4)})
        tables.sort(key=lambda x: x["score"], reverse=True)

        fields = []
        for table, field, text, grams in self.field_docs:
            score = _cosine(qg, grams) + _keyword_bonus(question, text)
            fields.append({"table": table, "field": field,
                           "cn": SCHEMA[table]["fields"][field]["cn"], "score": round(score, 4)})
        fields.sort(key=lambda x: x["score"], reverse=True)

        return {
            "tables": tables[:top_tables],
            "fields": fields[:top_fields],
            "termHints": [{"term": t, "field": f} for t, f in TERM_HINTS.items() if t in question],
        }

    def known_fields(self, table: str) -> set:
        return set(SCHEMA.get(table, {}).get("fields", {}).keys())


STORE = SchemaStore()
