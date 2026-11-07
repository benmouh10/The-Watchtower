"""
Compression & Deduplication Module
====================================
Implements §5 of the pipeline spec.

Two stages, both run entirely without an LLM:

1. Intra-article compression (articles only):
   Score sentences by entity presence, numbers, quotes, attribution language.
   Keep top 35-40%. Short posts/statements are never compressed.

2. Cross-source deduplication:
   Compare all compressed sentences from all sources in a cluster.
   Semantically equivalent sentences merge into FactObject with confirmation
   counts. The LLM sees ONE sentence per unique fact, annotated with how
   many outlets confirm it.
"""

import logging
import re
from typing import Any

from pipeline.data_models import FactObject, SentenceMapping

logger = logging.getLogger(__name__)

# Regex patterns for sentence scoring
QUOTE_PATTERN = re.compile(r'["\u201c\u201d\u00ab\u00bb«»]')
ATTRIBUTION_VERBS = {
    "said", "stated", "confirmed", "denied", "announced", "declared",
    "claimed", "reported", "told", "added", "explained", "warned",
    "revealed", "acknowledged", "stressed", "insisted", "noted",
    "emphasized", "urged", "pledged", "vowed",
}
NUMBER_PATTERN = re.compile(r'\b\d[\d,.]+%?\b')

# Words that invert the polarity of a claim. When BOTH sides of a pair use
# them (e.g. outlet A "denied X", outlet B "disputed X") the pair disagrees in
# framing rather than merely paraphrasing — the signal behind negation_conflict.
NEGATION_WORDS = {
    "not", "no", "never", "deny", "denied", "denies",
    "reject", "rejected", "rejects", "refuse", "refused", "refuses",
    "false", "untrue", "contrary", "dispute", "disputed", "disputes",
    "contradicts", "contradicted", "denial", "falsehood",
}


class Compressor:
    """
    Handles intra-article compression and cross-source deduplication.
    Produces FactObject instances from SentenceMapping instances.
    """

    def __init__(
        self,
        embedder=None,
        default_keep_ratio: float = 0.40,
        investigative_keep_ratio: float = 0.65,
        dedup_similarity_threshold: float = 0.80,
    ):
        """
        Args:
            embedder: Embedder instance for semantic similarity.
            default_keep_ratio: Fraction of sentences to keep (articles).
            investigative_keep_ratio: Higher ceiling for analysis pieces.
            dedup_similarity_threshold: Cosine threshold for merging facts.
        """
        self.embedder = embedder
        self.default_keep_ratio = default_keep_ratio
        self.investigative_keep_ratio = investigative_keep_ratio
        self.dedup_threshold = dedup_similarity_threshold
        logger.info("Compressor ready.")

    # ─── Stage 1: Intra-article compression ──────────────────────────────────

    def compress(
        self,
        sentence_mappings: list[SentenceMapping],
        is_investigative: bool = False,
    ) -> list[SentenceMapping]:
        """
        Compress a single article's sentences by scoring and selecting top N%.

        Short posts, statements, and OSINT posts are never compressed —
        they are returned unchanged.

        Args:
            sentence_mappings: Sentences from ONE article.
            is_investigative:  If True, use higher keep ratio (60-70%).

        Returns:
            Filtered list of SentenceMapping objects (no rewriting).
        """
        if not sentence_mappings:
            return sentence_mappings

        # Short posts/statements/OSINT skip compression entirely (§5.1)
        content_type = sentence_mappings[0].content_type
        if content_type in ("short_post", "statement", "osint"):
            logger.debug(f"Skipping compression for content_type={content_type}")
            return sentence_mappings

        if len(sentence_mappings) <= 3:
            return sentence_mappings

        # Score each sentence
        scored = []
        for mapping in sentence_mappings:
            score = self._score_sentence(mapping)
            scored.append((score, mapping))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Keep top N%
        keep_ratio = (
            self.investigative_keep_ratio if is_investigative
            else self.default_keep_ratio
        )
        keep_count = max(2, int(len(scored) * keep_ratio))
        selected = scored[:keep_count]

        # Re-sort by original position (char_start) to preserve narrative order
        selected.sort(key=lambda x: x[1].char_start)
        result = [mapping for _, mapping in selected]

        logger.info(
            f"Compressed {len(sentence_mappings)} → {len(result)} sentences "
            f"(keep_ratio={keep_ratio:.0%})"
        )
        return result

    def _score_sentence(self, mapping: SentenceMapping) -> float:
        """
        Score a sentence for extractive selection.
        Higher score = more informative / important.

        Scoring criteria (§5.1):
          - Presence of named entities
          - Numerical values
          - Direct quotes
          - Attribution language (said/confirmed/denied/stated)
        """
        text = mapping.translated_text or mapping.original_text
        score = 0.0

        # Named entities (from NER stage)
        entity_count = len(mapping.entities)
        score += min(entity_count * 0.15, 0.6)

        # Numbers and statistics
        numbers = NUMBER_PATTERN.findall(text)
        score += min(len(numbers) * 0.2, 0.4)

        # Quotation marks (direct quotes)
        quotes = QUOTE_PATTERN.findall(text)
        if quotes:
            score += 0.3

        # Attribution verbs
        text_lower = text.lower()
        for verb in ATTRIBUTION_VERBS:
            if verb in text_lower:
                score += 0.25
                break

        # Length bonus (slightly prefer medium-length sentences)
        word_count = len(text.split())
        if 15 <= word_count <= 40:
            score += 0.1

        return score

    # ─── Stage 2: Cross-source deduplication ─────────────────────────────────

    def deduplicate(
        self,
        all_mappings: list[SentenceMapping],
    ) -> list[FactObject]:
        """
        Compare all sentences from all sources in a cluster and merge
        semantically equivalent sentences into FactObject instances.

        FIX: We now de-duplicate *within* each outlet first (collapse
        near-duplicate phrasings of the same fact from the same source)
        before running cross-source comparison.  This prevents the
        "flexibility clause × 3" duplication bug.

        Args:
            all_mappings: All compressed sentences from all articles in cluster.

        Returns:
            List of FactObject instances with confirmation counts.
        """
        if not all_mappings:
            return []

        # Step 1 — intra-source deduplication
        deduped_mappings = self._dedup_within_source(all_mappings)

        # Step 2 — cross-source deduplication
        if self.embedder is not None:
            facts = self._deduplicate_semantic(deduped_mappings)
        else:
            facts = self._deduplicate_textual(deduped_mappings)

        # Step 3 — coverage floor: ensure no high-value numeric claim is dropped
        facts = self._apply_coverage_floor(facts, all_mappings)

        return facts

    def _dedup_within_source(
        self,
        mappings: list[SentenceMapping],
    ) -> list[SentenceMapping]:
        """
        Collapse near-duplicate sentences from the **same outlet** before
        cross-source deduplication.  Uses cosine similarity when an embedder
        is available; falls back to Jaccard otherwise.

        Threshold: 0.92 cosine / 0.60 Jaccard (stricter than cross-source
        because same-outlet duplicates are almost always exact paraphrases).
        """
        from collections import defaultdict
        by_outlet: dict[str, list[SentenceMapping]] = defaultdict(list)
        for m in mappings:
            by_outlet[m.outlet].append(m)

        result: list[SentenceMapping] = []
        for outlet, outlet_mappings in by_outlet.items():
            if len(outlet_mappings) <= 1:
                result.extend(outlet_mappings)
                continue

            if self.embedder is not None:
                texts = [m.translated_text or m.original_text for m in outlet_mappings]
                embeddings = self.embedder.embed_batch(texts)
                kept = [True] * len(outlet_mappings)
                for i in range(len(outlet_mappings)):
                    if not kept[i]:
                        continue
                    for j in range(i + 1, len(outlet_mappings)):
                        if not kept[j]:
                            continue
                        sim = self._cosine_similarity(embeddings[i], embeddings[j])
                        if sim > 0.92:
                            kept[j] = False
                result.extend(m for m, k in zip(outlet_mappings, kept) if k)
            else:
                # Jaccard fallback
                kept = [True] * len(outlet_mappings)
                for i in range(len(outlet_mappings)):
                    if not kept[i]:
                        continue
                    words_i = set((outlet_mappings[i].translated_text or outlet_mappings[i].original_text).lower().split())
                    for j in range(i + 1, len(outlet_mappings)):
                        if not kept[j]:
                            continue
                        words_j = set((outlet_mappings[j].translated_text or outlet_mappings[j].original_text).lower().split())
                        intersection = words_i & words_j
                        union = words_i | words_j
                        sim = len(intersection) / len(union) if union else 0.0
                        if sim > 0.60:
                            kept[j] = False
                result.extend(m for m, k in zip(outlet_mappings, kept) if k)

        before = len(mappings)
        after  = len(result)
        if before != after:
            logger.info(f"Intra-source dedup: {before} → {after} sentences.")
        return result

    def _apply_coverage_floor(
        self,
        facts: list[FactObject],
        original_mappings: list[SentenceMapping],
    ) -> list[FactObject]:
        """
        Ensure that significant numeric claims present in the original
        sentences are not silently dropped by compression / deduplication.

        Any number that appears in the original set but is absent from
        the compressed facts is re-inserted as a FactObject flagged
        flagged_single_source=True so the LLM knows to treat it with care.

        'Significant' is defined as: value ≥ 1 000 (thousands, millions,
        billions) OR a percentage that differs from any surviving fact by
        more than 5 pp.
        """
        def _extract_numbers(text: str) -> list[str]:
            return NUMBER_PATTERN.findall(text)

        # Collect all numbers present in surviving facts
        surviving_numbers: set[str] = set()
        for f in facts:
            surviving_numbers.update(_extract_numbers(f.fact))

        # Collect all numbers in original sentences
        dropped_sentences: list[SentenceMapping] = []
        for m in original_mappings:
            text = m.translated_text or m.original_text
            nums = _extract_numbers(text)
            for n in nums:
                if n not in surviving_numbers:
                    # Check whether this is a high-value number (≥1000 or a %)
                    clean = n.replace(",", "").replace("%", "")
                    try:
                        val = float(clean)
                        is_significant = val >= 1_000 or "%" in n
                    except ValueError:
                        is_significant = False

                    if is_significant:
                        dropped_sentences.append(m)
                        surviving_numbers.add(n)  # don't add the same sentence twice
                        break

        # Re-insert dropped high-value sentences as single-source facts
        for m in dropped_sentences:
            text = m.translated_text or m.original_text
            reinstated = FactObject(
                fact=text,
                first_seen_outlet=m.outlet,
                first_seen_sentence_id=m.sentence_id,
                confirmed_by=[m.outlet],
                confirmation_count=1,
                unique_to=m.outlet,
                source_sentence_ids=[m.sentence_id],
                section_flag="Numbers & Data",
                flagged_single_source=True,
            )
            facts.append(reinstated)
            logger.info(
                f"Coverage floor: reinstated dropped numeric claim from "
                f"'{m.outlet}': {text[:80]}…"
            )

        return facts

    def _deduplicate_semantic(
        self,
        mappings: list[SentenceMapping],
    ) -> list[FactObject]:
        """Semantic deduplication using embedder cosine similarity."""
        # Embed all translated sentences
        texts = [m.translated_text or m.original_text for m in mappings]
        embeddings = self.embedder.embed_batch(texts)

        # Track which sentences have been assigned to a fact
        assigned = [False] * len(mappings)
        facts: list[FactObject] = []

        for i in range(len(mappings)):
            if assigned[i]:
                continue

            # This sentence becomes the representative for a new fact
            fact = FactObject(
                fact=texts[i],
                first_seen_outlet=mappings[i].outlet,
                first_seen_sentence_id=mappings[i].sentence_id,
                confirmed_by=[mappings[i].outlet],
                source_sentence_ids=[mappings[i].sentence_id],
            )
            assigned[i] = True

            # Find all similar sentences from OTHER outlets
            for j in range(i + 1, len(mappings)):
                if assigned[j]:
                    continue

                sim = self._cosine_similarity(embeddings[i], embeddings[j])
                if sim >= self.dedup_threshold:
                    assigned[j] = True
                    fact.source_sentence_ids.append(mappings[j].sentence_id)

                    # Same outlet → don't double-count confirmation
                    if mappings[j].outlet not in fact.confirmed_by:
                        fact.confirmed_by.append(mappings[j].outlet)

                    # Track short post confirmations separately
                    if mappings[j].content_type in ("short_post", "osint"):
                        fact.short_post_confirmations.append(mappings[j].outlet)

                elif sim >= 0.60 and mappings[j].outlet != mappings[i].outlet:
                    # Moderate similarity from different outlet → potential
                    # contradiction. The pair is below the merge threshold so
                    # it is NOT deduplicated: both sentences survive, and the
                    # disagreement is flagged here as a structured A-vs-B pair.
                    # The later detect_contradictions() pass sees mapping j as
                    # its own fact and — because this fact already lists
                    # mappings[j].outlet — only appends the reverse pair, so
                    # every disagreement is reported exactly once.
                    nature = self._conflict_nature(texts[i], texts[j])
                    if nature:
                        fact.contradicted_by.append({
                            "type": "contradiction",
                            "claim_a": {
                                "text": texts[i],
                                "source": mappings[i].outlet,
                            },
                            "claim_b": {
                                "text": texts[j],
                                "source": mappings[j].outlet,
                            },
                            "nature": nature,
                        })
                        logger.info(
                            f"Contradiction ({nature}) flagged at dedup between "
                            f"'{mappings[i].outlet}' and '{mappings[j].outlet}'"
                        )

            fact.confirmation_count = len(set(fact.confirmed_by))
            if fact.confirmation_count == 1:
                fact.unique_to = fact.first_seen_outlet

            facts.append(fact)

        logger.info(
            f"Deduplicated {len(mappings)} sentences → {len(facts)} unique facts. "
            f"Max confirmation: {max((f.confirmation_count for f in facts), default=0)}"
        )
        return facts

    def _deduplicate_textual(
        self,
        mappings: list[SentenceMapping],
    ) -> list[FactObject]:
        """
        Fallback deduplication using simple text overlap when no embedder
        is available. Uses word-level Jaccard similarity.
        """
        assigned = [False] * len(mappings)
        facts: list[FactObject] = []
        threshold = 0.45  # word Jaccard threshold

        for i in range(len(mappings)):
            if assigned[i]:
                continue

            text_i = (mappings[i].translated_text or mappings[i].original_text).lower()
            words_i = set(text_i.split())

            fact = FactObject(
                fact=mappings[i].translated_text or mappings[i].original_text,
                first_seen_outlet=mappings[i].outlet,
                first_seen_sentence_id=mappings[i].sentence_id,
                confirmed_by=[mappings[i].outlet],
                source_sentence_ids=[mappings[i].sentence_id],
            )
            assigned[i] = True

            for j in range(i + 1, len(mappings)):
                if assigned[j]:
                    continue

                text_j = (mappings[j].translated_text or mappings[j].original_text).lower()
                words_j = set(text_j.split())

                # Jaccard similarity
                intersection = words_i & words_j
                union = words_i | words_j
                sim = len(intersection) / len(union) if union else 0.0

                if sim >= threshold:
                    assigned[j] = True
                    fact.source_sentence_ids.append(mappings[j].sentence_id)
                    if mappings[j].outlet not in fact.confirmed_by:
                        fact.confirmed_by.append(mappings[j].outlet)
                    if mappings[j].content_type in ("short_post", "osint"):
                        fact.short_post_confirmations.append(mappings[j].outlet)

            fact.confirmation_count = len(set(fact.confirmed_by))
            if fact.confirmation_count == 1:
                fact.unique_to = fact.first_seen_outlet

            facts.append(fact)

        logger.info(
            f"Deduplicated (textual) {len(mappings)} → {len(facts)} facts."
        )
        return facts

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    # ─── Contradiction detection ─────────────────────────────────────────────

    def detect_contradictions(
        self,
        facts: list[FactObject],
        all_mappings: list[SentenceMapping],
    ) -> list[FactObject]:
        """
        Detect contradictions between fact objects from different outlets.

        Paraphrase filter (applied BEFORE flagging):
          If two facts share the same core numeric triple AND their embedding
          similarity is ≥ 0.88, they are paraphrases — merge confirmation,
          do NOT flag as contradictions.

        Contradiction pairs are stored as structured A-vs-B dicts:
            {
              "type": "contradiction",
              "claim_a": {"text": str, "source": str},
              "claim_b": {"text": str, "source": str},
              "nature": "mechanism_conflict" | "numeric_conflict" | "negation_conflict"
            }
        """
        negation_words = {
            "not", "no", "never", "deny", "denied", "denies",
            "reject", "rejected", "refuses", "false", "untrue",
            "contrary", "dispute", "disputed", "contradicts",
        }

        # Pre-compute embeddings for paraphrase check (reuse if embedder available)
        embeddings: list | None = None
        if self.embedder is not None:
            try:
                embeddings = self.embedder.embed_batch([f.fact for f in facts])
            except Exception:
                embeddings = None

        for i, fact_a in enumerate(facts):
            text_a_lower = fact_a.fact.lower()
            words_a = set(text_a_lower.split())

            for j in range(i + 1, len(facts)):
                fact_b = facts[j]

                # Skip same-outlet pairs
                if fact_a.first_seen_outlet == fact_b.first_seen_outlet:
                    continue

                text_b_lower = fact_b.fact.lower()
                words_b = set(text_b_lower.split())

                shared_words = words_a & words_b
                if len(shared_words) < 8:
                    continue

                # ── Paraphrase filter ──────────────────────────────────────
                # If facts share the same key numbers AND are semantically
                # very close, they are paraphrases — merge and skip.
                nums_a = set(re.findall(r'\d+(?:[,.]\d+)?%?', text_a_lower))
                nums_b = set(re.findall(r'\d+(?:[,.]\d+)?%?', text_b_lower))
                shared_nums = nums_a & nums_b

                sem_sim = 0.0
                if embeddings is not None:
                    sem_sim = self._cosine_similarity(embeddings[i], embeddings[j])

                # Same numbers + high similarity → paraphrase, merge confirmation
                if shared_nums and sem_sim >= 0.88:
                    if fact_b.first_seen_outlet not in fact_a.confirmed_by:
                        fact_a.confirmed_by.append(fact_b.first_seen_outlet)
                        fact_a.confirmation_count = len(set(fact_a.confirmed_by))
                        fact_a.unique_to = None  # now confirmed by multiple outlets
                    logger.debug(
                        f"Paraphrase merged: '{fact_a.first_seen_outlet}' ≈ "
                        f"'{fact_b.first_seen_outlet}' (sim={sem_sim:.2f}, "
                        f"shared_nums={shared_nums})"
                    )
                    continue  # Not a contradiction
                # ── End paraphrase filter ──────────────────────────────────

                neg_a = words_a & negation_words
                neg_b = words_b & negation_words

                has_numeric = self._has_opposing_numbers(text_a_lower, text_b_lower)
                has_mutual_negation = bool(neg_a) and bool(neg_b)

                # Extra guard: if semantic similarity is very high, the numeric
                # difference is likely a detail (e.g. baseline year), not conflict
                if has_numeric and sem_sim >= 0.82:
                    continue

                if has_numeric:
                    nature = "numeric_conflict"
                elif has_mutual_negation:
                    nature = "negation_conflict"
                else:
                    continue

                pair_a = {
                    "type": "contradiction",
                    "claim_a": {"text": fact_a.fact, "source": fact_a.first_seen_outlet},
                    "claim_b": {"text": fact_b.fact, "source": fact_b.first_seen_outlet},
                    "nature": nature,
                }
                pair_b = {
                    "type": "contradiction",
                    "claim_a": {"text": fact_b.fact, "source": fact_b.first_seen_outlet},
                    "claim_b": {"text": fact_a.fact, "source": fact_a.first_seen_outlet},
                    "nature": nature,
                }

                existing_outlets_a = {c.get("outlet") or c.get("claim_b", {}).get("source")
                                      for c in fact_a.contradicted_by}
                if fact_b.first_seen_outlet not in existing_outlets_a:
                    fact_a.contradicted_by.append(pair_a)

                existing_outlets_b = {c.get("outlet") or c.get("claim_b", {}).get("source")
                                      for c in fact_b.contradicted_by}
                if fact_a.first_seen_outlet not in existing_outlets_b:
                    fact_b.contradicted_by.append(pair_b)

                logger.info(
                    f"Contradiction ({nature}) detected between "
                    f"'{fact_a.first_seen_outlet}' and '{fact_b.first_seen_outlet}'"
                )

        return facts

    @staticmethod
    def _conflict_nature(text_a: str, text_b: str) -> str | None:
        """
        Classify a moderate-similarity cross-outlet pair as a contradiction.

        Returns "numeric_conflict" when the first numbers diverge by >50%,
        "negation_conflict" when both sides use inversion language, otherwise
        None (no disagreement — the pair is just similar reporting).
        """
        if Compressor._has_opposing_numbers(text_a, text_b):
            return "numeric_conflict"
        lowered_a = text_a.lower().split()
        lowered_b = text_b.lower().split()
        neg_a = any(word in NEGATION_WORDS for word in lowered_a)
        neg_b = any(word in NEGATION_WORDS for word in lowered_b)
        if neg_a and neg_b:
            return "negation_conflict"
        return None

    @staticmethod
    def _has_opposing_numbers(text_a: str, text_b: str) -> bool:
        """Check if two texts have explicitly opposing numeric claims."""
        import re
        numbers_a = re.findall(r'\d+(?:,\d{3})*(?:\.\d+)?', text_a)
        numbers_b = re.findall(r'\d+(?:,\d{3})*(?:\.\d+)?', text_b)
        
        # If both have numbers and they're significantly different (>50% diff), flag as potential contradiction
        if numbers_a and numbers_b:
            try:
                val_a = float(numbers_a[0].replace(',', ''))
                val_b = float(numbers_b[0].replace(',', ''))
                if val_a > 0 and val_b > 0:
                    ratio = max(val_a, val_b) / min(val_a, val_b)
                    return ratio > 1.5  # More than 50% difference
            except (ValueError, ZeroDivisionError):
                pass
        
        return False