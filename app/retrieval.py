"""Small transparent BM25 retriever over a curated synthetic corpus."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from app.schemas import KnowledgeDocument


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
QUERY_SYNONYMS = {
    "northern": {"north"},
    "southern": {"south"},
    "eastern": {"east"},
    "western": {"west"},
    "location": {"region"},
    "scope": {"project"},
    "unclear": {"uncertain"},
    "alternately": {"conflicting"},
}
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for", "from",
    "in", "is", "it", "of", "on", "or", "our", "the", "to", "we", "what", "with",
    "you", "your", "i", "me", "my", "this", "that", "can", "could", "would", "will",
    "company", "companies", "firm", "firms", "business", "businesses", "enterprise",
    "enterprises", "grant", "grants", "fund", "funds", "funding", "program", "programs",
    "request", "requests", "requesting", "asking", "asks", "seek", "seeking", "need",
    "needs", "information", "info",
}


@dataclass(frozen=True)
class RetrievedDocument:
    document: KnowledgeDocument
    score: float


class KnowledgeRetriever:
    """BM25 ranks curated records; it does not claim semantic understanding."""

    def __init__(
        self,
        documents: list[KnowledgeDocument],
        *,
        top_k: int = 6,
        minimum_score: float = 0.0,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        if not documents:
            raise ValueError("Knowledge corpus must not be empty")
        if top_k < 1 or minimum_score < 0 or k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("Invalid BM25 configuration")
        ids = [document.document_id for document in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("Knowledge document IDs must be unique")
        self.documents = documents
        self.top_k = top_k
        self.minimum_score = minimum_score
        self.k1 = k1
        self.b = b
        self._tokens = [self._tokenize(f"{doc.title} {doc.content}") for doc in documents]
        self._lengths = [len(tokens) for tokens in self._tokens]
        self._average_length = sum(self._lengths) / len(self._lengths)
        self._document_frequency: dict[str, int] = {}
        for tokens in self._tokens:
            for token in set(tokens):
                self._document_frequency[token] = self._document_frequency.get(token, 0) + 1

    @classmethod
    def from_json(cls, path: str | Path, **kwargs) -> "KnowledgeRetriever":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Knowledge file root must be a JSON array")
            documents = [KnowledgeDocument.model_validate(item) for item in raw]
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError("Could not load the knowledge corpus") from exc
        return cls(documents, **kwargs)

    @classmethod
    def from_default_corpus(cls) -> "KnowledgeRetriever":
        corpus_path = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.json"
        return cls.from_json(corpus_path)

    def search(self, query: str) -> list[RetrievedDocument]:
        terms = set(self._tokenize(query))
        for term in tuple(terms):
            terms.update(QUERY_SYNONYMS.get(term, set()))
        # "R&D" tokenizes as the uninformative letters "r" and "d". Expand
        # that common shorthand so the project rule and required-information
        # document can be retrieved for scope questions.
        if re.search(r"\br\s*&\s*d\b", query, re.IGNORECASE):
            terms.update({"research", "development", "project"})
        if not terms:
            return []
        ranked = []
        document_count = len(self.documents)
        for index, document in enumerate(self.documents):
            frequencies: dict[str, int] = {}
            for token in self._tokens[index]:
                frequencies[token] = frequencies.get(token, 0) + 1
            score = 0.0
            for term in terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                document_frequency = self._document_frequency[term]
                inverse_frequency = math.log(
                    1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                length_norm = 1 - self.b + self.b * self._lengths[index] / self._average_length
                score += inverse_frequency * frequency * (self.k1 + 1) / (frequency + self.k1 * length_norm)
            if score > self.minimum_score:
                ranked.append(RetrievedDocument(document=document, score=score))
        ranked.sort(key=lambda match: (-match.score, match.document.document_id))
        return ranked[: self.top_k]

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [token for token in TOKEN_PATTERN.findall(text.casefold()) if token not in STOP_WORDS]
