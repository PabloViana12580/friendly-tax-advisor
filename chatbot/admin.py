from django.contrib import admin
from django.db.models import Count

from .models import ChatExchange, Conversation, ExchangeCitation


class ReadOnlyAdmin(admin.ModelAdmin):
    """The chat log is an audit record: viewable by staff, never edited here."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ExchangeCitationInline(admin.TabularInline):
    model = ExchangeCitation
    fields = ['number', 'document_title', 'source_url', 'section_heading', 'page_number', 'cited_text']
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ChatExchange)
class ChatExchangeAdmin(ReadOnlyAdmin):
    list_display = ['created_at', 'user', 'outcome', 'category', 'question_excerpt', 'citation_count']
    list_filter = ['outcome', 'category', 'created_at', 'user']
    search_fields = ['question', 'answer', 'user__username']
    date_hierarchy = 'created_at'
    list_select_related = ['user']
    inlines = [ExchangeCitationInline]
    fields = [
        'created_at', 'user', 'conversation', 'category', 'outcome', 'question', 'standalone_query',
        'answer', 'disclaimer', 'retrieved_passage_ids', 'model', 'latency_ms',
    ]
    readonly_fields = fields

    @admin.display(description='Question')
    def question_excerpt(self, obj):
        return obj.question if len(obj.question) <= 90 else obj.question[:89] + '…'

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_citation_count=Count('citations'))

    @admin.display(description='Sources', ordering='_citation_count')
    def citation_count(self, obj):
        return obj._citation_count


@admin.register(Conversation)
class ConversationAdmin(ReadOnlyAdmin):
    list_display = ['title', 'user', 'created_at', 'updated_at']
    list_filter = ['user']
    search_fields = ['title', 'user__username']
    fields = ['title', 'user', 'created_at', 'updated_at']
    readonly_fields = fields
