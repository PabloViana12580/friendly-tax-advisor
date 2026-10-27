import re

from django import template
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()

_MARKER_RE = re.compile(r'\[(\d+)\]')


@register.simple_tag
def render_answer(exchange):
    """Render answer text as paragraphs with [n] markers linked to its sources.

    The text comes from the model, so it is escaped before any markup is added;
    only markers that match a saved citation become links.
    """
    numbers = {c.number for c in exchange.citations.all()}

    def link(match):
        number = int(match.group(1))
        if number not in numbers:
            return ''
        return format_html(
            '<sup class="sb-cite"><a href="#source-{}-{}" aria-label="Source {}">{}</a></sup>',
            exchange.pk, number, number, number,
        )

    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', exchange.answer) if p.strip()]
    html = ''.join(
        '<p>{}</p>'.format(_MARKER_RE.sub(link, escape(p)).replace('\n', '<br>'))
        for p in paragraphs
    )
    return mark_safe(html)
