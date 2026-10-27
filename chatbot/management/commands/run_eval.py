"""Live end-to-end evaluation: runs the question set through the real chat pipeline."""

import time
import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from chatbot.answering import answer_question
from chatbot.audit import record_exchange
from chatbot.evaluation import QUESTIONS_PATH, load_cases, score, select_cases
from chatbot.llm import LLMUnavailable, get_llm


class Command(BaseCommand):
    help = (
        'Run the evaluation questions through the real pipeline (retrieval + Gemini) and '
        'check each outcome. Calls the Gemini API: about 2 requests per question. Nothing is '
        'saved unless --record is given.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--only', nargs='+', metavar='ID', help='Run only these case ids (and their conversations).')
        parser.add_argument('--delay', type=float, default=5.0, help='Seconds between questions (free-tier rate limits).')
        parser.add_argument('--record', action='store_true', help='Keep the exchanges in the admin log.')
        parser.add_argument('--user', help='Username that owns the exchanges when --record is given.')
        parser.add_argument('--questions', default=str(QUESTIONS_PATH), help='Path to the question set (JSON).')

    def handle(self, *args, **options):
        try:
            cases = select_cases(load_cases(options['questions']), options['only'])
        except (OSError, ValueError) as exc:
            raise CommandError(f'Could not load questions: {exc}')
        if options['record'] and not options['user']:
            raise CommandError('--record needs --user USERNAME to own the saved exchanges.')
        try:
            llm = get_llm()
        except LLMUnavailable as exc:
            raise CommandError(f'Gemini is not configured: {exc}')

        verbose = options['verbosity'] >= 2
        failures = 0
        # One transaction: follow-up questions get real conversation history, and
        # everything is rolled back at the end unless --record is given.
        with transaction.atomic():
            user = self.get_user(options)
            conversations = {}
            for position, case in enumerate(cases):
                if position and options['delay']:
                    time.sleep(options['delay'])
                key = case.get('conversation')
                result = answer_question(case['question'], conversations.get(key), llm=llm)
                exchange = record_exchange(user, conversations.get(key), result)
                if key:
                    conversations[key] = exchange.conversation
                passed, note = score(case, result)
                failures += not passed
                self.report(case, result, passed, note, verbose)
            if not options['record']:
                transaction.set_rollback(True)

        total = len(cases)
        saved = 'saved to the admin log' if options['record'] else 'nothing saved'
        summary = f'{total - failures} of {total} passed ({saved}).'
        if failures:
            raise CommandError(summary)
        self.stdout.write(self.style.SUCCESS(summary))

    def get_user(self, options):
        User = get_user_model()
        if options['record']:
            try:
                return User.objects.get(username=options['user'])
            except User.DoesNotExist:
                raise CommandError(f'No user named {options["user"]!r}.')
        # Rolled back with everything else.
        return User.objects.create_user(f'eval-{uuid.uuid4().hex[:12]}')

    def report(self, case, result, passed, note, verbose):
        label = self.style.SUCCESS('PASS') if passed else self.style.ERROR('FAIL')
        self.stdout.write(
            f'{label} {case["id"]:<16} expected {"/".join(case["expect"]):<28} got {result.outcome:<16} '
            f'sources {len(result.sources)}  {result.model or "-":<22} {result.latency_ms / 1000:5.1f}s  {note}'
        )
        if verbose:
            self.stdout.write(f'     Q: {case["question"]}')
            for line in result.answer.splitlines():
                if line.strip():
                    self.stdout.write(f'     A: {line}')
            for source in result.sources:
                self.stdout.write(f'     [{source.number}] {source.document.title} ({source.document.source_url})')
