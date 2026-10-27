"""Tests for loading data outside Django and for the readiness/evaluation commands.

Gemini is always faked; no network calls.
"""

import json
import tempfile
import uuid
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings

from . import evaluation
from .llm import LLMUnavailable, Triage
from .models import ChatExchange, Conversation, Document, DocumentPassage
from .test_chat import FakeLLM, make_corpus

User = get_user_model()


class DocumentDatabaseDefaultsTests(TestCase):
    """Rows inserted from the Supabase dashboard, SQL, or CSV need only the real data."""

    def test_sql_insert_with_required_columns_only(self):
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO chatbot_document (title, publisher, source_url) VALUES (%s, %s, %s) '
                'RETURNING id, status, created_at, updated_at',
                ['Publication 544', 'IRS', 'https://www.irs.gov/publications/p544'],
            )
            doc_id, status, created_at, updated_at = cursor.fetchone()
            cursor.execute(
                'INSERT INTO chatbot_documentpassage (document_id, chunk_index, content) VALUES (%s, 0, %s) '
                'RETURNING id, created_at',
                [doc_id, 'Like-kind property is real property held for investment.'],
            )
            passage_id, passage_created_at = cursor.fetchone()

        self.assertIsInstance(doc_id, uuid.UUID)
        self.assertIsInstance(passage_id, uuid.UUID)
        self.assertEqual(status, Document.Status.DRAFT)
        self.assertIsNotNone(created_at)
        self.assertIsNotNone(updated_at)
        self.assertIsNotNone(passage_created_at)
        self.assertEqual(Document.objects.get(pk=doc_id).passages.get().pk, passage_id)

    def test_orm_still_uses_python_defaults(self):
        document = Document(title='Pub 551', publisher='IRS', source_url='https://www.irs.gov/publications/p551')
        self.assertIsInstance(document.id, uuid.UUID)  # assigned before saving
        document.save()
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.DRAFT)


class EvaluationModuleTests(SimpleTestCase):
    def write(self, cases):
        handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        json.dump(cases, handle)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_repository_question_set_is_valid(self):
        cases = evaluation.load_cases()
        self.assertGreaterEqual(len(cases), 10)
        expected = {outcome for case in cases for outcome in case['expect']}
        self.assertTrue({'answered', 'declined_advice', 'out_of_scope'} <= expected)

    def test_rejects_duplicates_unknown_outcomes_and_missing_fields(self):
        bad_sets = [
            [{'id': 'a', 'question': 'q', 'expect': 'answered'}, {'id': 'a', 'question': 'q', 'expect': 'answered'}],
            [{'id': 'a', 'question': 'q', 'expect': 'great'}],
            [{'id': 'a', 'expect': 'answered'}],
        ]
        for cases in bad_sets:
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                evaluation.load_cases(self.write(cases))

    def test_select_keeps_whole_conversation(self):
        cases = evaluation.load_cases(self.write([
            {'id': 'a', 'question': 'q', 'expect': 'answered'},
            {'id': 'b1', 'conversation': 'c', 'question': 'q', 'expect': 'answered'},
            {'id': 'b2', 'conversation': 'c', 'question': 'q', 'expect': 'answered'},
        ]))
        self.assertEqual([c['id'] for c in evaluation.select_cases(cases, ['b2'])], ['b1', 'b2'])
        self.assertEqual(evaluation.select_cases(cases, None), cases)
        with self.assertRaises(ValueError):
            evaluation.select_cases(cases, ['missing'])

    def test_score(self):
        source = SimpleNamespace(document=SimpleNamespace(source_url='https://www.irs.gov/publications/p544'))
        answered = SimpleNamespace(outcome='answered', sources=[source])
        case = {'id': 'x', 'expect': ['answered']}
        self.assertEqual(evaluation.score(case, answered), (True, ''))
        self.assertTrue(evaluation.score({**case, 'source_contains': 'p544'}, answered)[0])
        self.assertFalse(evaluation.score({**case, 'source_contains': 'sec.gov'}, answered)[0])
        passed, note = evaluation.score(case, SimpleNamespace(outcome='error', sources=[]))
        self.assertFalse(passed)
        self.assertIn('run this case again', note)
        self.assertEqual(evaluation.score(case, SimpleNamespace(outcome='not_found', sources=[]))[1], 'expected answered')


@override_settings(GEMINI_API_KEY='test-key')
class CheckCorpusTests(TestCase):
    def run_check(self):
        out = StringIO()
        try:
            call_command('check_corpus', stdout=out, no_color=True)
        except CommandError as exc:
            return False, out.getvalue() + str(exc)
        return True, out.getvalue()

    def test_empty_database_fails(self):
        ok, output = self.run_check()
        self.assertFalse(ok)
        self.assertIn('No approved documents', output)

    def test_loaded_corpus_passes_and_reports_retrieval(self):
        make_corpus()
        User.objects.create_superuser('admin', 'admin@example.com', 'correct-Horse-battery-42')
        ok, output = self.run_check()
        self.assertTrue(ok, output)
        self.assertIn('2 passages  IRS: Instructions for Form 8824', output)
        self.assertIn('Each document searched by its own title', output)
        # Sample passages are short and retrieved_at is unset: warnings, not errors.
        self.assertIn('under 30 words', output)
        self.assertIn('retrieved_at is empty', output)

    def test_problems_that_break_the_chat_are_errors(self):
        approved = make_corpus()
        Document.objects.create(title='Empty', publisher='SEC', source_url='https://example.com/empty',
                                status=Document.Status.APPROVED)
        DocumentPassage.objects.create(document=approved, chunk_index=5, content='   \n ')
        with override_settings(GEMINI_API_KEY=''):
            ok, output = self.run_check()
        self.assertFalse(ok)
        self.assertIn('Approved document has no passages: Empty', output)
        self.assertIn('only whitespace', output)
        self.assertIn('GEMINI_API_KEY is not set', output)
        self.assertIn('chunk_index is not 0..n-1', output)
        self.assertIn('No active superuser', output)


class RunEvalTests(TestCase):
    def setUp(self):
        self.advisor = User.objects.create_user('advisor', 'a@example.com', 'correct-Horse-battery-42')
        self.llm = FakeLLM(triage=Triage(category='out_of_scope', standalone_query='x'))
        patcher = mock.patch('chatbot.management.commands.run_eval.get_llm', return_value=self.llm)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.questions = self.write([
            {'id': 'off-topic', 'question': 'Banana bread?', 'expect': 'out_of_scope'},
            {'id': 'turn-1', 'conversation': 'c', 'question': 'First?', 'expect': ['out_of_scope']},
            {'id': 'turn-2', 'conversation': 'c', 'question': 'Second?', 'expect': ['out_of_scope']},
        ])

    def write(self, cases):
        handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8')
        json.dump(cases, handle)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def run_eval(self, *args):
        out = StringIO()
        call_command('run_eval', '--questions', self.questions, '--delay', '0', *args, stdout=out, no_color=True)
        return out.getvalue()

    def test_passes_and_rolls_back_by_default(self):
        output = self.run_eval()
        self.assertIn('3 of 3 passed (nothing saved)', output)
        self.assertEqual(ChatExchange.objects.count(), 0)
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertFalse(User.objects.filter(username__startswith='eval-').exists())

    def test_follow_ups_receive_history(self):
        self.run_eval()
        history = self.llm.triage_calls[2][1]
        self.assertEqual([question for question, _ in history], ['First?'])

    def test_record_keeps_exchanges_for_the_admin_log(self):
        output = self.run_eval('--record', '--user', 'advisor')
        self.assertIn('saved to the admin log', output)
        self.assertEqual(ChatExchange.objects.filter(user=self.advisor).count(), 3)
        self.assertEqual(Conversation.objects.count(), 2)  # the two turns share one conversation

    def test_failures_exit_with_error_and_still_roll_back(self):
        self.questions = self.write([{'id': 'needs-answer', 'question': 'Q?', 'expect': 'answered'}])
        with self.assertRaisesMessage(CommandError, '0 of 1 passed'):
            self.run_eval()
        self.assertEqual(ChatExchange.objects.count(), 0)

    def test_argument_and_configuration_errors(self):
        with self.assertRaisesMessage(CommandError, '--record needs --user'):
            self.run_eval('--record')
        with self.assertRaisesMessage(CommandError, "No user named 'nobody'"):
            self.run_eval('--record', '--user', 'nobody')
        with self.assertRaisesMessage(CommandError, 'Unknown case id'):
            self.run_eval('--only', 'missing')
        with mock.patch('chatbot.management.commands.run_eval.get_llm', side_effect=LLMUnavailable('no key')):
            with self.assertRaisesMessage(CommandError, 'Gemini is not configured'):
                self.run_eval()
