"""Passage retrieval: PostgreSQL full-text search over approved documents only."""

from django.db import connection

from .models import SEARCH_CONFIG, Document, DocumentPassage

DEFAULT_LIMIT = 8
# The rank is an IDF-weighted average of per-word ts_rank values. On the loaded
# corpus relevant passages rank 0.2-0.6 and incidental matches as low as 0.01,
# so this only drops the latter; whether passages answer the question is left
# to the answer model. Check with `manage.py check_corpus`.
MIN_RANK = 0.02
MAX_TERMS = 24
# Reciprocal rank fusion constant; small, so a top result in one list counts
# clearly more than a low one.
RRF_K = 10

# 1. words: the question parsed with the same configuration as the stored
#    search_vector (stemmed, stop words dropped); the lexemes are cast to
#    tsquery directly so they are not parsed a second time.
# 2. weights: inverse document frequency over all passages. ts_rank alone
#    weighs every word equally, so a question's common words ("exchange",
#    "property") would outrank the specific one that matters ("intermediary").
#    Words that match no passage are dropped.
# 3. Each approved passage matching any word is ranked by the weighted average
#    of its per-word ts_rank. Every @@ uses the GIN index on search_vector.
SEARCH_SQL = f"""
WITH words AS (
    SELECT quote_literal(lexeme)::tsquery AS query
    FROM unnest(tsvector_to_array(to_tsvector(%(config)s::regconfig, %(text)s))) AS lexeme
    LIMIT %(max_terms)s
),
weights AS (
    SELECT words.query,
           ln(1 + (SELECT count(*) FROM {DocumentPassage._meta.db_table})::float / count(*)) AS weight
    FROM words JOIN {DocumentPassage._meta.db_table} p ON p.search_vector @@ words.query
    GROUP BY words.query
)
SELECT p.id, sum(w.weight * ts_rank(p.search_vector, w.query)) / (SELECT sum(weight) FROM weights) AS rank
FROM {DocumentPassage._meta.db_table} p
JOIN {Document._meta.db_table} d ON d.id = p.document_id AND d.status = %(status)s
JOIN weights w ON p.search_vector @@ w.query
GROUP BY p.id
HAVING sum(w.weight * ts_rank(p.search_vector, w.query)) / (SELECT sum(weight) FROM weights) >= %(min_rank)s
ORDER BY rank DESC, p.id
LIMIT %(limit)s
"""


def search(text, limit=DEFAULT_LIMIT, min_rank=MIN_RANK):
    """Return up to `limit` approved passages ranked by relevance to `text`.

    Each passage has a `rank` attribute. `min_rank` is overridable so
    `check_corpus` can show what the cutoff drops.
    """
    if not text or not text.strip():
        return []
    with connection.cursor() as cursor:
        cursor.execute(SEARCH_SQL, {
            'config': SEARCH_CONFIG, 'text': text, 'max_terms': MAX_TERMS,
            'status': Document.Status.APPROVED, 'min_rank': min_rank, 'limit': limit,
        })
        ranks = dict(cursor.fetchall())
    passages = DocumentPassage.objects.filter(pk__in=ranks).select_related('document')
    for passage in passages:
        passage.rank = ranks[passage.pk]
    return sorted(passages, key=lambda p: (-p.rank, str(p.document_id), p.chunk_index))


def search_combined(texts, limit=DEFAULT_LIMIT):
    """Search each text separately and merge the lists with reciprocal rank fusion.

    Used with each triage keyword phrase and the full question. In one combined
    query a generic phrase ("like-kind exchange") drowns out the specific one
    ("basis of replacement property"); searched separately, each concept gets
    its own top results, and passages found by several searches rank first. The
    question keeps numbers and phrasing ("45-day", "Section 721") that keywords
    may drop.
    """
    scores, passages = {}, {}
    for text in dict.fromkeys(t.strip() for t in texts if t and t.strip()):
        for position, passage in enumerate(search(text, limit=limit)):
            scores[passage.pk] = scores.get(passage.pk, 0) + 1 / (RRF_K + position)
            passages.setdefault(passage.pk, passage)
    best = sorted(scores, key=lambda pk: (-scores[pk], -passages[pk].rank))
    return [passages[pk] for pk in best[:limit]]
