"""Runbook 检索（RAG）——零依赖实现。

设计说明（架构文档 §7）：
- 文档量少（6~12 份）时向量检索不稳定，因此内置检索器用
  「中文字符 bigram + 英文词」混合 TF-IDF 余弦相似度，确定性、可单测；
- 每条结果必须带 doc_id / title / section / source_path / version，保证可溯源；
- 分数过低或无结果时返回空列表，由调用方标记 knowledge_status=INSUFFICIENT，
  进入保守只读调查，绝不基于不可靠片段申请修复；
- 预留 ChromaRetriever 接口位置：文档量大时可替换实现，上层契约不变。
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge" / "runbooks"

# 相似度低于该值视为未命中（避免强行返回无关文档）；
# 实测已知场景命中分数 0.4+，无关问题 0.1 以下，取 0.2 为安全分界
SCORE_FLOOR = 0.2

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    """中英文混合分词：CJK 单字成 bigram，拉丁文按词切。"""
    tokens: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        tokens.append(word)
    # 中文字符 bigram
    cjk = "".join(_CJK_RE.findall(text))
    for i in range(len(cjk) - 1):
        tokens.append(cjk[i: i + 2])
    if len(cjk) == 1:
        tokens.append(cjk)
    return tokens


@dataclass
class RunbookDoc:
    doc_id: str
    title: str
    service: str
    version: str
    risk_level: str
    path: str
    sections: dict[str, str] = field(default_factory=dict)


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """解析 ```---``` 包围的简单 front matter（key: value，不引第三方 yaml）。"""
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
            body = parts[2]
    return meta, body


def load_docs(knowledge_dir: Path | None = None) -> list[RunbookDoc]:
    """加载 knowledge/runbooks/**/*.md，按 '## ' 标题分块。"""
    root = knowledge_dir or KNOWLEDGE_DIR
    docs: list[RunbookDoc] = []
    if not root.exists():
        logger.warning("knowledge dir not found: %s", root)
        return docs
    for path in sorted(root.glob("**/*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = parse_front_matter(text)
        sections: dict[str, str] = {}
        current = "overview"
        buffer: list[str] = []
        for line in body.splitlines():
            if line.startswith("## "):
                if buffer:
                    sections[current] = "\n".join(buffer).strip()
                current = line[3:].strip()
                buffer = []
            else:
                buffer.append(line)
        if buffer:
            sections[current] = "\n".join(buffer).strip()
        docs.append(RunbookDoc(
            doc_id=meta.get("doc_id", path.stem),
            title=meta.get("title", path.stem),
            service=meta.get("service", ""),
            version=meta.get("version", "1"),
            risk_level=meta.get("risk_level", "unknown"),
            path=str(path.relative_to(root.parent.parent)),
            sections=sections,
        ))
    return docs


@dataclass
class Chunk:
    doc: RunbookDoc
    section: str
    text: str
    tf: dict[str, float]


class TfidfIndex:
    """轻量 TF-IDF 索引（内存态，启动时构建，文档变更后重建）。"""

    def __init__(self, docs: list[RunbookDoc]) -> None:
        self.docs = docs
        self.chunks: list[Chunk] = []
        self.df: dict[str, int] = {}
        for doc in docs:
            for section, text in doc.sections.items():
                tokens = _tokenize(f"{doc.title} {section} {text}")
                tf: dict[str, float] = {}
                for t in tokens:
                    tf[t] = tf.get(t, 0.0) + 1.0
                norm = math.sqrt(sum(v * v for v in tf.values())) or 1.0
                for t in tf:
                    tf[t] /= norm
                chunk = Chunk(doc=doc, section=section, text=text, tf=tf)
                self.chunks.append(chunk)
                for t in tf:
                    self.df[t] = self.df.get(t, 0) + 1

    def _idf(self, term: str) -> float:
        n = len(self.chunks) or 1
        return math.log((n + 1) / ((self.df.get(term, 0)) + 0.5))

    def search(self, query: str, service: str | None = None, k: int = 3) -> list[dict]:
        if not self.chunks:
            return []
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        q_tf: dict[str, float] = {}
        for t in q_tokens:
            q_tf[t] = q_tf.get(t, 0.0) + 1.0
        norm = math.sqrt(sum(v * v for v in q_tf.values())) or 1.0
        for t in q_tf:
            q_tf[t] /= norm

        results: list[tuple[float, Chunk]] = []
        for chunk in self.chunks:
            if service and chunk.doc.service and chunk.doc.service != service:
                # service 不匹配时降权而非直接丢弃（服务字段可能缺失）
                penalty = 0.3
            else:
                penalty = 1.0
            score = 0.0
            for term, qw in q_tf.items():
                tv = chunk.tf.get(term)
                if tv:
                    score += qw * tv * self._idf(term)
            results.append((score * penalty, chunk))
        results.sort(key=lambda x: x[0], reverse=True)

        output: list[dict] = []
        for score, chunk in results:
            if score < SCORE_FLOOR or len(output) >= k:
                break
            output.append({
                "doc_id": chunk.doc.doc_id,
                "title": chunk.doc.title,
                "section": chunk.section,
                "excerpt": chunk.text[:400],
                "score": round(score, 4),
                "source_path": chunk.doc.path,
                "version": chunk.doc.version,
                "service": chunk.doc.service,
                "risk_level": chunk.doc.risk_level,
            })
        return output


@lru_cache(maxsize=8)
def _index_for_dir(knowledge_dir: str) -> TfidfIndex:
    return TfidfIndex(load_docs(Path(knowledge_dir)))


def build_index() -> TfidfIndex:
    """显式构建默认知识库索引（CLI init 时调用，预热并打印文档数）。"""
    index = _index_for_dir(str(KNOWLEDGE_DIR))
    logger.info("runbook index built: %d docs / %d chunks", len(index.docs), len(index.chunks))
    return index


def retrieve_runbook(query: str, service: str | None = None, k: int = 3) -> list[dict]:
    """检索接口契约（架构文档 §7.2）。

    返回 [{doc_id, title, section, excerpt, score, source_path, version}, ...]；
    无可靠结果时返回空列表（调用方标记 INSUFFICIENT）。
    """
    return _index_for_dir(str(KNOWLEDGE_DIR)).search(query, service=service, k=k)
