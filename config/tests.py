import json
import os
import subprocess
import sys

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

User = get_user_model()
PASSWORD = 'correct-Horse-battery-42'


class DeploymentTests(SimpleTestCase):
    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_health_check_needs_no_login_or_https(self):
        response = self.client.get('/healthz')
        self.assertEqual((response.status_code, response.content), (200, b'ok'))
        self.assertEqual(self.client.post('/healthz').status_code, 405)
        # Other pages still redirect plain HTTP to HTTPS.
        self.assertEqual(self.client.get(reverse('login')).status_code, 301)

    def test_render_host_name_is_trusted(self):
        """Production settings, imported in a fresh process with Render's variable set."""
        env = {
            **os.environ,
            'DJANGO_SETTINGS_MODULE': 'config.settings',
            'DJANGO_DEBUG': 'false',
            'DJANGO_SECRET_KEY': 'test-only-secret',
            'RENDER_EXTERNAL_HOSTNAME': 'sightbridge.onrender.com',
        }
        script = (
            'import json; from django.conf import settings; '
            'print(json.dumps([settings.DEBUG, settings.ALLOWED_HOSTS, settings.CSRF_TRUSTED_ORIGINS]))'
        )
        output = subprocess.run([sys.executable, '-c', script], env=env, cwd=settings.BASE_DIR,
                                capture_output=True, text=True, check=True).stdout
        debug, hosts, origins = json.loads(output)
        self.assertFalse(debug)
        self.assertIn('sightbridge.onrender.com', hosts)
        self.assertIn('https://sightbridge.onrender.com', origins)


class LoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('advisor', 'advisor@example.com', PASSWORD)
        self.url = reverse('login')

    def test_login_page_renders_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/login.html')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, 'autocomplete="username"')
        self.assertContains(response, 'autocomplete="current-password"')
        self.assertContains(response, 'data-password-toggle')

    def test_successful_login_starts_session_and_redirects_to_workspace(self):
        response = self.client.post(self.url, {'username': 'advisor', 'password': PASSWORD})
        self.assertRedirects(response, reverse('chatbot:workspace'))
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_wrong_password_shows_error_and_does_not_log_in(self):
        response = self.client.post(self.url, {'username': 'advisor', 'password': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please enter a correct username and password')
        self.assertContains(response, 'role="alert"')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_missing_fields_show_field_errors(self):
        response = self.client.post(self.url, {'username': '', 'password': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This field is required.', count=2)
        self.assertContains(response, 'aria-invalid="true"', count=2)

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        self.client.post(self.url, {'username': 'advisor', 'password': PASSWORD})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_safe_next_url_is_followed(self):
        response = self.client.post(f'{self.url}?next=/', {'username': 'advisor', 'password': PASSWORD, 'next': '/'})
        self.assertRedirects(response, '/')

    def test_external_next_url_is_ignored(self):
        response = self.client.post(self.url, {
            'username': 'advisor', 'password': PASSWORD, 'next': 'https://evil.example.com/',
        })
        self.assertRedirects(response, reverse('chatbot:workspace'))

    def test_signed_in_user_is_redirected_away_from_login(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(self.url), reverse('chatbot:workspace'))


class LogoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('advisor', 'advisor@example.com', PASSWORD)
        self.client.force_login(self.user)

    def test_post_logout_ends_session(self):
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertRedirects(self.client.get(reverse('chatbot:workspace')), f"{reverse('login')}?next=/")

    def test_get_logout_is_rejected(self):
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertIn('_auth_user_id', self.client.session)

    def test_logout_requires_csrf_token(self):
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('logout')).status_code, 403)


class AdminSeparationTests(TestCase):
    def setUp(self):
        self.regular = User.objects.create_user('advisor', 'advisor@example.com', PASSWORD)
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', PASSWORD)

    def test_regular_user_cannot_open_admin(self):
        self.client.force_login(self.regular)
        response = self.client.get(reverse('admin:index'))
        self.assertRedirects(response, f"{reverse('admin:login')}?next={reverse('admin:index')}")

    def test_regular_user_cannot_sign_in_to_admin(self):
        response = self.client.post(reverse('admin:login'), {'username': 'advisor', 'password': PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_administrator_can_open_admin(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)

    def test_both_roles_can_use_workspace(self):
        for user in (self.regular, self.admin):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('chatbot:workspace')).status_code, 200)


class UserProvisioningTests(TestCase):
    """The behaviour the one-off create_user() setup relies on."""

    def test_create_user_makes_hashed_non_staff_account(self):
        user = User.objects.create_user('advisor', 'advisor@example.com', PASSWORD)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.password.startswith('pbkdf2_sha256$'))
        self.assertEqual(authenticate(username='advisor', password=PASSWORD), user)

    def test_usernames_are_unique(self):
        from django.db import IntegrityError, transaction
        User.objects.create_user('advisor', 'a@example.com', PASSWORD)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user('advisor', 'b@example.com', PASSWORD)

    def test_weak_passwords_are_rejected_by_validators(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_password('password')
        validate_password(PASSWORD)
