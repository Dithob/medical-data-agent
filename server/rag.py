"""Offline lexical retrieval and evidence extraction; no embedding or rerank model."""

import re
import threading
from collections import Counter
from datetime import datetime
from .config import (
    ANSWER_TOP_K,
    CHUNK_TOKENS,
    RECALL_CANDIDATES,
    RERANK_KEEP,
    SCORE_THRESHOLD,
)
from .documents import DOCUMENTS
from .schema_store import _cosine, _ngrams


def semantic_split(text, size=CHUNK_TOKENS):
    """Sentence-aware target size with a hard character cap."""
    chunks, buf = [], ""
    for sentence in re.split(r"(?<=[。！？；\n])", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if buf and len(buf) + len(sentence) > size:
            chunks.append(buf)
            buf = ""
        while len(sentence) > size:
            chunks.append(sentence[:size])
            sentence = sentence[size:]
        buf += sentence
    if buf:
        chunks.append(buf)
    return chunks


def sections(text):
    parts = re.split(r"\n\s*\n|(?=第[一二三四五六七八九十百\d]+条)", text)
    evidence = []
    for part in parts:
        part = part.strip()
        if not part or (len(part) < 60 and part.endswith("（示例）")):
            continue
        # Bound evidence as well as index chunks, so a hit near the end of a huge
        # paragraph never gets replaced by an unrelated prefix in the answer.
        evidence.extend(part[i : i + 2000] for i in range(0, len(part), 2000))
    return evidence


STOP = (
    "是什么",
    "有什么",
    "怎么",
    "如何",
    "分别",
    "多少",
    "要求",
    "哪些",
    "请问",
    "药品",
    "规定",
    "分为几级",
)
TOPICS = (
    "冷链",
    "冷藏",
    "处方药",
    "召回",
    "温湿度",
    "温度",
    "湿度",
    "近效期",
    "预警",
    "供应商",
    "评估",
    "采购",
    "验收",
    "追溯",
    "销毁",
    "盘点",
    "存储",
    "储存",
    "养护",
    "退货",
    "销售",
    "记录",
    "培训",
)


def query_terms(question):
    q = question.replace("冷链", "冷藏").replace("温湿度", "温度湿度")
    for word in STOP:
        q = q.replace(word, "")
    segs = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", q)
    return {s[i : i + 2] for s in segs for i in range(max(1, len(s) - 1))}


class RagEngine:
    def __init__(self):
        self.chunks = ()
        self.built_at = ""
        self.lock = threading.RLock()

    def build(self):
        with self.lock:
            chunks, counts = [], Counter()
            for doc in DOCUMENTS.current():
                for section_id, passage in enumerate(sections(doc["content"])):
                    for idx, chunk in enumerate(semantic_split(passage)):
                        chunks.append(
                            {
                                "docId": doc["id"],
                                "docName": doc["name"],
                                "version": doc["version"],
                                "chunkId": f"{doc['id']}-{section_id}-{idx}",
                                "sectionId": section_id,
                                "text": chunk,
                                "passage": passage,
                                "grams": _ngrams(chunk),
                            }
                        )
                        counts[doc["id"]] += 1
            DOCUMENTS.indexed(counts)
            self.chunks = tuple(chunks)
            self.built_at = datetime.now().isoformat(timespec="seconds")

    def search(self, question, top_k=ANSWER_TOP_K, cancel=None):
        terms, grams = query_terms(question), _ngrams(question)
        snapshot = self.chunks
        scored = []
        for chunk in snapshot:
            if cancel and cancel.is_set():
                raise InterruptedError("任务已取消")
            text = (
                chunk["passage"].replace("冷链", "冷藏").replace("温湿度", "温度湿度")
            )
            hits = sum(t in text for t in terms)
            if not hits:
                continue
            score = 0.35 * _cosine(grams, chunk["grams"]) + 0.65 * hits / max(
                1, len(terms)
            )
            topics = [t for t in TOPICS if t in question]
            if topics and not any(
                t.replace("冷链", "冷藏").replace("温湿度", "温度") in text
                for t in topics
            ):
                continue
            scored.append(
                {k: v for k, v in chunk.items() if k != "grams"}
                | {"score": round(score, 4)}
            )
        scored.sort(key=lambda r: r["score"], reverse=True)
        candidates = scored[:RECALL_CANDIDATES]
        filtered = [c for c in candidates if c["score"] >= SCORE_THRESHOLD]
        retained = filtered[:RERANK_KEEP]
        top, seen = [], set()
        for chunk in retained:
            key = (chunk["docId"], chunk["sectionId"])
            if key not in seen:
                seen.add(key)
                top.append(chunk)
            if len(top) >= top_k:
                break
        return {
            "top": top,
            "stats": {
                "totalChunks": len(snapshot),
                "candidates": len(candidates),
                "afterThreshold": len(filtered),
                "afterRerank": len(retained),
                "used": len(top),
                "threshold": SCORE_THRESHOLD,
                "rerankKeep": RERANK_KEEP,
                "rerankEnabled": False,
                "retriever": "字符特征 + 词面匹配",
                "chunkUnit": "字符",
            },
        }

    def answer(self, question, cancel=None):
        res = self.search(question, cancel=cancel)
        if not res["top"]:
            return {
                "content": "未找到足够相关的知识库证据，请调整关键词或上传资料。",
                "referDoc": [],
                "stats": res["stats"],
                "status": "no_evidence",
            }
        refs, lines = [], []
        for idx, hit in enumerate(res["top"], 1):
            passage = hit["passage"][:2000]
            lines.append(f"[{idx}] 《{hit['docName']}》：{passage}")
            refs.append(
                {
                    "docId": hit["docId"],
                    "docName": hit["docName"],
                    "version": hit["version"],
                    "chunkId": hit["chunkId"],
                    "score": hit["score"],
                    "snippet": passage,
                }
            )
        return {
            "content": "以下为与问题相关的示例资料原文：\n\n"
            + "\n\n".join(lines)
            + "\n\n请结合引用原文核对；示例资料不代表现实业务或行业规定。",
            "referDoc": refs,
            "stats": res["stats"],
            "status": "evidence_found",
        }


ENGINE = RagEngine()
