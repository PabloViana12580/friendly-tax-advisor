from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET

# General educational prompts only; the composer is not connected to an
# assistant yet, so selecting one just fills the message box.
SUGGESTED_QUESTIONS = [
    {'topic': '1031 exchange', 'text': 'What makes real property “like-kind” under Section 1031?'},
    {'topic': '1031 exchange', 'text': 'How do the 45-day identification and 180-day exchange periods work?'},
    {'topic': 'DST', 'text': 'How does Revenue Ruling 2004-86 treat interests in a Delaware Statutory Trust?'},
    {'topic': '721 exchange', 'text': 'What is a Section 721 contribution to a REIT operating partnership?'},
]


@login_required
@require_GET
def workspace(request):
    return render(request, 'chatbot/workspace.html', {
        'suggested_questions': SUGGESTED_QUESTIONS,
    })
