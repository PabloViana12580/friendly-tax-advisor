"""Audit logging: persist every exchange with its cited sources."""

from django.db import transaction

from .models import ChatExchange, Conversation, ExchangeCitation

TITLE_LENGTH = 80


@transaction.atomic
def record_exchange(user, conversation, result):
    """Save `result` (a ChatResult), creating the conversation on first use."""
    if conversation is None:
        title = result.question if len(result.question) <= TITLE_LENGTH else result.question[:TITLE_LENGTH - 1] + '…'
        conversation = Conversation.objects.create(user=user, title=title)
    exchange = ChatExchange.objects.create(
        conversation=conversation,
        user=user,
        question=result.question,
        standalone_query=result.standalone_query,
        category=result.category,
        outcome=result.outcome,
        answer=result.answer,
        disclaimer=result.disclaimer,
        retrieved_passage_ids=result.retrieved_ids,
        model=result.model,
        latency_ms=result.latency_ms,
    )
    ExchangeCitation.objects.bulk_create([
        ExchangeCitation(
            exchange=exchange,
            passage=source.passage,
            number=source.number,
            document_title=source.document.title,
            source_url=source.document.source_url,
            section_heading=source.passage.section_heading or '',
            page_number=source.passage.page_number,
            cited_text=source.passage.content,
        )
        for source in result.sources
    ])
    conversation.save(update_fields=['updated_at'])
    return exchange
