"""Bounded, manifest-backed retrieval for the assistant's system documents."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import unicodedata


APPROVED_DOCUMENT_PATHS = frozenset({
    "README.md",
    "docs/usage.md",
    "docs/architecture.md",
    "docs/development.md",
    "docs/deployment.md",
    "docs/ai-assistant.md",
})
DOCUMENT_ID_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
ASCII_TERM_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:/-]*")
CHINESE_RUN_PATTERN = re.compile(r"[\u3400-\u9fff]+")
TARGET_CHUNK_CHARS = 1_800
MAX_CHUNK_CHARS = 2_200
MAX_SEARCH_RESULTS = 6


class KnowledgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class KnowledgeSource:
    document_id: str
    title: str
    relative_path: str


@dataclass(frozen=True)
class KnowledgeChunk:
    source: KnowledgeSource
    section: str
    section_id: str
    start_line: int
    end_line: int
    content: str
    score: float = 0.0

    def as_tool_payload(self) -> dict[str, object]:
        return {
            "documentId": self.source.document_id,
            "title": self.source.title,
            "path": self.source.relative_path,
            "section": self.section,
            "sectionId": self.section_id,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "content": self.content,
        }


@dataclass(frozen=True)
class _DocumentSpec:
    source: KnowledgeSource
    path: Path


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _terms(value: str) -> set[str]:
    normalized = _normalized(value)
    terms = set(ASCII_TERM_PATTERN.findall(normalized))
    for run in CHINESE_RUN_PATTERN.findall(normalized):
        terms.update(run)
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return {term for term in terms if term}


def _section_id(document_id: str, heading_path: str, start_line: int) -> str:
    digest = hashlib.sha1(f"{document_id}\0{heading_path}\0{start_line}".encode("utf-8")).hexdigest()[:12]
    return f"section-{digest}"


def _paragraph_blocks(lines: list[str], first_line: int) -> list[tuple[str, int, int]]:
    blocks: list[tuple[str, int, int]] = []
    current: list[str] = []
    current_start = first_line
    in_fence = False
    for offset, line in enumerate(lines):
        line_number = first_line + offset
        if not current:
            current_start = line_number
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                blocks.append(("\n".join(current).strip(), current_start, line_number - 1))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(("\n".join(current).strip(), current_start, first_line + len(lines) - 1))
    return [block for block in blocks if block[0]]


def _split_section(
    source: KnowledgeSource,
    section: str,
    section_id: str,
    lines: list[str],
    start_line: int,
) -> tuple[list[KnowledgeChunk], KnowledgeChunk]:
    full_content = "\n".join(lines).strip()
    full = KnowledgeChunk(
        source=source,
        section=section,
        section_id=section_id,
        start_line=start_line,
        end_line=start_line + max(0, len(lines) - 1),
        content=full_content[:MAX_CHUNK_CHARS],
    )
    if len(full_content) <= MAX_CHUNK_CHARS:
        return [full], full

    chunks: list[KnowledgeChunk] = []
    current = ""
    chunk_start = start_line
    chunk_end = start_line
    for text, block_start, block_end in _paragraph_blocks(lines, start_line):
        pieces = [text[index:index + TARGET_CHUNK_CHARS] for index in range(0, len(text), TARGET_CHUNK_CHARS)]
        for piece in pieces:
            separator = "\n\n" if current else ""
            if current and len(current) + len(separator) + len(piece) > TARGET_CHUNK_CHARS:
                chunks.append(KnowledgeChunk(source, section, section_id, chunk_start, chunk_end, current[:MAX_CHUNK_CHARS]))
                overlap = current[-200:]
                current = f"{overlap}\n\n{piece}" if overlap else piece
                chunk_start = block_start
            else:
                if not current:
                    chunk_start = block_start
                current = f"{current}{separator}{piece}"
            chunk_end = block_end
    if current:
        chunks.append(KnowledgeChunk(source, section, section_id, chunk_start, chunk_end, current[:MAX_CHUNK_CHARS]))
    return chunks, full


class DocumentKnowledge:
    def __init__(self, project_root: Path, manifest_path: Path | None = None) -> None:
        self.project_root = Path(project_root)
        self.manifest_path = Path(manifest_path) if manifest_path else self.project_root / "docs" / "assistant-knowledge.json"
        self._signature: tuple[object, ...] | None = None
        self._chunks: tuple[KnowledgeChunk, ...] = ()
        self._sections: dict[tuple[str, str], KnowledgeChunk] = {}

    def _specs(self) -> tuple[list[_DocumentSpec], tuple[object, ...]]:
        try:
            raw_manifest = self.manifest_path.read_bytes()
            manifest = json.loads(raw_manifest.decode("utf-8-sig"))
        except FileNotFoundError as exc:
            raise KnowledgeError("知识文档清单不存在") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KnowledgeError("知识文档清单不是有效的 UTF-8 JSON") from exc
        if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("documents"), list):
            raise KnowledgeError("知识文档清单格式无效")

        try:
            root = self.project_root.resolve(strict=True)
        except FileNotFoundError as exc:
            raise KnowledgeError("项目根目录不存在") from exc
        seen_ids: set[str] = set()
        specs: list[_DocumentSpec] = []
        signature: list[object] = [hashlib.sha256(raw_manifest).hexdigest()]
        for item in manifest["documents"]:
            if not isinstance(item, dict):
                raise KnowledgeError("知识文档清单条目格式无效")
            document_id = str(item.get("id") or "")
            relative_path = str(item.get("path") or "").replace("\\", "/")
            title = str(item.get("title") or "").strip()
            if not DOCUMENT_ID_PATTERN.fullmatch(document_id) or not title:
                raise KnowledgeError("知识文档 ID 或标题无效")
            if document_id in seen_ids:
                raise KnowledgeError(f"知识文档 ID 重复：{document_id}")
            seen_ids.add(document_id)
            if relative_path not in APPROVED_DOCUMENT_PATHS:
                raise KnowledgeError(f"知识文档路径不允许：{relative_path}")
            candidate = root / relative_path
            if candidate.is_symlink():
                raise KnowledgeError(f"知识文档路径不允许使用符号链接：{relative_path}")
            try:
                resolved = candidate.resolve(strict=True)
            except FileNotFoundError as exc:
                raise KnowledgeError(f"知识文档不存在：{relative_path}") from exc
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise KnowledgeError(f"知识文档路径越过项目边界：{relative_path}")
            stat = resolved.stat()
            source = KnowledgeSource(document_id, title, relative_path)
            specs.append(_DocumentSpec(source, resolved))
            signature.extend((document_id, str(resolved), stat.st_size, stat.st_mtime_ns))
        if not specs:
            raise KnowledgeError("知识文档清单为空")
        return specs, tuple(signature)

    def _ensure_index(self) -> None:
        specs, signature = self._specs()
        if signature == self._signature:
            return
        chunks: list[KnowledgeChunk] = []
        sections: dict[tuple[str, str], KnowledgeChunk] = {}
        for spec in specs:
            try:
                lines = spec.path.read_text(encoding="utf-8-sig").splitlines()
            except UnicodeDecodeError as exc:
                raise KnowledgeError(f"知识文档不是有效的 UTF-8：{spec.source.relative_path}") from exc
            headings: list[str] = []
            section_lines: list[str] = []
            section_start = 1
            section_name = spec.source.title

            def flush() -> None:
                if not any(line.strip() for line in section_lines):
                    return
                identifier = _section_id(spec.source.document_id, section_name, section_start)
                section_chunks, full = _split_section(
                    spec.source, section_name, identifier, section_lines, section_start,
                )
                chunks.extend(section_chunks)
                sections[(spec.source.document_id, identifier)] = full

            for index, line in enumerate(lines, start=1):
                heading = HEADING_PATTERN.match(line)
                if heading:
                    flush()
                    level = len(heading.group(1))
                    title = heading.group(2).strip().strip("#").strip()
                    headings[level - 1:] = [title]
                    section_name = " / ".join(headings)
                    section_lines = [line]
                    section_start = index
                else:
                    section_lines.append(line)
            flush()
        self._chunks = tuple(chunks)
        self._sections = sections
        self._signature = signature

    def status(self) -> dict[str, object]:
        try:
            self._ensure_index()
        except KnowledgeError as exc:
            return {"configured": False, "documentCount": 0, "chunkCount": 0, "error": str(exc)}
        document_count = len({chunk.source.document_id for chunk in self._chunks})
        return {"configured": True, "documentCount": document_count, "chunkCount": len(self._chunks), "error": ""}

    def chunks(self) -> tuple[KnowledgeChunk, ...]:
        self._ensure_index()
        return self._chunks

    def search(self, query: str, limit: int = MAX_SEARCH_RESULTS) -> list[KnowledgeChunk]:
        query = str(query or "").strip()
        if not query:
            raise KnowledgeError("知识文档搜索词不能为空")
        self._ensure_index()
        normalized_query = _normalized(query)
        query_parts = [_normalized(part) for part in query.split() if part.strip()]
        query_terms = _terms(query)
        if not query_terms:
            return []
        scored: list[KnowledgeChunk] = []
        for chunk in self._chunks:
            title = _normalized(chunk.source.title)
            section = _normalized(chunk.section)
            content = _normalized(chunk.content)
            matched_terms = {term for term in query_terms if term in title or term in section or term in content}
            coverage = len(matched_terms) / len(query_terms)
            if coverage < 0.4:
                continue
            score = 12.0 * coverage
            for term in query_terms:
                weight = 1.0 + min(len(term), 4) * 0.25
                score += title.count(term) * 5.0 * weight
                score += section.count(term) * 8.0 * weight
                score += content.count(term) * weight
            for phrase in query_parts:
                if len(phrase) < 2:
                    continue
                if phrase in title:
                    score += 24.0
                if phrase in section:
                    score += 36.0
                if phrase in content:
                    score += 12.0
            if normalized_query in section:
                score += 40.0
            scored.append(replace(chunk, score=score))
        scored.sort(key=lambda item: (-item.score, item.source.document_id, item.start_line))
        selected: list[KnowledgeChunk] = []
        seen: set[tuple[str, str, str]] = set()
        for chunk in scored:
            fingerprint = (chunk.source.document_id, chunk.section_id, chunk.content[:240])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            selected.append(chunk)
            if len(selected) >= max(1, min(MAX_SEARCH_RESULTS, int(limit or MAX_SEARCH_RESULTS))):
                break
        return selected

    def read_section(self, document_id: str, section_id: str) -> KnowledgeChunk:
        self._ensure_index()
        chunk = self._sections.get((str(document_id), str(section_id)))
        if chunk is None:
            raise KnowledgeError("知识文档或章节不存在")
        return chunk
