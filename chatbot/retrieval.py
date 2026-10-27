"""Passage retrieval: PostgreSQL full-text search over approved documents only."""

import re
from functools import reduce
from operator import or_

from django.contrib.postgres.search import SearchQuery, SearchRank

from .models import SEARCH_CONFIG, Document, DocumentPassage, passage_search_vector

DEFAULT_LIMIT = 8
# ts_rank for a single matching term in a typical passage is ~0.06; this only
# drops incidental matches. Tune once the real corpus is loaded.
MIN_RANK = 0.02
MAX_TERMS = 16

# Keeps statute-style tokens such as "1031", "1.1031(k)-1" and "721" intact enough
# for the english parser, which splits them consistently on both sides.
_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.()'-]*")


def build_query(text):
    """OR together each term so long natural-language questions still match.

    websearch/plain queries AND every term, which rarely matches a whole
    question; ranking rewards passages that match more of the terms. The
    english configuration drops stop words.
    """
    terms = list(dict.fromkeys(t.lower() for t in _TERM_RE.findall(text)))[:MAX_TERMS]
    if not terms:
        return None
    return reduce(or_, (SearchQuery(term, search_type='plain', config=SEARCH_CONFIG) for term in terms))


def search(text, limit=DEFAULT_LIMIT):
    """Return up to `limit` approved passages ranked by relevance to `text`."""
    query = build_query(text)
    if query is None:
        return []
    return list(
        DocumentPassage.objects
        .filter(document__status=Document.Status.APPROVED)
        # Same expression as the GIN index, so the match can use it.
        .annotate(search=passage_search_vector())
        .filter(search=query)
        .annotate(rank=SearchRank(passage_search_vector(), query))
        .filter(rank__gte=MIN_RANK)
        .select_related('document')
        .order_by('-rank', 'document_id', 'chunk_index')[:limit]
    )
