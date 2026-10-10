"""Public view surface.

The URLconf and the tests import view names from `quizzes.views`, so they are
re-exported here. The implementations are grouped by what a visitor is doing:
account pages, making a quiz, and working through one.
"""

from .creation import quiz_delete, quiz_progress, quiz_status, upload
from .pages import dashboard, health, landing, signup
from .review import quiz_edit, quiz_export, quiz_results, quiz_take

__all__ = [
    "dashboard",
    "health",
    "landing",
    "quiz_delete",
    "quiz_edit",
    "quiz_export",
    "quiz_progress",
    "quiz_results",
    "quiz_status",
    "quiz_take",
    "signup",
    "upload",
]
