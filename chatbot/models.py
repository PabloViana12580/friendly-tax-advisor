import uuid

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector
from django.db import models
from django.db.models import Q

# Text-search configuration shared by the passage index and future retrieval
# queries. Queries must use the same expression for PostgreSQL to use the index.
SEARCH_CONFIG = 'english'


def passage_search_vector():
    return SearchVector('content', config=SEARCH_CONFIG)


class Document(models.Model):
    """An approved public source that chatbot answers may cite."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        APPROVED = 'approved', 'Approved'
        ARCHIVED = 'archived', 'Archived'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=500)
    publisher = models.CharField(max_length=255, help_text='Publishing organization, e.g. IRS, SEC, FINRA.')
    source_url = models.URLField(max_length=2048, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    content_hash = models.CharField(
        max_length=64, null=True, blank=True,
        help_text='Hash of the retrieved source content, used to detect changes.',
    )
    retrieved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    created_at = models.DateTimeField(auto_now_add=True)

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
            GinIndex(passage_search_vector(), name='chatbot_passage_search_idx'),
        ]

    def __str__(self):
        return f'{self.document} (passage {self.chunk_index})'
