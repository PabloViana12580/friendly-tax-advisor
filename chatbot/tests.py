import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.postgres.search import SearchQuery
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, models, transaction
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from config.settings import database_from_env

from .models import Document, DocumentPassage, passage_search_vector


class DatabaseConfigTests(SimpleTestCase):
    """Settings parsing only; no database connection is made."""

    def config(self, **env):
        with mock.patch.dict(os.environ, env, clear=True):
            return database_from_env()

    def test_parses_database_url_with_encoded_password(self):
        db = self.config(DATABASE_URL='postgresql://postgres.ref:p%40ss%2Fword@pooler.example.com:5432/postgres')
        self.assertEqual(db['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(db['USER'], 'postgres.ref')
        self.assertEqual(db['PASSWORD'], 'p@ss/word')
        self.assertEqual(db['HOST'], 'pooler.example.com')
        self.assertEqual(db['PORT'], '5432')
        self.assertEqual(db['NAME'], 'postgres')

    def test_ssl_required_by_default(self):
        db = self.config(DATABASE_URL='postgresql://u:p@h:5432/postgres')
        self.assertEqual(db['OPTIONS'], {'sslmode': 'require'})

    def test_stricter_sslmode_and_root_cert(self):
        db = self.config(
            DATABASE_URL='postgresql://u:p@h:5432/postgres',
            DB_SSLMODE='verify-full', DB_SSLROOTCERT='/certs/supabase.crt',
        )
        self.assertEqual(db['OPTIONS'], {'sslmode': 'verify-full', 'sslrootcert': '/certs/supabase.crt'})

    def test_rejects_insecure_sslmode(self):
        for mode in ('disable', 'allow', 'prefer'):
            with self.subTest(source='env', mode=mode), self.assertRaises(ImproperlyConfigured):
                self.config(DATABASE_URL='postgresql://u:p@h:5432/postgres', DB_SSLMODE=mode)
            with self.subTest(source='url', mode=mode), self.assertRaises(ImproperlyConfigured):
                self.config(DATABASE_URL=f'postgresql://u:p@h:5432/postgres?sslmode={mode}')

    def test_local_server_may_disable_ssl(self):
        db = self.config(DATABASE_URL='postgresql://u:p@localhost:5432/postgres', DB_SSLMODE='disable')
        self.assertEqual(db['OPTIONS'], {'sslmode': 'disable'})

    def test_individual_settings(self):
        db = self.config(DB_HOST='h', DB_USER='u', DB_PASSWORD='p', DB_PORT='6543', DB_NAME='dev')
        self.assertEqual((db['HOST'], db['USER'], db['PASSWORD'], db['PORT'], db['NAME']), ('h', 'u', 'p', '6543', 'dev'))

    def test_missing_configuration_raises_instead_of_falling_back(self):
        with self.assertRaises(ImproperlyConfigured):
            self.config()
        with self.assertRaises(ImproperlyConfigured):
            self.config(DB_HOST='h', DB_USER='u')

    def test_rejects_non_postgres_or_incomplete_url(self):
        with self.assertRaises(ImproperlyConfigured):
            self.config(DATABASE_URL='sqlite:///db.sqlite3')
        with self.assertRaises(ImproperlyConfigured):
            self.config(DATABASE_URL='postgresql://h:5432/postgres')


class ModelDefinitionTests(SimpleTestCase):
    """Schema metadata checks that do not need a database."""

    def test_string_representations(self):
        document = Document(title='Publication 544', publisher='IRS')
        self.assertEqual(str(document), 'IRS: Publication 544')
        self.assertEqual(str(DocumentPassage(document=document, chunk_index=3)), 'IRS: Publication 544 (passage 3)')

    def test_status_choices_and_default(self):
        self.assertEqual(set(Document.Status.values), {'draft', 'approved', 'archived'})
        self.assertEqual(Document().status, Document.Status.DRAFT)

    def test_passage_document_relationship(self):
        field = DocumentPassage._meta.get_field('document')
        self.assertIs(field.related_model, Document)
        self.assertIs(field.remote_field.on_delete, models.PROTECT)

    def test_unique_document_chunk_constraint_leads_with_document(self):
        unique = [c for c in DocumentPassage._meta.constraints if c.name == 'chatbot_passage_document_chunk_unique']
        self.assertEqual(len(unique), 1)
        # The leading column lets this index serve foreign-key lookups.
        self.assertEqual(unique[0].fields, ('document', 'chunk_index'))


class WorkspaceViewTests(TestCase):
    def setUp(self):
        self.url = reverse('chatbot:workspace')
        self.user = get_user_model().objects.create_user('advisor', 'advisor@example.com', 'correct-Horse-battery-42')

    def test_anonymous_user_is_redirected_to_login(self):
        self.assertRedirects(self.client.get(self.url), f"{reverse('login')}?next={self.url}")

    def test_signed_in_user_sees_empty_workspace(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'chatbot/workspace.html')
        self.assertContains(response, 'New chat')
        self.assertContains(response, 'Section 1031')
        self.assertContains(response, 'Delaware Statutory Trust')
        self.assertContains(response, 'Section 721')
        self.assertContains(response, 'Your conversations will be listed here.')
        self.assertContains(response, f'action="{reverse("chatbot:ask")}"')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, 'Don’t include client names or personal details.')

    def test_user_menu_has_post_logout_and_hides_admin_link_for_regular_users(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, f'<form method="post" action="{reverse("logout")}">')
        self.assertContains(response, 'Signed in as advisor')
        self.assertNotContains(response, reverse('admin:index'))

    def test_staff_users_see_admin_link(self):
        self.user.is_staff = True
        self.user.save()
        self.client.force_login(self.user)
        self.assertContains(self.client.get(self.url), reverse('admin:index'))

    def test_workspace_does_not_accept_posts(self):
        # Questions go to chatbot:ask; the page itself is read-only.
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.url, {'message': 'Hi'}).status_code, 405)


class DocumentSchemaTests(TestCase):
    """Constraint behaviour against a real PostgreSQL database."""

    def setUp(self):
        self.document = Document.objects.create(
            title='Publication 544', publisher='IRS', source_url='https://www.irs.gov/publications/p544',
        )

    def create_passage(self, **kwargs):
        values = {'document': self.document, 'chunk_index': 0, 'content': 'Like-kind exchanges defer gain.'}
        values.update(kwargs)
        return DocumentPassage.objects.create(**values)

    def assertIntegrityError(self, func):
        with self.assertRaises(IntegrityError), transaction.atomic():
            func()

    def test_document_defaults(self):
        self.assertEqual(self.document.status, Document.Status.DRAFT)
        self.assertIsNotNone(self.document.created_at)
        self.assertIsNone(self.document.content_hash)
        self.assertIsNone(self.document.retrieved_at)

    def test_source_url_is_unique(self):
        self.assertIntegrityError(lambda: Document.objects.create(
            title='Duplicate', publisher='IRS', source_url=self.document.source_url,
        ))

    def test_invalid_status_rejected_by_database(self):
        self.assertIntegrityError(lambda: Document.objects.filter(pk=self.document.pk).update(status='published'))

    def test_chunk_index_unique_per_document(self):
        self.create_passage(chunk_index=0)
        self.assertIntegrityError(lambda: self.create_passage(chunk_index=0))

    def test_same_chunk_index_allowed_across_documents(self):
        other = Document.objects.create(title='Pub 551', publisher='IRS', source_url='https://www.irs.gov/publications/p551')
        self.create_passage(chunk_index=0)
        self.create_passage(document=other, chunk_index=0)
        self.assertEqual(DocumentPassage.objects.count(), 2)

    def test_negative_chunk_index_rejected(self):
        self.assertIntegrityError(lambda: self.create_passage(chunk_index=-1))

    def test_empty_content_rejected(self):
        self.assertIntegrityError(lambda: self.create_passage(content=''))

    def test_page_number_must_be_positive_when_set(self):
        self.assertIntegrityError(lambda: self.create_passage(page_number=0))
        self.assertIsNone(self.create_passage(chunk_index=1).page_number)
        self.assertEqual(self.create_passage(chunk_index=2, page_number=1).page_number, 1)

    def test_document_with_passages_cannot_be_deleted(self):
        self.create_passage()
        with self.assertRaises(ProtectedError):
            self.document.delete()

    def test_passages_resolve_to_their_document_in_order(self):
        self.create_passage(chunk_index=1, content='Second')
        self.create_passage(chunk_index=0, content='First')
        self.assertEqual([p.content for p in self.document.passages.all()], ['First', 'Second'])

    def test_full_text_search_uses_indexed_expression(self):
        self.create_passage(content='The replacement property must be identified within 45 days.')
        self.create_passage(chunk_index=1, content='REITs distribute most taxable income.')
        matches = DocumentPassage.objects.annotate(search=passage_search_vector()).filter(
            search=SearchQuery('identify replacement', config='english'),
        )
        self.assertEqual([p.chunk_index for p in matches], [0])

    def test_row_level_security_enabled_on_all_public_tables(self):
        # Supabase exposes the public schema through its Data API.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, relrowsecurity FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
            )
            rls = dict(cursor.fetchall())
        self.assertTrue(rls['chatbot_document'] and rls['chatbot_documentpassage'])
        self.assertIn('auth_user', rls)
        self.assertEqual([name for name, enabled in rls.items() if not enabled], [])
