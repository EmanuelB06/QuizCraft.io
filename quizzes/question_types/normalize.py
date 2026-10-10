"""Answer text handling, kept free of Django so the handlers stay cheap to test.

`normalize` is the single definition of "the same answer typed differently", and
it is shared by grading, by de-duplication during generation and by the handlers,
so it lives in a leaf module rather than inside any one of them.
"""

import re

_ARTICLES = {"a", "an", "the"}

_TRUTHY = {"true", "yes", "t", "y", "1", "correct"}
_FALSY = {"false", "no", "f", "n", "0", "incorrect"}


def normalize(text):
    """Lowercase, drop punctuation and articles, collapse whitespace.

    'The Cell.' / 'cell' / 'CELL ' all normalize to 'cell', which is what a
    student means when they type any of them.
    """
    if text is None:
        return ""
    text = str(text).lower().strip()
    text = text.replace("’", "'")
    text = text.replace("—", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in _ARTICLES]
    return " ".join(tokens)


def to_bool(value):
    """Interpret a free-text answer as True/False, or None if it is neither."""
    if isinstance(value, bool):
        return value
    normalized = normalize(value)
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    return None
