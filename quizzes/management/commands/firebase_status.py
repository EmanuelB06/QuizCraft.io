"""Report whether the optional Firestore backend is ready to use.

Never touches the network: it checks the configured credential file and reads
the project id straight out of that JSON, so it is safe to run offline and in
CI.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand

from quizcraft import firebase


class Command(BaseCommand):
    help = "Show the configured data store and whether Firestore credentials are present."

    def handle(self, *args, **options):
        store = getattr(settings, "DATA_STORE", "") or "sqlite"
        self.stdout.write(f"  data store      : {store}")

        if not firebase.is_configured():
            self.stdout.write("  firebase        : not configured (SQLite is in use)")
            self.stdout.write(
                self.style.NOTICE(
                    "  hint            : set FIREBASE_CREDENTIAL_PATH and "
                    "FIREBASE_PROJECT_ID in .env, then DATA_STORE=firestore"
                )
            )
            return

        path = firebase.credential_path()
        self.stdout.write(f"  credential file : {path}")

        try:
            with open(path, "r", encoding="utf-8") as handle:
                project_id = json.load(handle).get("project_id", "")
        except (OSError, ValueError):
            project_id = ""
        self.stdout.write(f"  project id      : {project_id}")

        if firebase.firestore_client() is None:
            self.stdout.write(self.style.ERROR("  client          : unavailable"))
            return
        self.stdout.write(self.style.SUCCESS("  client          : ready"))