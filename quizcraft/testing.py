"""Test-runner overrides for speed and filesystem hygiene.

Two defaults make the suite slow and messy:

* every ``make_user()`` hashes the password with PBKDF2 at production
  strength, which dominates the wall time of ``test_views``;
* every ``make_document()`` writes a real file into ``MEDIA_ROOT``, and the
  database rollback does not roll the filesystem back, so ``media/``
  accumulates orphans across runs.

Both are test-environment concerns, so they live here rather than in the
tests. ``manage.py test`` discovers this runner through ``TEST_RUNNER``, so
the documented commands do not change.
"""

import shutil
import tempfile

from django.conf import settings
from django.test.runner import DiscoverRunner


class QCDiscoverRunner(DiscoverRunner):
    """Use a fast password hasher and a throwaway media directory."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.PASSWORD_HASHERS = [
            "django.contrib.auth.hashers.MD5PasswordHasher",
        ]
        self._media_root = tempfile.mkdtemp(prefix="qc-test-media-")
        settings.MEDIA_ROOT = self._media_root

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        media_root = getattr(self, "_media_root", None)
        if media_root:
            shutil.rmtree(media_root, ignore_errors=True)
