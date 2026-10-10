"""Session backend that keeps sessions in Firestore when ``DATA_STORE=firestore``.

When Firestore is the data store the accounts already live on the ``users``
collection, so sessions also leave the host's disk: each cookie's payload is a
document under ``sessions/{key}``. Everywhere else — the whole test suite, and
any non-Firestore deployment — this is exactly the Django database backend, so
``django_session`` keeps working untouched.

The session payload is Django's own serialized (encoded) session string, so a
round trip through Firestore is just a blob swap: no session semantics change.
"""

from django.conf import settings
from django.contrib.sessions.backends.db import (
    SessionStore as DatabaseSessionStore,
)
from django.utils import timezone


class SessionKeyAlreadyExists(Exception):
    """Ack a random session-key collision while creating in Firestore mode."""


#: Tests inject a fake Firestore here; production keeps it None so the store
#: builds its client from the configured credentials.
client_override = None


def _is_firestore() -> bool:
    return (settings.DATA_STORE or "").lower() == "firestore"


def _store():
    from .services.store import FirestoreStore

    return FirestoreStore(client=client_override)


class SessionStore(DatabaseSessionStore):
    """The database backend, with Firestore substituted when configured.

    Django's session machinery imports this module via ``SESSION_ENGINE`` and
    reads ``SessionStore`` off it, so the name is fixed by convention.
    """

    def load(self):
        if _is_firestore():
            record = self._firestore_session_record()
            if record is None:
                # Mirror the database backend: a missing (or expired) session
                # clears the key so a new one is minted next time.
                self._session_key = None
                return {}
            return self.decode(record["data"])
        return super().load()

    def exists(self, session_key):
        if _is_firestore():
            return _store().session_exists(session_key)
        return super().exists(session_key)

    def create(self):
        if _is_firestore():
            while True:
                self._session_key = self._get_new_session_key()
                try:
                    self.save(must_create=True)
                    return self._session_key
                except SessionKeyAlreadyExists:
                    continue
        return super().create()

    def save(self, must_create=False):
        if _is_firestore():
            if self.session_key is None:
                return self.create()
            store = _store()
            session_key = self._get_or_create_session_key()
            if must_create and store.session_exists(session_key):
                raise SessionKeyAlreadyExists
            data = self._get_session(no_load=must_create)
            store.save_session(
                session_key, self.encode(data), self.get_expiry_date()
            )
            return
        return super().save(must_create=must_create)

    def delete(self, session_key=None):
        if _is_firestore():
            key = session_key or self.session_key
            if key:
                _store().delete_session(key)
            return
        return super().delete(session_key)

    @classmethod
    def clear_expired(cls):
        if _is_firestore():
            _store().clear_expired_sessions(timezone.now())
            return
        return super().clear_expired()

    # -- helpers -----------------------------------------------------------

    def _firestore_session_record(self):
        record = _store().get_session(self.session_key)
        if record is None:
            return None
        expire_date = record.get("expire_date")
        if expire_date is not None and expire_date < timezone.now():
            return None
        return record