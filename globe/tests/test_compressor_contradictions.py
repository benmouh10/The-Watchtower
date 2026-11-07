"""Roadmap item 9: contradiction detection in the offline compressor."""
from pipeline.compressor import Compressor
from pipeline.data_models import SentenceMapping


class BrokenEmbedder:
    """An embedder that is unusable — detect_contradictions must degrade to 0.0 similarity."""

    def embed_batch(self, texts):
        raise RuntimeError("embedder unavailable")


class FakeEmbedder:
    """Returns pre-arranged vectors in the order sentences are embedded."""

    def __init__(self, vectors):
        self._vectors = list(vectors)

    def embed_batch(self, texts):
        return list(self._vectors)


def _mapping(sentence_id, text, outlet):
    return SentenceMapping(
        sentence_id=sentence_id,
        translated_text=text,
        outlet=outlet,
        content_type="article",
    )


# Cosine similarity of (1,0,0,0) and (0.7,0,0.7,0) is ≈0.707 — above the
# contradiction floor (0.60), below the merge threshold (0.80).
_CONFLICT_VECTORS = ([1, 0, 0, 0], [0.7, 0, 0.7, 0])


def test_numeric_conflict_flagged_at_dedup():
    compressor = Compressor(embedder=FakeEmbedder(_CONFLICT_VECTORS))
    mappings = [
        _mapping("s1", "The minister announced the deficit reached 200 billion dollars this year.", "Outlet A"),
        _mapping("s2", "The minister announced the deficit reached 100 billion dollars this year.", "Outlet B"),
    ]
    facts = compressor._deduplicate_semantic(mappings)
    # Not merged: both sides of the disagreement survive as separate facts.
    assert len(facts) == 2
    (pair,) = facts[0].contradicted_by
    assert pair["type"] == "contradiction"
    assert pair["nature"] == "numeric_conflict"
    assert pair["claim_a"]["source"] == "Outlet A"
    assert pair["claim_b"]["source"] == "Outlet B"
    assert "100 billion" in pair["claim_b"]["text"]


def test_negation_conflict_flagged_at_dedup():
    compressor = Compressor(embedder=FakeEmbedder(_CONFLICT_VECTORS))
    mappings = [
        _mapping("s1", "Witnesses disputed the official account of the explosion events.", "Outlet C"),
        _mapping("s2", "Witnesses denied the official account of the explosion events.", "Outlet D"),
    ]
    facts = compressor._deduplicate_semantic(mappings)
    (pair,) = facts[0].contradicted_by
    assert pair["nature"] == "negation_conflict"
    assert pair["claim_a"]["source"] == "Outlet C"
    assert pair["claim_b"]["source"] == "Outlet D"


def test_similar_reporting_without_conflict_is_not_flagged():
    compressor = Compressor(embedder=FakeEmbedder(_CONFLICT_VECTORS))
    mappings = [
        _mapping("s1", "The film festival concluded with the award ceremony last night.", "Outlet E"),
        _mapping("s2", "The film festival concluded with the award ceremony earlier today.", "Outlet F"),
    ]
    facts = compressor._deduplicate_semantic(mappings)
    assert len(facts) == 2
    assert facts[0].contradicted_by == []
    assert facts[1].contradicted_by == []


def test_paraphrase_merges_without_flagging():
    compressor = Compressor(embedder=FakeEmbedder(([1, 0, 0, 0], [1, 0, 0, 0])))
    mappings = [
        _mapping("s1", "Police said the protest drew 50000 people to the square yesterday.", "Outlet G"),
        _mapping("s2", "Police said the protest drew 50000 people to the square yesterday.", "Outlet H"),
    ]
    facts = compressor._deduplicate_semantic(mappings)
    assert len(facts) == 1
    assert facts[0].contradicted_by == []
    assert sorted(facts[0].confirmed_by) == ["Outlet G", "Outlet H"]


def test_conflict_nature_classifier():
    assert (
        Compressor._conflict_nature(
            "Costs rose to 90 million euros.",
            "Costs rose to 30 million euros.",
        )
        == "numeric_conflict"
    )
    assert (
        Compressor._conflict_nature(
            "Officials denied the ship was in the area.",
            "Official sources disputed the ship was in the area.",
        )
        == "negation_conflict"
    )
    assert (
        Compressor._conflict_nature(
            "Rain is expected across the north.",
            "Rain is expected across parts of the east.",
        )
        is None
    )


def test_full_detector_still_produces_structured_pairs():
    compressor = Compressor(embedder=BrokenEmbedder())
    text_a = "The ministry reported a strike of 12 hours near the border crossing."
    text_b = "The ministry reported a strike of 4 hours near the border crossing."
    from pipeline.data_models import FactObject

    fact_a = FactObject(fact=text_a, first_seen_outlet="A", confirmation_count=1)
    fact_b = FactObject(fact=text_b, first_seen_outlet="B", confirmation_count=1)
    detected = compressor.detect_contradictions([fact_a, fact_b], [])
    assert detected[0].contradicted_by
    pair = detected[0].contradicted_by[0]
    assert pair["nature"] == "numeric_conflict"
    assert pair["claim_b"]["source"] == "B"