"""Answer pipeline: triage -> route -> retrieve -> grounded answer -> validate.

Kept free of HTTP and persistence so every outcome can be tested with a fake LLM.
"""

import time
from dataclasses import dataclass, field

from django.conf import settings

from . import retrieval, safety
from .llm import LLMUnavailable, Triage, get_llm
from .models import ChatExchange

HISTORY_TURNS = 3

Outcome = ChatExchange.Outcome
Category = ChatExchange.Category


@dataclass
class Source:
    number: int
    passage: object

    @property
    def document(self):
        return self.passage.document


@dataclass
class ChatResult:
    question: str
    category: str
    outcome: str
    answer: str
    standalone_query: str = ''
    sources: list = field(default_factory=list)
    retrieved_ids: list = field(default_factory=list)
    model: str = ''
    latency_ms: int = 0
    disclaimer: str = safety.DISCLAIMER


def _fallback_triage(question):
    category = 'personalized_advice' if safety.looks_like_advice_request(question) else 'educational'
    general = question if category == 'personalized_advice' else ''
    return Triage(category=category, standalone_query=question, general_question=general)


def _history(conversation):
    if conversation is None or conversation.pk is None:
        return []
    recent = conversation.exchanges.order_by('-created_at')[:HISTORY_TURNS]
    return [(e.question, e.answer) for e in reversed(recent)]


def answer_question(question, conversation=None, llm=None):
    started = time.monotonic()
    result = _run(question, conversation, llm)
    result.latency_ms = int((time.monotonic() - started) * 1000)
    return result


def _run(question, conversation, llm):
    try:
        llm = llm or get_llm()
    except LLMUnavailable:
        return ChatResult(question, Category.EDUCATIONAL, Outcome.ERROR, safety.UNAVAILABLE)

    try:
        triage = llm.triage(question, _history(conversation))
    except LLMUnavailable:
        triage = _fallback_triage(question)

    if triage.category == Category.OUT_OF_SCOPE:
        return ChatResult(question, triage.category, Outcome.OUT_OF_SCOPE, safety.OUT_OF_SCOPE,
                          standalone_query=triage.standalone_query)

    advice = triage.category == Category.PERSONALIZED_ADVICE
    query = (triage.general_question if advice else triage.standalone_query).strip() or question
    result = ChatResult(question, triage.category, Outcome.ANSWERED, '', standalone_query=query)

    passages = retrieval.search(query)
    result.retrieved_ids = [str(p.pk) for p in passages]

    body = None
    if passages:
        labelled = [(f'P{i}', p) for i, p in enumerate(passages, start=1)]
        try:
            grounded = llm.answer(query, labelled)
        except LLMUnavailable:
            return _finish(result, advice, outcome=Outcome.ERROR, body=safety.UNAVAILABLE)
        result.model = getattr(llm, 'answer_model', '')
        if grounded.status == 'answered':
            by_label = dict(labelled)
            text, labels = safety.validate_citations(grounded.answer, grounded.cited, by_label)
            if labels and text:
                body = text
                result.sources = [Source(n, by_label[label]) for n, label in enumerate(labels, start=1)]

    if body is None:
        return _finish(result, advice, outcome=Outcome.NOT_FOUND, body=safety.NOT_FOUND)
    return _finish(result, advice, outcome=Outcome.ANSWERED, body=body)


def _finish(result, advice, outcome, body):
    """Set the final outcome and text. Advice requests always lead with the decline."""
    if advice:
        if result.sources:
            body = f'{safety.DECLINE_GENERAL_INTRO}\n\n{body}'
        body = f'{safety.DECLINE_ADVICE}\n\n{body}'
        if outcome != Outcome.ERROR:
            outcome = Outcome.DECLINED_ADVICE
    result.outcome = outcome
    result.answer = body
    return result
