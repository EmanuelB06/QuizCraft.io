"""The Firestore integration must be a no-op until it is configured.

These are SimpleTestCases (no database): importing quizcraft.firebase and
calling into it must not touch the network or require credentials, or every
SQLite run would start failing the moment the module is imported.
"""

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from quizcraft import firebase


@override_settings(FIREBASE_CREDENTIAL_PATH="", FIREBASE_PROJECT_ID="")
class FirebaseConfigTests(SimpleTestCase):
    def setUp(self):
        # The SDK app/client are lru_cached process-wide, so each test must
        # start from an empty cache or an earlier test could leak a real
        # client through the override.
        firebase.reset()
    def test_not_configured_by_default(self):
        self.assertFalse(firebase.is_configured())

    def test_client_is_none_when_unconfigured(self):
        self.assertIsNone(firebase.firestore_client())

    def test_app_raises_when_asked_for_credentials(self):
        with self.assertRaises(firebase.FirebaseUnavailable):
            firebase.app()

    def test_reset_leaves_everything_unconfigured(self):
        firebase.reset()
        self.assertIsNone(firebase.firestore_client())

    def test_fake_credential_path_is_rejected(self):
        with override_settings(FIREBASE_CREDENTIAL_PATH="C:/nope/missing.json"):
            self.assertFalse(firebase.is_configured())


class DataStoreSettingsTests(SimpleTestCase):
    def test_data_store_defaults_to_sqlite(self):
        self.assertEqual(settings.DATA_STORE, "")
        self.assertEqual(settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1], "sqlite3")