"""Offline tests for Firestore-backed auth (accounts and sessions).

``DATA_STORE=firestore`` switches the ``User`` manager and the session engine
over to Firestore (see quizzes/models.py, quizzes/session_store.py). Both route
through ``FirestoreStore``, so these tests inject the same offline fake client
``test_store`` uses and run with no network and no credentials.

The ORM-mode branch of the same code is exercised by the rest of the suite,
which always runs with ``DATA_STORE=""``.
"""

from datetime import timedelta

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from quizzes import models as models_module
from quizzes import session_store as session_store_module

from .test_store import FakeFirestore


def _firestore_client(fake):
    models_module._firestore_client_override = fake
    session_store_module.client_override = fake
    return fake


@override_settings(DATA_STORE="firestore")
class FirestoreUserManagerTests(SimpleTestCase):
    def setUp(self):
        self.firestore = FakeFirestore()
        _firestore_client(fake=self.firestore)
        self.addCleanup(self._reset)

    def _reset(self):
        models_module._firestore_client_override = None
        session_store_module.client_override = None

    def _create(self, username="anna", password="sup3r-secret!"):
        return models_module.User.objects.create_user(
            username=username, password=password
        )

    def test_create_user_writes_a_profile_and_hydrates_back(self):
        user = self._create()
        record = self.firestore.data[f"users/{user.pk}"]
        self.assertEqual(record["username"], "anna")
        self.assertTrue(record["password"])

        loaded = models_module.User.objects.get(pk=user.pk)
        self.assertEqual(loaded.username, "anna")
        self.assertTrue(loaded.check_password("sup3r-secret!"))
        self.assertTrue(loaded.is_active)

    def test_get_by_username(self):
        self._create(username="bob")
        user = models_module.User.objects.get(username="bob")
        self.assertEqual(user.username, "bob")

    def test_missing_user_raises_does_not_exist(self):
        with self.assertRaises(models_module.User.DoesNotExist):
            models_module.User.objects.get(
                pk="00000000-0000-0000-0000-000000000000"
            )
        with self.assertRaises(models_module.User.DoesNotExist):
            models_module.User.objects.get(username="nobody")

    def test_get_or_create_round_trips(self):
        created, was_created = models_module.User.objects.get_or_create(
            username="anna"
        )
        self.assertTrue(was_created)
        again, was_created = models_module.User.objects.get_or_create(
            username="anna"
        )
        self.assertFalse(was_created)
        self.assertEqual(again.pk, created.pk)

    def test_create_superuser_sets_the_flags(self):
        superuser = models_module.User.objects.create_superuser(
            username="root", password="a-strong-pass-1"
        )
        record = self.firestore.data[f"users/{superuser.pk}"]
        self.assertTrue(record["is_staff"])
        self.assertTrue(record["is_superuser"])
        self.assertTrue(superuser.has_perm("any.permission"))

    def test_filter_supports_the_auth_lookups(self):
        self._create()
        self.assertTrue(models_module.User.objects.filter(username="anna").exists())
        self.assertFalse(models_module.User.objects.filter(username="nope").exists())
        self.assertEqual(
            models_module.User.objects.filter(username="anna").count(), 1
        )
        self.assertEqual(models_module.User.objects.all().count(), 1)
        first = models_module.User.objects.filter(username="anna").first()
        self.assertEqual(first.username, "anna")


@override_settings(DATA_STORE="firestore")
class FirestoreSessionStoreTests(SimpleTestCase):
    def setUp(self):
        self.firestore = FakeFirestore()
        session_store_module.client_override = self.firestore
        self.addCleanup(self._reset)

    def _reset(self):
        session_store_module.client_override = None

    def _save(self, **data):
        store = session_store_module.SessionStore()
        store.update(data)
        store.save()
        return store

    def test_session_saves_and_loads(self):
        store = self._save(answer="42")
        key = store.session_key
        self.assertTrue(self.firestore.data[f"sessions/{key}"]["data"])

        loaded = session_store_module.SessionStore(session_key=key)
        self.assertEqual(loaded["answer"], "42")
        self.assertTrue(loaded.exists(key))

    def test_missing_session_loads_empty(self):
        loaded = session_store_module.SessionStore(session_key="missing-key")
        self.assertEqual(loaded.load(), {})
        self.assertIsNone(loaded.session_key)

    def test_expired_session_is_treated_as_missing(self):
        store = self._save(answer="42")
        record = self.firestore.data[f"sessions/{store.session_key}"]
        record["expire_date"] = timezone.now() - timedelta(seconds=1)

        loaded = session_store_module.SessionStore(
            session_key=store.session_key
        )
        self.assertEqual(loaded.load(), {})
        self.assertIsNone(loaded.session_key)

    def test_delete_removes_the_session(self):
        store = self._save(answer="42")
        key = store.session_key
        store.delete()
        self.assertNotIn(f"sessions/{key}", self.firestore.data)


@override_settings(DATA_STORE="firestore")
class FirestoreSignupFlowTests(TestCase):
    def setUp(self):
        self.firestore = FakeFirestore()
        _firestore_client(fake=self.firestore)

    def tearDown(self):
        models_module._firestore_client_override = None
        session_store_module.client_override = None

    def _credentials(self, username="newcomer"):
        return {
            "username": username,
            "password1": "a-strong-passphrase-42",
            "password2": "a-strong-passphrase-42",
        }

    def test_signup_writes_the_account_and_logs_in(self):
        response = self.client.post(reverse("signup"), self._credentials())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

        profiles = [
            key
            for key in self.firestore.data
            if key.startswith("users/") and "/" not in key[len("users/"):]
        ]
        self.assertEqual(len(profiles), 1)
        self.assertEqual(
            self.firestore.data[profiles[0]]["username"], "newcomer"
        )
        self.assertTrue(self.client.session["_auth_user_id"])

    def test_duplicate_username_is_rejected(self):
        models_module.User.objects.create_user(
            username="taken", password="a-strong-passphrase-42"
        )
        response = self.client.post(
            reverse("signup"), self._credentials(username="taken")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertContains(response, "is-invalid")

    def test_signup_fields_render_styled_and_hinted(self):
        html = self.client.get(reverse("signup")).content.decode()
        self.assertEqual(
            html.count("form-control"), 3, "three styled fields on signup"
        )
        self.assertIn('autocomplete="username"', html)
        self.assertIn('autocomplete="new-password"', html)