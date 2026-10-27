"""Tests for the chat pipeline. The LLM is always faked; no network calls."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import retrieval, safety
from .answering import answer_question
from .audit import record_exchange
from .llm import GroundedAnswer, LLMUnavailable, Triage
from .models import ChatExchange, Conversation, Document, DocumentPassage

User = get_user_model()
PASSWORD = 'correct-Horse-battery-42'


class FakeLLM:
    answer_model = 'fake-answer-model'

    def __init__(self, triage=None, answer=None, triage_error=False, answer_error=False):
        self._triage = triage
        self._answer = answer
        self.triage_error = triage_error
        self.answer_error = answer_error
        self.triage_calls = []
        self.answer_calls = []

    def triage(self, question, history):
        self.triage_calls.append((question, history))
        if self.triage_error:
            raise LLMUnavailable('rate limited')
        return self._triage or Triage(category='educational', standalone_query=question)

    def answer(self, question, labelled):
        self.answer_calls.append((question, [label for label, _ in labelled]))
        if self.answer_error:
            raise LLMUnavailable('rate limited')
        return self._answer


def make_corpus():
    approved = Document.objects.create(
        title='Instructions for Form 8824', publisher='IRS', status=Document.Status.APPROVED,
        source_url='https://www.irs.gov/instructions/i8824',
    )
    DocumentPassage.objects.create(
        document=approved, chunk_index=0, section_heading='Deferred exchanges', page_number=3,
        content='In a deferred exchange the replacement property must be identified within 45 days '
                'and received within 180 days after the relinquished property is transferred.',
    )
    DocumentPassage.objects.create(
        document=approved, chunk_index=1,
        content='Like-kind property is real property held for productive use in a trade or business '
                'or for investment.',
    )
    draft = Document.objects.create(
        title='Draft explainer', publisher='Example', status=Document.Status.DRAFT,
        source_url='https://example.com/draft',
    )
    DocumentPassage.objects.create(
        document=draft, chunk_index=0,
        content='Replacement property identification deadlines are flexible.',
    )
    return approved


class CitationValidationTests(SimpleTestCase):
    def test_valid_markers_are_renumbered_in_order_of_use(self):
        text, labels = safety.validate_citations('A [P3]. B [P1]. C [P3].', [], ['P1', 'P2', 'P3'])
        self.assertEqual(text, 'A[1]. B[2]. C[1].')
        self.assertEqual(labels, ['P3', 'P1'])

    def test_invented_labels_are_removed(self):
        text, labels = safety.validate_citations('A [P9]. B [P1, P7].', ['P8'], ['P1', 'P2'])
        self.assertEqual(text, 'A. B[1].')
        self.assertEqual(labels, ['P1'])

    def test_answer_without_valid_citations_has_no_labels(self):
        self.assertEqual(safety.validate_citations('Unsupported claim.', [], ['P1'])[1], [])

    def test_cited_labels_without_markers_still_count(self):
        self.assertEqual(safety.validate_citations('Text.', ['P2'], ['P1', 'P2'])[1], ['P2'])

    def test_advice_keyword_fallback(self):
        self.assertTrue(safety.looks_like_advice_request('Should my client sell now and buy a DST?'))
        self.assertTrue(safety.looks_like_advice_request('Do you recommend a 721 exchange?'))
        self.assertFalse(safety.looks_like_advice_request('What is a Delaware Statutory Trust?'))


class RetrievalTests(TestCase):
    def setUp(self):
        make_corpus()

    def test_only_approved_documents_are_searched(self):
        results = retrieval.search('replacement property identification deadlines')
        self.assertTrue(results)
        self.assertTrue(all(p.document.status == Document.Status.APPROVED for p in results))

    def test_natural_language_question_matches_and_ranks(self):
        results = retrieval.search('How many days do I have to identify replacement property in a deferred exchange?')
        self.assertEqual(results[0].chunk_index, 0)

    def test_no_match_returns_empty(self):
        self.assertEqual(retrieval.search('cryptocurrency staking rewards'), [])
        self.assertEqual(retrieval.search('   '), [])

    def test_stop_words_only_returns_empty(self):
        self.assertEqual(retrieval.search('what is the'), [])


class PipelineTests(TestCase):
    def setUp(self):
        make_corpus()

    def test_answered_with_valid_citations(self):
        llm = FakeLLM(answer=GroundedAnswer(status='answered', answer='Identify within 45 days [P1].', cited=['P1']))
        result = answer_question('When must replacement property be identified?', llm=llm)
        self.assertEqual(result.outcome, ChatExchange.Outcome.ANSWERED)
        self.assertEqual(result.answer, 'Identify within 45 days[1].')
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0].document.title, 'Instructions for Form 8824')
        self.assertEqual(result.disclaimer, safety.DISCLAIMER)
        self.assertEqual(result.model, 'fake-answer-model')

    def test_answer_without_valid_citation_becomes_not_found(self):
        llm = FakeLLM(answer=GroundedAnswer(status='answered', answer='Made up [P42].', cited=['P42']))
        result = answer_question('When must replacement property be identified?', llm=llm)
        self.assertEqual(result.outcome, ChatExchange.Outcome.NOT_FOUND)
        self.assertEqual(result.answer, safety.NOT_FOUND)
        self.assertEqual(result.sources, [])

    def test_model_not_found_is_respected(self):
        llm = FakeLLM(answer=GroundedAnswer(status='not_found'))
        result = answer_question('When must replacement property be identified?', llm=llm)
        self.assertEqual(result.outcome, ChatExchange.Outcome.NOT_FOUND)

    def test_no_passages_skips_answer_call(self):
        llm = FakeLLM()
        result = answer_question('How are cryptocurrency staking rewards taxed?', llm=llm)
        self.assertEqual(result.outcome, ChatExchange.Outcome.NOT_FOUND)
        self.assertEqual(llm.answer_calls, [])

    def test_out_of_scope_skips_retrieval_and_answer(self):
        llm = FakeLLM(triage=Triage(category='out_of_scope', standalone_query='weather'))
        result = answer_question("What's the weather tomorrow?", llm=llm)
        self.assertEqual(result.outcome, ChatExchange.Outcome.OUT_OF_SCOPE)
        self.assertEqual(result.answer, safety.OUT_OF_SCOPE)
        self.assertEqual(llm.answer_calls, [])

    def test_advice_declines_then_answers_general_question(self):
        llm = FakeLLM(
            triage=Triage(category='personalized_advice', standalone_query='x',
                          general_question='How long is the replacement property identification period?'),
            answer=GroundedAnswer(status='answered', answer='45 days [P1].', cited=['P1']),
        )
        result = answer_question('Should my client use a DST for her $2M gain?', llm=llm)
        self.assertEqual(result.outcome, ChatExchange.Outcome.DECLINED_ADVICE)
        self.assertTrue(result.answer.startswith(safety.DECLINE_ADVICE))
        self.assertIn(safety.DECLINE_GENERAL_INTRO, result.answer)
        self.assertIn('45 days[1].', result.answer)
        self.assertEqual(llm.answer_calls[0][0], 'How long is the replacement property identification period?')

    def test_advice_without_supporting_sources_still_declines(self):
        llm = FakeLLM(triage=Triage(category='personalized_advice', standalone_query='x',
                                    general_question='crypto staking'))
        result = answer_question('Should I stake my crypto?', llm=llm)
        self.assertEqual(result.outcome, ChatExchange.Outcome.DECLINED_ADVICE)
        self.assertTrue(result.answer.startswith(safety.DECLINE_ADVICE))
        self.assertIn(safety.NOT_FOUND, result.answer)

    def test_triage_failure_falls_back_to_keyword_rule(self):
        llm = FakeLLM(triage_error=True, answer=GroundedAnswer(status='answered', answer='45 days [P1].', cited=['P1']))
        result = answer_question('Should my client identify replacement property within 45 days?', llm=llm)
        self.assertEqual(result.category, ChatExchange.Category.PERSONALIZED_ADVICE)
        self.assertEqual(result.outcome, ChatExchange.Outcome.DECLINED_ADVICE)

    def test_answer_failure_returns_busy_message(self):
        llm = FakeLLM(answer_error=True)
        result = answer_question('What is the identification period for replacement property?', llm=llm)
        self.assertEqual(result.outcome, ChatExchange.Outcome.ERROR)
        self.assertEqual(result.answer, safety.UNAVAILABLE)

    @override_settings(GEMINI_API_KEY='')
    def test_missing_api_key_returns_busy_message(self):
        with mock.patch('chatbot.llm._client', None):
            result = answer_question('What is a DST?')
        self.assertEqual(result.outcome, ChatExchange.Outcome.ERROR)

    def test_follow_up_receives_conversation_history(self):
        user = User.objects.create_user('advisor', 'a@example.com', PASSWORD)
        first = FakeLLM(answer=GroundedAnswer(status='answered', answer='45 days [P1].', cited=['P1']))
        exchange = record_exchange(user, None, answer_question('When must replacement property be identified?', llm=first))
        second = FakeLLM(answer=GroundedAnswer(status='not_found'))
        answer_question('And the exchange period?', conversation=exchange.conversation, llm=second)
        history = second.triage_calls[0][1]
        self.assertEqual(history, [('When must replacement property be identified?', '45 days[1].')])


class AuditTests(TestCase):
    def test_exchange_and_citation_snapshot_are_recorded(self):
        make_corpus()
        user = User.objects.create_user('advisor', 'a@example.com', PASSWORD)
        llm = FakeLLM(answer=GroundedAnswer(status='answered', answer='45 days [P1].', cited=['P1']))
        exchange = record_exchange(user, None, answer_question('When must replacement property be identified?', llm=llm))
        self.assertEqual(exchange.conversation.title, 'When must replacement property be identified?')
        self.assertEqual(exchange.disclaimer, safety.DISCLAIMER)
        citation = exchange.citations.get()
        self.assertIn(str(citation.passage_id), exchange.retrieved_passage_ids)
        self.assertEqual((citation.number, citation.page_number), (1, 3))
        self.assertEqual(citation.source_url, 'https://www.irs.gov/instructions/i8824')
        self.assertIn('45 days', citation.cited_text)
        # The snapshot survives deletion of the passage.
        citation.passage.delete()
        citation.refresh_from_db()
        self.assertIsNone(citation.passage)
        self.assertIn('45 days', citation.cited_text)

    def test_long_question_title_is_truncated(self):
        user = User.objects.create_user('advisor', 'a@example.com', PASSWORD)
        result = answer_question('x' * 300, llm=FakeLLM(triage=Triage(category='out_of_scope', standalone_query='x')))
        exchange = record_exchange(user, None, result)
        self.assertEqual(len(exchange.conversation.title), 80)


@override_settings(CHAT_RATE_LIMIT_PER_MINUTE=3)
class AskViewTests(TestCase):
    def setUp(self):
        cache.clear()
        make_corpus()
        self.user = User.objects.create_user('advisor', 'a@example.com', PASSWORD)
        self.other = User.objects.create_user('other', 'o@example.com', PASSWORD)
        self.url = reverse('chatbot:ask')
        self.llm = FakeLLM(answer=GroundedAnswer(status='answered', answer='Within 45 days [P1].', cited=['P1']))
        patcher = mock.patch('chatbot.answering.get_llm', return_value=self.llm)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, payload, client=None):
        return (client or self.client).post(self.url, json.dumps(payload), content_type='application/json')

    def test_requires_login(self):
        response = self.post({'question': 'What is a DST?'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ChatExchange.objects.count(), 0)

    def test_get_not_allowed(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_requires_csrf_token(self):
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(self.post({'question': 'What is a DST?'}, client).status_code, 403)

    def test_answer_is_logged_and_rendered(self):
        self.client.force_login(self.user)
        response = self.post({'question': 'When must replacement property be identified?'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        exchange = ChatExchange.objects.get()
        self.assertEqual(data['conversation_id'], str(exchange.conversation_id))
        self.assertEqual(data['outcome'], 'answered')
        self.assertEqual(exchange.user, self.user)
        self.assertIn('<sup class="sb-cite"><a href="#source-', data['html'])
        self.assertIn('Instructions for Form 8824', data['html'])
        self.assertIn(safety.DISCLAIMER, data['html'])

    def test_follow_up_joins_existing_conversation(self):
        self.client.force_login(self.user)
        first = self.post({'question': 'When must replacement property be identified?'}).json()
        second = self.post({'question': 'And the exchange period?', 'conversation_id': first['conversation_id']}).json()
        self.assertEqual(first['conversation_id'], second['conversation_id'])
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(ChatExchange.objects.count(), 2)

    def test_cannot_post_into_someone_elses_conversation(self):
        self.client.force_login(self.other)
        theirs = self.post({'question': 'What is a DST?'}).json()['conversation_id']
        self.client.force_login(self.user)
        self.assertEqual(self.post({'question': 'Hi', 'conversation_id': theirs}).status_code, 404)
        self.assertEqual(self.post({'question': 'Hi', 'conversation_id': 'not-a-uuid'}).status_code, 400)

    def test_rejects_empty_long_and_malformed_input(self):
        self.client.force_login(self.user)
        self.assertEqual(self.post({'question': '   '}).status_code, 400)
        self.assertEqual(self.post({'question': 'x' * 2001}).status_code, 400)
        self.assertEqual(self.client.post(self.url, 'not json', content_type='application/json').status_code, 400)
        self.assertEqual(self.post(['a list']).status_code, 400)
        self.assertEqual(ChatExchange.objects.count(), 0)

    def test_per_user_rate_limit(self):
        self.client.force_login(self.user)
        for _ in range(3):
            self.assertEqual(self.post({'question': 'What is a DST?'}).status_code, 200)
        self.assertEqual(self.post({'question': 'What is a DST?'}).status_code, 429)
        self.client.force_login(self.other)
        self.assertEqual(self.post({'question': 'What is a DST?'}).status_code, 200)

    def test_errors_are_still_logged(self):
        self.llm.answer_error = True
        self.client.force_login(self.user)
        data = self.post({'question': 'When must replacement property be identified?'}).json()
        self.assertEqual(data['outcome'], 'error')
        self.assertEqual(ChatExchange.objects.get().outcome, ChatExchange.Outcome.ERROR)


class ConversationViewTests(TestCase):
    def setUp(self):
        make_corpus()
        self.user = User.objects.create_user('advisor', 'a@example.com', PASSWORD)
        llm = FakeLLM(answer=GroundedAnswer(status='answered', answer='Within 45 days [P1].', cited=['P1']))
        self.exchange = record_exchange(self.user, None, answer_question('When must replacement property be identified?', llm=llm))
        self.url = reverse('chatbot:conversation', args=[self.exchange.conversation_id])

    def test_owner_sees_saved_thread_and_sidebar(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, 'Within 45 days')
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, f'data-conversation-id="{self.exchange.conversation_id}"')

    def test_other_users_get_404(self):
        self.client.force_login(User.objects.create_user('other', 'o@example.com', PASSWORD))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)


class RenderAnswerTests(TestCase):
    def test_model_text_is_escaped_and_only_real_citations_link(self):
        make_corpus()
        user = User.objects.create_user('advisor', 'a@example.com', PASSWORD)
        llm = FakeLLM(answer=GroundedAnswer(
            status='answered', answer='<script>alert(1)</script> Rule [P1].\n\nSecond paragraph.', cited=['P1'],
        ))
        exchange = record_exchange(user, None, answer_question('replacement property identified', llm=llm))
        exchange.answer += ' Stray [7].'
        html = Template('{% load chatbot_tags %}{% render_answer exchange %}').render(Context({'exchange': exchange}))
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertEqual(html.count('<sup class="sb-cite">'), 1)
        self.assertEqual(html.count('<p>'), 2)


class ChatAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', PASSWORD)
        self.regular = User.objects.create_user('advisor', 'a@example.com', PASSWORD)
        result = answer_question('weather?', llm=FakeLLM(triage=Triage(category='out_of_scope', standalone_query='weather')))
        self.exchange = record_exchange(self.regular, None, result)

    def test_admin_can_list_and_view_but_not_edit(self):
        self.client.force_login(self.admin)
        changelist = reverse('admin:chatbot_chatexchange_changelist')
        response = self.client.get(changelist)
        self.assertContains(response, 'weather?')
        detail = self.client.get(reverse('admin:chatbot_chatexchange_change', args=[self.exchange.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, 'name="_save"')
        self.assertEqual(self.client.get(reverse('admin:chatbot_chatexchange_add')).status_code, 403)
        delete_url = reverse('admin:chatbot_chatexchange_delete', args=[self.exchange.pk])
        self.assertEqual(self.client.post(delete_url, {'post': 'yes'}).status_code, 403)
        self.assertTrue(ChatExchange.objects.filter(pk=self.exchange.pk).exists())

    def test_regular_users_cannot_view_log(self):
        self.client.force_login(self.regular)
        response = self.client.get(reverse('admin:chatbot_chatexchange_changelist'))
        self.assertEqual(response.status_code, 302)
