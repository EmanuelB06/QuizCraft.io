"""
Django settings for the QuizCraft project.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

#: True while a `manage.py test ...` run is in progress. Used to keep the suite
#: on the offline SQLite/ORM path even when .env points at Firestore, so tests
#: never construct a real Firestore client.
TESTING = "test" in sys.argv

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()

# Fail closed: DEBUG defaults to off, and a production boot without a secret
# key is an error rather than a silently insecure fallback. Local development
# sets DJANGO_DEBUG=1 in .env and gets the throwaway dev key.
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-dev-only-do-not-use-in-production"
    else:
        raise RuntimeError(
            "DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off. "
            "See .env.example."
        )

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

# --- Production hardening -------------------------------------------------
# Only safe behind a reverse proxy that terminates TLS, which is how Render
# serves the site. Stays off locally under runserver.
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # Opt into the browser preload list only once you are sure the whole site
    # is HTTPS forever; it is effectively irreversible.
    SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "0") == "1"

# --- AI provider ---------------------------------------------------------
# Free keys: https://aistudio.google.com/apikey
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()

# Number of times a rate-limited (429) request is retried before giving up.
GEMINI_MAX_RETRIES = int(os.environ.get("GEMINI_MAX_RETRIES", "5"))
# Base seconds for exponential backoff between retries.
GEMINI_BACKOFF_BASE = float(os.environ.get("GEMINI_BACKOFF_BASE", "3"))

# --- Quiz generation -----------------------------------------------------
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "15"))
# Documents are split into chunks of roughly this many characters before
# being sent to the model, so long files do not blow the context window.
CHUNK_CHARS = int(os.environ.get("CHUNK_CHARS", "12000"))
CHUNK_OVERLAP_CHARS = int(os.environ.get("CHUNK_OVERLAP_CHARS", "400"))
MIN_SOURCE_CHARS = 200

# --- Firestore (optional data store) ------------------------------------
# Set DATA_STORE=firestore and point FIREBASE_CREDENTIAL_PATH at a service
# account JSON to back the data with Cloud Firestore instead of SQLite. When
# it is on, accounts and sessions also move into Firestore (AUTH_USER_MODEL and
# SESSION_ENGINE below read DATA_STORE at call time), so a server-hosted
# deployment keeps nothing on an ephemeral local disk. Leave DATA_STORE empty
# for the default, fully-offline SQLite store.
DATA_STORE = os.environ.get("DATA_STORE", "").strip().lower()
FIREBASE_CREDENTIAL_PATH = os.environ.get("FIREBASE_CREDENTIAL_PATH", "").strip()
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "").strip()

if TESTING:
    # Force the ORM path in tests: the suite is written against SQLite and must
    # run fully offline, even when the developer's .env enables Firestore.
    # Tests that exercise the Firestore store inject a fake client directly.
    DATA_STORE = ""
    # Blank the credentials too, not just DATA_STORE. override_settings can put
    # a single test back into Firestore mode, and with real credentials in
    # .env that test would build a live client and write to the project -- so
    # an unwrapped test now fails with FirestoreUnavailable instead.
    FIREBASE_CREDENTIAL_PATH = ""
    FIREBASE_PROJECT_ID = ""

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "quizzes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "quizcraft.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "quizzes.context_processors.app_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "quizcraft.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # Generation runs in a background thread, so the worker needs to wait
        # rather than fail immediately when the main thread holds the write lock.
        "OPTIONS": {"timeout": 30},
    }
}

# Production can run Django's own tables (auth, sessions, admin) on a server
# database via DATABASE_URL, for deployments that keep DATA_STORE off. Under
# DATA_STORE=firestore the accounts and sessions move into Firestore and this
# setting is left unset, so the only database is the ephemeral SQLite file
# that `migrate` builds and that never holds real data. Leave DATABASE_URL
# unset locally and in the test suite so they keep using SQLite.
if os.environ.get("DATABASE_URL"):
    import dj_database_url

    DATABASES["default"] = dj_database_url.parse(
        os.environ["DATABASE_URL"], conn_max_age=600
    )

# Swaps in a fast password hasher and a temporary MEDIA_ROOT for the test run.
# See quizcraft/testing.py for why. `manage.py test` picks this up on its own.
TEST_RUNNER = "quizcraft.testing.QCDiscoverRunner"

# When DATA_STORE=firestore, this custom model and the session engine back
# accounts and sessions on Firestore (see quizzes/models.py, quizzes/session_store.py).
# With the default SQLite store they behave like a normal Django user/session.
AUTH_USER_MODEL = "quizzes.User"
SESSION_ENGINE = "quizzes.session_store"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
#: collectstatic output, served by WhiteNoise in production.
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        # Hashed, compressed files in production so caches can be sent
        # long-lived and immutably; the plain backend under runserver because
        # the manifest storage needs a collectstatic run to resolve.
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if not DEBUG
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        ),
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

#: The uploaded source is only ever read for text extraction. When on, the
#: bytes are deleted right after extraction so nothing is persisted on disk.
DISCARD_SOURCE_UPLOADS = os.environ.get("DISCARD_SOURCE_UPLOADS", "0") == "1"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "quizzes": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "landing"

FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
