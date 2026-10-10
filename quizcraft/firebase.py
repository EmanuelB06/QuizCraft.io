"""Firebase Admin SDK wiring (optional).

QuizCraft can back its business data (documents, quizzes, questions, attempts,
responses) with Cloud Firestore instead of SQLite. This module is the only
place that touches the Admin SDK, so the rest of the app never imports
`google.cloud` or `firebase_admin` directly.

Importing this module is deliberately side-effect free: no credentials are
loaded and no network is touched at import time. Initialisation happens only
when `firestore_client()` is called, and only when Firebase is configured
(`settings.FIREBASE_CREDENTIAL_PATH` points at an existing service-account
JSON). With Firebase absent the app runs exactly as it always has -- SQLite,
fully offline -- which is how the test suite runs.

Django's users, sessions, the admin and CSRF stay on SQLite regardless;
Firestore only replaces the business-entity tables.
"""

from functools import lru_cache
from pathlib import Path

from django.conf import settings


class FirebaseUnavailable(RuntimeError):
    """Raised when Firebase is not configured but the caller asked for it."""


def credential_path() -> str:
    return getattr(settings, "FIREBASE_CREDENTIAL_PATH", "").strip()


def is_configured() -> bool:
    """True when a service-account file exists to authenticate with."""
    path = credential_path()
    return bool(path) and Path(path).is_file()


def _project_id() -> str:
    return getattr(settings, "FIREBASE_PROJECT_ID", "").strip()


@lru_cache(maxsize=1)
def app():
    """The initialised admin app (cached). Raises FirebaseUnavailable off."""
    from firebase_admin import credentials, initialize_app

    if not is_configured():
        raise FirebaseUnavailable(
            "Firebase is not configured. Set FIREBASE_CREDENTIAL_PATH to a "
            "service-account JSON (and FIREBASE_PROJECT_ID)."
        )
    options = {"projectId": _project_id()} if _project_id() else None
    return initialize_app(credentials.Certificate(credential_path()), options)


@lru_cache(maxsize=1)
def firestore_client():
    """A Firestore client, or None when Firebase is not configured.

    The status command calls this to report readiness; store code calls it and
    treats FirebaseUnavailable as the signal to fall back to SQLite.
    """
    from firebase_admin import firestore

    try:
        return firestore.client(app=app())
    except FirebaseUnavailable:
        return None


def reset() -> None:
    """Forget the cached app/client. Tests that need a fresh instance use this."""
    app.cache_clear()
    firestore_client.cache_clear()