import json
import uuid

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from .answering import answer_question
from .audit import record_exchange
from .models import Conversation

MAX_QUESTION_LENGTH = 2000
SIDEBAR_CONVERSATIONS = 20

# General educational prompts; selecting one fills the message box.
SUGGESTED_QUESTIONS = [
    {'topic': '1031 exchange', 'text': 'What makes real property “like-kind” under Section 1031?'},
    {'topic': '1031 exchange', 'text': 'How do the 45-day identification and 180-day exchange periods work?'},
    {'topic': 'DST', 'text': 'How does Revenue Ruling 2004-86 treat interests in a Delaware Statutory Trust?'},
    {'topic': '721 exchange', 'text': 'What is a Section 721 contribution to a REIT operating partnership?'},
]


@login_required
@require_GET
def workspace(request, conversation_id=None):
    conversation = None
    exchanges = []
    if conversation_id is not None:
        conversation = get_object_or_404(Conversation, pk=conversation_id, user=request.user)
        exchanges = conversation.exchanges.prefetch_related('citations')
    return render(request, 'chatbot/workspace.html', {
        'conversation': conversation,
        'exchanges': exchanges,
        'conversations': request.user.conversations.all()[:SIDEBAR_CONVERSATIONS],
        'suggested_questions': SUGGESTED_QUESTIONS,
    })


def _error(message, status):
    return JsonResponse({'error': message}, status=status)


def _rate_limited(user):
    key = f'chat-rate:{user.pk}'
    cache.add(key, 0, timeout=60)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, timeout=60)
        count = 1
    return count > settings.CHAT_RATE_LIMIT_PER_MINUTE


@login_required
@require_POST
def ask(request):
    try:
        data = json.loads(request.body)
    except ValueError:
        return _error('Send the question as JSON.', 400)
    if not isinstance(data, dict):
        return _error('Send the question as JSON.', 400)

    question = str(data.get('question') or '').strip()
    if not question:
        return _error('Enter a question.', 400)
    if len(question) > MAX_QUESTION_LENGTH:
        return _error(f'Shorten the question to {MAX_QUESTION_LENGTH} characters or fewer.', 400)

    conversation = None
    if data.get('conversation_id'):
        try:
            conversation_id = uuid.UUID(str(data['conversation_id']))
        except ValueError:
            return _error('Unknown conversation.', 400)
        conversation = get_object_or_404(Conversation, pk=conversation_id, user=request.user)

    if _rate_limited(request.user):
        return _error('You’re asking faster than the assistant allows. Wait a minute and try again.', 429)

    result = answer_question(question, conversation)
    exchange = record_exchange(request.user, conversation, result)
    return JsonResponse({
        'conversation_id': str(exchange.conversation_id),
        'conversation_title': exchange.conversation.title,
        'outcome': exchange.outcome,
        'html': render_to_string('chatbot/_exchange.html', {'exchange': exchange}, request=request),
    })
