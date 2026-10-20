"""Immutable document versions. Uploaded bytes stay in SQLite, never at client paths."""

import hashlib
import re
import uuid
from datetime import datetime
from pathlib import Path
from .config import UPLOAD_DIR, MAX_UPLOAD_BYTES
from .db import platform_db
from .seed_docs import DOCS


class DocumentRepository:
    def initialize(self):
        with platform_db() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS kb_document (
                id INTEGER PRIMARY KEY, uuid TEXT NOT NULL UNIQUE, name TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS kb_version (
                id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL, version INTEGER NOT NULL,
                sha256 TEXT NOT NULL, content TEXT NOT NULL, size INTEGER NOT NULL,
                status TEXT NOT NULL, chunks INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                UNIQUE(document_id, sha256));
            """)
            empty = con.execute("SELECT COUNT(*) FROM kb_document").fetchone()[0] == 0
        for name, text in DOCS.items():
            self.save(name + ".txt", text.encode("utf-8"))
        if empty:
            for path in sorted(Path(UPLOAD_DIR).glob("*")):
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.suffix.lower() in (".md", ".txt")
                ):
                    if path.stat().st_size <= MAX_UPLOAD_BYTES:
                        try:
                            self.save(path.name, path.read_bytes())
                        except (ValueError, UnicodeError):
                            continue

    def save(self, filename, content):
        if (
            not filename
            or len(filename) > 180
            or re.search(r"[\\/:\x00-\x1f]", filename)
            or filename in (".", "..")
        ):
            raise ValueError("文件名不得包含路径、控制字符或超过 180 字符")
        if Path(filename).suffix.lower() not in (".txt", ".md"):
            raise ValueError("仅支持 UTF-8 TXT/Markdown 文档")
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("文件必须非空且不超过 2 MiB")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("文档必须使用 UTF-8 编码") from exc
        if not text.strip() or "\x00" in text:
            raise ValueError("文档必须包含有效文本")
        digest = hashlib.sha256(content).hexdigest()
        with platform_db() as con:
            con.execute(
                "INSERT OR IGNORE INTO kb_document(uuid,name) VALUES (?,?)",
                (str(uuid.uuid4()), filename),
            )
            doc = con.execute(
                "SELECT id FROM kb_document WHERE name=?", (filename,)
            ).fetchone()[0]
            existing = con.execute(
                "SELECT id FROM kb_version WHERE document_id=? AND sha256=?",
                (doc, digest),
            ).fetchone()
            if existing:
                return existing[0]
            version = con.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM kb_version WHERE document_id=?",
                (doc,),
            ).fetchone()[0]
            return con.execute(
                "INSERT INTO kb_version(document_id,version,sha256,content,size,status,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    doc,
                    version,
                    digest,
                    text,
                    len(content),
                    "parsing",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            ).lastrowid

    def current(self):
        with platform_db() as con:
            return [
                dict(row)
                for row in con.execute("""SELECT v.*, d.name, d.uuid FROM kb_version v
                JOIN kb_document d ON d.id=v.document_id
                WHERE v.id=(SELECT v2.id FROM kb_version v2 WHERE v2.document_id=v.document_id
                            AND v2.status IN ('parsing','indexed') ORDER BY v2.version DESC LIMIT 1)
                ORDER BY d.id""")
            ]

    def get(self, version_id):
        with platform_db() as con:
            row = con.execute(
                "SELECT v.*,d.name FROM kb_version v JOIN kb_document d ON d.id=v.document_id WHERE v.id=?",
                (version_id,),
            ).fetchone()
            return dict(row) if row else None

    def indexed(self, counts):
        with platform_db() as con:
            con.executemany(
                "UPDATE kb_version SET status='indexed', chunks=? WHERE id=?",
                [(n, k) for k, n in counts.items()],
            )

    def failed(self, version_id):
        with platform_db() as con:
            con.execute(
                "UPDATE kb_version SET status='failed' WHERE id=?", (version_id,)
            )

    def list(self):
        with platform_db() as con:
            rows = con.execute("""SELECT d.id documentId,d.uuid,d.name doc_name,v.id,v.version,v.sha256,
                v.size,v.status,v.chunks,v.created_at,
                (SELECT COUNT(*) FROM kb_version h WHERE h.document_id=d.id) versions
                FROM kb_document d JOIN kb_version v ON v.document_id=d.id
                WHERE v.version=(SELECT MAX(h.version) FROM kb_version h WHERE h.document_id=d.id)
                ORDER BY d.id""").fetchall()
            return [
                {**dict(r), "file_type": Path(r["doc_name"]).suffix[1:].upper()}
                for r in rows
            ]


DOCUMENTS = DocumentRepository()
