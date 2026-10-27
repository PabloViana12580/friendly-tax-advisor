"""Read-only readiness check for the loaded documents and chat configuration."""

import statistics
from collections import defaultdict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from chatbot import retrieval
from chatbot.evaluation import load_cases
from chatbot.models import Document, DocumentPassage

EXAMPLES = 5


class Command(BaseCommand):
    help = (
        'Check that approved documents and passages are ready for the chat, show how retrieval '
        'ranks them, and check the chat configuration. Read-only; exits with an error status '
        'when a problem would break the chat.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--min-words', type=int, default=30, help='Warn about passages shorter than this.')
        parser.add_argument('--max-words', type=int, default=600, help='Warn about passages longer than this.')

    def handle(self, *args, **options):
        self.errors = []
        self.warnings = []
        approved = self.check_documents()
        self.check_passages(options['min_words'], options['max_words'])
        self.check_retrieval(approved)
        self.check_configuration()

        self.stdout.write('')
        for message in self.warnings:
            self.stdout.write(self.style.WARNING(f'WARNING  {message}'))
        for message in self.errors:
            self.stdout.write(self.style.ERROR(f'ERROR    {message}'))
        if self.errors:
            raise CommandError(f'{len(self.errors)} error(s), {len(self.warnings)} warning(s).')
        self.stdout.write(self.style.SUCCESS(f'Ready: no errors, {len(self.warnings)} warning(s).'))

    def section(self, title):
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n{title}'))

    def check_documents(self):
        self.section('Documents')
        counts = dict(Document.objects.order_by().values_list('status').annotate(n=Count('id')))
        for status in Document.Status.values:
            self.stdout.write(f'  {status:<9} {counts.get(status, 0)}')

        approved = list(
            Document.objects.filter(status=Document.Status.APPROVED).annotate(n=Count('passages')).order_by('title')
        )
        if not approved:
            self.errors.append("No approved documents. Only documents with status = 'approved' are searched.")
        for doc in approved:
            self.stdout.write(f'  {doc.n:>5} passages  {doc.publisher}: {doc.title}')
            if doc.n == 0:
                self.errors.append(f'Approved document has no passages: {doc.title} ({doc.source_url})')
            if doc.retrieved_at is None:
                self.warnings.append(f'retrieved_at is empty: {doc.title}')
        return approved

    def check_passages(self, min_words, max_words):
        self.section('Passages of approved documents')
        rows = DocumentPassage.objects.filter(document__status=Document.Status.APPROVED).values_list(
            'document__title', 'chunk_index', 'content',
        )
        lengths = []
        blank, short, long_ = [], [], []
        indexes = defaultdict(list)
        for title, chunk_index, content in rows:
            words = len(content.split())
            lengths.append(words)
            indexes[title].append(chunk_index)
            where = f'{title} #{chunk_index}'
            if words == 0:
                blank.append(where)
            elif words < min_words:
                short.append(f'{where} ({words} words)')
            elif words > max_words:
                long_.append(f'{where} ({words} words)')

        if not lengths:
            self.stdout.write('  none')
            return
        self.stdout.write(
            f'  {len(lengths)} passages; words per passage min {min(lengths)}, '
            f'median {statistics.median(lengths):.0f}, max {max(lengths)} (guidance: 150-400)'
        )
        if blank:
            self.errors.append(f'{len(blank)} passage(s) contain only whitespace, e.g. {", ".join(blank[:EXAMPLES])}')
        if short:
            self.warnings.append(f'{len(short)} passage(s) under {min_words} words, e.g. {", ".join(short[:EXAMPLES])}')
        if long_:
            self.warnings.append(
                f'{len(long_)} passage(s) over {max_words} words (each answer sends up to '
                f'{retrieval.DEFAULT_LIMIT}), e.g. {", ".join(long_[:EXAMPLES])}'
            )
        gaps = [title for title, found in indexes.items() if sorted(found) != list(range(len(found)))]
        if gaps:
            self.warnings.append(f'chunk_index is not 0..n-1 without gaps in: {", ".join(gaps[:EXAMPLES])}')

    def check_retrieval(self, approved):
        self.section(f'Retrieval (MIN_RANK = {retrieval.MIN_RANK} in chatbot/retrieval.py)')
        searchable = [doc for doc in approved if doc.n]
        if not searchable:
            self.stdout.write('  skipped: no approved passages')
            return

        self.stdout.write('  Each document searched by its own title:')
        for doc in searchable:
            results = retrieval.search(doc.title, min_rank=0)
            own = [p for p in results if p.document_id == doc.id]
            if own:
                kept = 'kept' if own[0].rank >= retrieval.MIN_RANK else 'DROPPED by MIN_RANK'
                self.stdout.write(f'    rank {own[0].rank:.3f} {kept:<20} {doc.title}')
            else:
                self.stdout.write(f'    not found            {doc.title}')
                self.warnings.append(f'Searching for the title does not return its own passages: {doc.title}')

        self.stdout.write('  Evaluation questions expected to be answered:')
        for case in load_cases():
            if 'answered' not in case['expect']:
                continue
            results = retrieval.search(case['question'], min_rank=0)
            kept = [p for p in results if p.rank >= retrieval.MIN_RANK]
            top = f'top {results[0].rank:.3f} {results[0].document.title}' if results else 'no matching passages'
            self.stdout.write(f'    {len(kept)} kept / {len(results)} matched  {case["id"]:<16} {top}')
            if not kept:
                self.warnings.append(
                    f'Eval question "{case["id"]}" retrieves nothing above MIN_RANK, so it would be answered '
                    '"not in the sources". Check the corpus covers it, or adjust the question or MIN_RANK.'
                )

    def check_configuration(self):
        self.section('Configuration')
        self.stdout.write(f'  DEBUG                {settings.DEBUG}')
        self.stdout.write(f'  GEMINI_API_KEY       {"set" if settings.GEMINI_API_KEY else "MISSING"}')
        self.stdout.write(f'  Answer / fallback    {settings.GEMINI_ANSWER_MODEL} / {settings.GEMINI_FALLBACK_MODEL or "none"}')
        self.stdout.write(f'  Triage model         {settings.GEMINI_TRIAGE_MODEL}')
        superusers = get_user_model().objects.filter(is_superuser=True, is_active=True).count()
        self.stdout.write(f'  Active superusers    {superusers}')
        if not settings.GEMINI_API_KEY:
            self.errors.append('GEMINI_API_KEY is not set; every question would get the "busy" response.')
        if not superusers:
            self.warnings.append('No active superuser, so nobody can view the admin log. Run createsuperuser.')
