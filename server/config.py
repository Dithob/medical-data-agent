import os
from datetime import date

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.abspath(
    os.environ.get("MEDICAL_RAG_DATA_DIR", os.path.join(BASE_DIR, "data"))
)
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
STATIC_DIR = os.path.join(BASE_DIR, "static")

BIZ_DB = os.path.join(DATA_DIR, "biz.db")
PLATFORM_DB = os.path.join(DATA_DIR, "platform.db")

# 离线词面检索参数；不与原项目模型分数作等效比较。
CHUNK_TOKENS = 128  # 兼容旧配置名；实际单位为字符，不是模型 token
RECALL_CANDIDATES = 512  # 混合检索召回候选数
SCORE_THRESHOLD = 0.2  # 相似度阈值
RERANK_KEEP = 50  # 重排保留条数
ANSWER_TOP_K = 5  # 最终参与生成的片段数

SQL_MAX_ROWS = 500  # 问数结果最大返回行数
SQL_TIMEOUT_SECONDS = 2.0
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
AS_OF_DATE = date.fromisoformat(os.environ.get("MEDICAL_RAG_AS_OF_DATE", "2026-08-31"))
