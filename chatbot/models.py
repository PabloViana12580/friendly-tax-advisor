import uuid

from django.conf import settings
from django.contrib.postgres.functions import RandomUUID
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now

# Text-search configuration shared by the passage index and future retrieval
# queries. Queries must use the same expression for PostgreSQL to use the index.
SEARCH_CONFIG = 'english'


def passage_search_vector():
    """Section heading (weight A) and passage text (weight B), parsed for full-text search."""
    return (
        SearchVector('section_heading', config=SEARCH_CONFIG, weight='A')
        + SearchVector('content', config=SEARCH_CONFIG, weight='B')
    )


class Document(models.Model):
    """An approved public source that chatbot answers may cite."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        APPROVED = 'approved', 'Approved'
        ARCHIVED = 'archived', 'Archived'

    # db_default values let rows be inserted from the Supabase dashboard, SQL, or
    # CSV import; Django's Python defaults still apply to ORM saves.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=RandomUUID(), editable=False)
    title = models.CharField(max_length=500)
    publisher = models.CharField(max_length=255, help_text='Publishing organization, e.g. IRS, SEC, FINRA.')
    source_url = models.URLField(max_length=2048, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_default=Status.DRAFT)
    content_hash = models.CharField(
        max_length=64, null=True, blank=True,
        help_text='Hash of the retrieved source content, used to detect changes.',
    )
    retrieved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_default=Now())
    # SQL UPDATEs don't refresh this; set updated_at = now() when editing outside Django.
    updated_at = models.DateTimeField(auto_now=True, db_default=Now())

    class Meta:
        ordering = ['title']
        indexes = [
            models.Index(fields=['status'], name='chatbot_doc_status_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=['draft', 'approved', 'archived']),
                name='chatbot_document_status_valid',
            ),
        ]

    def __str__(self):
        return f'{self.publisher}: {self.title}'


class DocumentPassage(models.Model):
    """An extracted passage of a Document; the unit that answers cite."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=RandomUUID(), editable=False)
    # PROTECT: a document with passages cannot be deleted by accident, so
    # citations keep resolving. Retire sources by archiving them instead.
    # db_index=False: the (document, chunk_index) unique index leads with
    # document_id and already serves foreign-key lookups.
    document = models.ForeignKey(
        Document, on_delete=models.PROTECT, related_name='passages', db_index=False,
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    page_number = models.PositiveIntegerField(null=True, blank=True)
    section_heading = models.CharField(max_length=500, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_default=Now())
    # Computed by PostgreSQL on every insert and update, including SQL and CSV
    # loads, so retrieval never re-parses passage text at query time.
    search_vector = models.GeneratedField(
        expression=passage_search_vector(), output_field=SearchVectorField(), db_persist=True,
    )

    class Meta:
        ordering = ['document', 'chunk_index']
        constraints = [
            models.UniqueConstraint(
                fields=['document', 'chunk_index'],
                name='chatbot_passage_document_chunk_unique',
            ),
            models.CheckConstraint(
                condition=~Q(content=''),
                name='chatbot_passage_content_not_empty',
            ),
            models.CheckConstraint(
                condition=Q(page_number__isnull=True) | Q(page_number__gte=1),
                name='chatbot_passage_page_number_positive',
            ),
        ]
        indexes = [
            GinIndex(fields=['search_vector'], name='chatbot_passage_search_idx'),
        ]

    def __str__(self):
        return f'{self.document} (passage {self.chunk_index})'


class Conversation(models.Model):
    """A user's chat thread; groups exchanges for follow-up questions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversations', db_index=False,
    )
    title = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', '-updated_at'], name='chatbot_conv_user_updated_idx'),
        ]

    def __str__(self):
        return self.title


class ChatExchange(models.Model):
    """Audit log entry: one question and the answer shown for it."""

    class Category(models.TextChoices):
        EDUCATIONAL = 'educational', 'Educational'
        PERSONALIZED_ADVICE = 'personalized_advice', 'Personalized advice'
        OUT_OF_SCOPE = 'out_of_scope', 'Out of scope'

    class Outcome(models.TextChoices):
        ANSWERED = 'answered', 'Answered'
        NOT_FOUND = 'not_found', 'Not in sources'
        DECLINED_ADVICE = 'declined_advice', 'Declined advice'
        OUT_OF_SCOPE = 'out_of_scope', 'Out of scope'
        ERROR = 'error', 'Error'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # PROTECT: deleting a conversation or user must not silently erase the audit log.
    # db_index=False: the composite indexes below lead with these columns.
    conversation = models.ForeignKey(
        Conversation, on_delete=models.PROTECT, related_name='exchanges', db_index=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='chat_exchanges', db_index=False,
    )
    question = models.TextField()
    standalone_query = models.TextField(blank=True, help_text='Self-contained query used for retrieval.')
    category = models.CharField(max_length=32, choices=Category.choices)
    outcome = models.CharField(max_length=32, choices=Outcome.choices)
    answer = models.TextField(help_text='Answer shown to the user, with [n] citation markers.')
    disclaimer = models.TextField()
    retrieved_passage_ids = models.JSONField(default=list, blank=True)
    model = models.CharField(max_length=100, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['user', 'created_at'], name='chatbot_exch_user_created_idx'),
            models.Index(fields=['outcome'], name='chatbot_exch_outcome_idx'),
            models.Index(fields=['conversation', 'created_at'], name='chatbot_exch_conv_created_idx'),
        ]

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} {self.user}: {self.question[:60]}'


class ExchangeCitation(models.Model):
    """A passage cited in an answer, snapshotted so the log survives re-ingestion."""

    # db_index=False: the (exchange, number) unique index serves lookups.
    exchange = models.ForeignKey(ChatExchange, on_delete=models.CASCADE, related_name='citations', db_index=False)
    passage = models.ForeignKey(DocumentPassage, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    number = models.PositiveSmallIntegerField(help_text='Citation number shown in the answer.')
    document_title = models.CharField(max_length=500)
    source_url = models.URLField(max_length=2048)
    section_heading = models.CharField(max_length=500, blank=True)
    page_number = models.PositiveIntegerField(null=True, blank=True)
    cited_text = models.TextField()

    class Meta:
        ordering = ['exchange', 'number']
        constraints = [
            models.UniqueConstraint(fields=['exchange', 'number'], name='chatbot_citation_exchange_number_unique'),
        ]

    def __str__(self):
        return f'[{self.number}] {self.document_title}'
