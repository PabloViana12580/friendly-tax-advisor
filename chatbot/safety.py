"""Safety controls: fixed responses, the advice fallback rule, and citation checks.

These run in code, not in the model, so they hold even when the model misbehaves.
"""

import re

DISCLAIMER = 'For general educational purposes only; this is not tax, legal, or investment advice.'

DECLINE_ADVICE = (
    'I can’t advise on a specific person’s situation, say whether a particular transaction '
    'qualifies, or recommend an investment. Please consult a qualified tax, legal, or '
    'investment professional for that.'
)
DECLINE_GENERAL_INTRO = 'Here is general information from the approved sources:'
OUT_OF_SCOPE = (
    'I only answer general questions about 1031 exchanges, Delaware Statutory Trusts, '
    '721 exchanges, and REITs, using the approved source set.'
)
NOT_FOUND = (
    'The approved sources don’t cover this, so I can’t answer it. Try rephrasing, or ask '
    'about 1031 exchanges, Delaware Statutory Trusts, 721 exchanges, or REITs.'
)
UNAVAILABLE = 'The assistant is busy right now. Wait a minute and ask again.'

# Used only when the model can't classify the question (e.g. rate limited).
_ADVICE_PATTERNS = re.compile(
    r"\b("
    r"should (i|we|my client|they|he|she)"
    r"|my client'?s?"
    r"|for me\b|my (property|gain|taxes|situation|portfolio|investment|money)"
    r"|do you recommend|would you recommend|recommend(ed)? (a|an|which|that)"
    r"|is it a good (idea|investment)|worth (it|investing)"
    r"|how much (should|would) (i|we|my)"
    r"|does my|will my|can i (deduct|defer|avoid)"
    r")",
    re.IGNORECASE,
)


def looks_like_advice_request(text):
    return bool(_ADVICE_PATTERNS.search(text))


_MARKER_RE = re.compile(r'\[\s*(P\d+(?:\s*,\s*P\d+)*)\s*\]')


def validate_citations(answer, cited, allowed_labels):
    """Keep only citations to passages we actually sent; renumber them 1..n.

    Returns (text, labels): the answer with [P#] markers rewritten as [n] (invalid
    markers removed), and the cited labels in display order. An empty label list
    means the answer is unsupported and must not be shown.
    """
    allowed = set(allowed_labels)
    order = []

    def number_for(label):
        if label not in order:
            order.append(label)
        return order.index(label) + 1

    def rewrite(match):
        labels = [part.strip() for part in match.group(1).split(',')]
        numbers = [number_for(label) for label in labels if label in allowed]
        return ''.join(f'[{n}]' for n in numbers)

    text = _MARKER_RE.sub(rewrite, answer)
    for label in cited:
        if label in allowed:
            number_for(label)
    # Attach markers to the preceding word and tidy spaces left by removed markers.
    text = re.sub(r'[ \t]+(\[\d+\])', r'\1', text)
    text = re.sub(r'(\[\d+\])[ \t]*,[ \t]*(?=\[\d+\])', r'\1', text)  # "[2], [1]" -> "[2][1]"
    text = re.sub(r'[ \t]+([.,;:])', r'\1', re.sub(r'[ \t]{2,}', ' ', text))
    text = '\n'.join(line.strip() for line in text.split('\n')).strip()
    return text, order
