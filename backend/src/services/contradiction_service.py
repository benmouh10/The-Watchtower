"""
GlobeLens AI — ContradictionService
===================================
Cross-source contradiction detection for a clustered event.

Ported from the offline research pipeline (globe/pipeline/compressor.py
detect_contradictions) and adapted to the web stack:

  * Paraphrase filtering reuses the article embeddings already stored in
    pgvector instead of recomputing them with a local sentence-transformer.
  * Operates on article sentences rather than pre-extracted fact objects,
    because the web pipeline has no fact-level compressor.
  * Emits a confidence score per pair.

Only pairs from *different* outlets are considered. Two articles that share
numbers and sit very close in embedding space are treated as paraphrase
(corroboration), not disagreement.
"""
import hashlib
import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.entities.models import Article, Embedding, Source

logger = structlog.get_logger()

_MIN_SHARED_WORDS = 8
_PARAPHRASE_SIMILARITY = 0.88
_NUMERIC_DIVERGENCE_GUARD = 0.82
_NEGATION_CONFIDENCE = 0.70
_MAX_PAIRS_PER_EVENT = 25
_SENTENCE_MIN_CHARS = 40

_NEGATION_WORDS = {
    "not", "no", "never", "deny", "denied", "denies", "denying",
    "reject", "rejected", "rejects", "refuse", "refused", "refuses",
    "false", "untrue", "contrary", "dispute", "disputed", "disputes",
    "contradicts", "contradicted", "denial", "falsehood",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9']+")
_NUMBER = re.compile(r"\d+(?:[,.]\d+)*(?:\.\d+)?")


def _cosine(a: List[float], b: List[float]) -> float:
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _sentences(text: str) -> List[str]:
    if not text:
        return []
    out = []
    for raw in _SENTENCE_SPLIT.split(text):
        cleaned = raw.strip()
        if len(cleaned) >= _SENTENCE_MIN_CHARS:
            out.append(cleaned)
    return out


def _words(text: str) -> set:
    return set(_WORD.findall(text.lower()))


def _has_opposing_numbers(text_a: str, text_b: str) -> bool:
    """True when the first number in each text differs by more than 50%."""
    nums_a = _numbers(text_a)
    nums_b = _numbers(text_b)
    if not nums_a or not nums_b:
        return False
    val_a, val_b = nums_a[0], nums_b[0]
    if val_a <= 0 or val_b <= 0:
        return False
    return (max(val_a, val_b) / min(val_a, val_b)) > 1.5


def _numbers(text: str) -> List[float]:
    values = []
    for token in _NUMBER.findall(text):
        try:
            values.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return values


class ContradictionService:
    """Detects numeric and negation conflicts between outlets covering one event."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def detect_for_event(self, event_id) -> List[Dict]:
        """
        Return structured A-vs-B contradiction pairs for an event.

        Each pair is a dict:
            {
              "type": "contradiction",
              "claim_a": {"text", "source", "article_id"},
              "claim_b": {"text", "source", "article_id"},
              "nature": "numeric_conflict" | "negation_conflict",
              "confidence": float 0..1
            }
        """
        articles = await self._load_articles(event_id)
        if len(articles) < 2:
            return []

        pairs: List[Dict] = []
        seen: set = set()

        for i in range(len(articles)):
            for j in range(i + 1, len(articles)):
                art_a, art_b = articles[i], articles[j]
                if art_a["source_name"] == art_b["source_name"]:
                    continue

                similarity = self._pair_similarity(art_a, art_b)
                if similarity >= _NUMERIC_DIVERGENCE_GUARD and not _has_opposing_numbers(
                    art_a["text"], art_b["text"]
                ):
                    continue

                for pair in self._compare(art_a, art_b, similarity):
                    key = frozenset({pair["claim_a"]["article_id"], pair["claim_b"]["article_id"]})
                    if key in seen:
                        continue
                    seen.add(key)
                    pairs.append(pair)
                    if len(pairs) >= _MAX_PAIRS_PER_EVENT:
                        logger.info(
                            "Contradiction cap reached",
                            event_id=str(event_id),
                            cap=_MAX_PAIRS_PER_EVENT,
                        )
                        return pairs

        if pairs:
            logger.info(
                "Contradictions detected",
                event_id=str(event_id),
                count=len(pairs),
                articles=len(articles),
            )
        return pairs

    # ── internals ────────────────────────────────────────────────────────────

    async def _load_articles(self, event_id) -> List[Dict]:
        result = await self._session.execute(
            select(Article, Source, Embedding)
            .join(Source, Article.source_id == Source.id)
            .join(Embedding, Embedding.article_id == Article.id, isouter=True)
            .where(Article.event_id == event_id, Article.is_hidden.is_(False))
            .order_by(Article.created_at.asc(), Article.id.asc())
        )
        rows = result.all()
        articles: List[Dict] = []
        for article, source, embedding in rows:
            text = f"{article.title or ''}\n{article.content or ''}".strip()
            if not text:
                continue
            articles.append(
                {
                    "article_id": str(article.id),
                    "source_name": source.name,
                    "text": text,
                    "sentences": _sentences(text),
                    "vector": list(embedding.vector) if embedding and embedding.vector else None,
                }
            )
        return articles

    @staticmethod
    def _pair_similarity(art_a: Dict, art_b: Dict) -> float:
        vec_a, vec_b = art_a.get("vector"), art_b.get("vector")
        if not vec_a or not vec_b:
            return 0.0
        try:
            return _cosine(vec_a, vec_b)
        except (ValueError, TypeError):
            return 0.0

    def _compare(self, art_a: Dict, art_b: Dict, similarity: float) -> List[Dict]:
        results: List[Dict] = []

        for sent_a in art_a["sentences"]:
            words_a = _words(sent_a)
            if not words_a:
                continue
            nums_a = set(_numbers(sent_a))

            for sent_b in art_b["sentences"]:
                words_b = _words(sent_b)
                shared = words_a & words_b
                if len(shared) < _MIN_SHARED_WORDS:
                    continue

                nums_b = set(_numbers(sent_b))
                if (nums_a & nums_b) and similarity >= _PARAPHRASE_SIMILARITY:
                    continue

                numeric = self._numeric_conflict(sent_a, sent_b)
                if numeric and similarity >= _NUMERIC_DIVERGENCE_GUARD:
                    continue

                neg_a = bool(words_a & _NEGATION_WORDS)
                neg_b = bool(words_b & _NEGATION_WORDS)

                if numeric:
                    nature, divergence = "numeric_conflict", numeric
                elif neg_a and neg_b:
                    nature, divergence = "negation_conflict", _NEGATION_CONFIDENCE
                else:
                    continue

                overlap_score = min(1.0, len(shared) / 20.0)
                confidence = round(min(1.0, 0.35 + 0.35 * overlap_score + 0.30 * divergence), 2)

                results.append(
                    {
                        # Stable identity for this contradiction, derived from the
                        # two article ids rather than randomly generated. The
                        # frontend needs a key that survives a re-run so it can
                        # track a contradiction across polls, expand/collapse it,
                        # or deep-link to it. A UUID regenerated on every
                        # detection would change identity every time.
                        "contradiction_id": self._contradiction_id(
                            art_a["article_id"], art_b["article_id"]
                        ),
                        "type": "contradiction",
                        "claim_a": {
                            "text": sent_a,
                            "source": art_a["source_name"],
                            "article_id": art_a["article_id"],
                        },
                        "claim_b": {
                            "text": sent_b,
                            "source": art_b["source_name"],
                            "article_id": art_b["article_id"],
                        },
                        "nature": nature,
                        "confidence": confidence,
                    }
                )

        return results

    @staticmethod
    def _contradiction_id(article_id_a: str, article_id_b: str) -> str:
        """
        Deterministic short id for an (article A, article B) pair.

        The pair is sorted before hashing so that the same disagreement always
        produces the same id regardless of which article the loop reached first.
        """
        pair = "|".join(sorted([str(article_id_a), str(article_id_b)]))
        return hashlib.sha256(pair.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _numeric_conflict(sent_a: str, sent_b: str) -> Optional[float]:
        nums_a = _numbers(sent_a)
        nums_b = _numbers(sent_b)
        if not nums_a or not nums_b:
            return None
        val_a, val_b = nums_a[0], nums_b[0]
        if val_a <= 0 or val_b <= 0:
            return None
        ratio = max(val_a, val_b) / min(val_a, val_b)
        if ratio <= 1.5:
            return None
        return min(1.0, (ratio - 1.0) / 2.0)
