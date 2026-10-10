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

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "django-insecure-dev-only-do-not-use-in-production"
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

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
# account JSON to back the business tables with Cloud Firestore instead of
# SQLite. Leave them empty for the default, fully-offline SQLite store. Django
# users, sessions and the admin stay on SQLite in either case.
DATA_STORE = os.environ.get("DATA_STORE", "").strip().lower()
FIREBASE_CREDENTIAL_PATH = os.environ.get("FIREBASE_CREDENTIAL_PATH", "").strip()
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "").strip()

if TESTING:
    # Force the ORM path in tests: the suite is written against SQLite and must
    # run fully offline, even when the developer's .env enables Firestore.
    # Tests that exercise the Firestore store inject a fake client directly.
    DATA_STORE = ""

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

# Swaps in a fast password hasher and a temporary MEDIA_ROOT for the test run.
# See quizcraft/testing.py for why. `manage.py test` picks this up on its own.
TEST_RUNNER = "quizcraft.testing.QCDiscoverRunner"

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

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "landing"

FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
