# AGENTS.md

Django 6 + SQLite quiz generator. Users upload a PDF/DOCX/TXT, Google Gemini writes
the questions in a background thread, they edit/take/export them.

## Commands

Always use the venv interpreter; the global `python` has no Django installed.

```
& .\.venv\Scripts\python.exe manage.py test quizzes -v1        # full suite (~2.5 min)
& .\.venv\Scripts\python.exe manage.py test quizzes.tests.test_views.EditFlowTests   # one class
& .\.venv\Scripts\python.exe manage.py test quizzes.tests.test_grading.GradeMultiSelectPartialCreditTests.test_name
& .\.venv\Scripts\python.exe manage.py runserver
& .\.venv\Scripts\python.exe scripts\make_pdfs.py            # render both PDF variants
```

No linter, formatter, type checker, or CI is configured. `manage.py check` is the
only other gate. Full suite is 213 tests; keep it green.

If you pipe a test run through `Select-String`/`grep`, the `Ran N tests` and
`OK`/`FAILED` summary gets lost and it looks like the run hung. Redirect to a file
and read the tail instead:

```
& .\.venv\Scripts\python.exe manage.py test quizzes -v1 *> "$env:TEMP\qc.log"; Get-Content "$env:TEMP\qc.log" -Tail 5
```

Two log lines in a passing run are expected, not failures: `GenerationError: Daily
quota reached.` comes from `test_provider_error_is_captured_on_the_quiz`, which
injects that error on purpose, and `invalid pdf header` is reportlab complaining
inside the "no questions to export" test.

`.env` is loaded by `load_dotenv` in `quizcraft/settings.py` (see `.env.example`).
`GEMINI_API_KEY` is empty by default, so nothing hits the network in tests — the
generation code is only reachable through mocks.

## Question types are a registry, not a switch statement

`quizzes/question_types/` is the only place that knows what a question type means.
`REGISTRY` in `__init__.py` maps a persisted value to a stateless handler; the
handlers in `choice.py` and `text.py` own validation, submission extraction,
grading, the answer-view description, and how an edit is written back.

`QuestionType`/`Difficulty` live in `question_types/kinds.py`, not `models.py`,
so the handlers can import them without a circular import. `models.py` re-exports
both for backwards compatibility.

**Adding a type means: one handler class plus one `REGISTRY` entry.** If you find
yourself adding an `if question.question_type == ...` to a view, a form, the PDF
writer or a template, that is the bug — add the behaviour to the handler instead.
`tests/test_question_types.py` holds the registry/enum parity tests that must keep
passing.

Handler flags that consumers read rather than branching on the value:

- `is_choice_based` — the edit screen offers option boxes. Only the two MCQs.
  `TrueFalseHandler` is **False** even though it owns the options `['True',
  'False']`, because true/false is authored as a text answer.
- `expects_many` — the browser posts several values (checkbox group). Only
  `mcq_multi`. `services/attempts.extract_submission` reads `get` vs `getlist`
  based on this; getting it wrong silently grades every answer as blank.
- `multiline` — a typed answer wants a textarea rather than a one-line input.
- `input_template` — the take page does `{% include question.handler.input_template %}`,
  so a new type brings its own control markup.

`Question.answer_view` is always the *student* view (the key is not reachable from
it). The PDF writer asks the handler directly when it needs the revealed version.

## TextChoices cannot hold extra classmethods

`QuestionType` is a `models.TextChoices`. Django's metaclass does not keep extra
classmethods on the resulting enum — a `label()` classmethod on the enum silently
vanished and made `Question.type_label` return an empty string in every template,
while `Quiz.get_absolute_url` calling into it broke migrations.

Labels therefore come from the handlers, via `label_for()` in
`quizzes/question_types/__init__.py`, and `Question.type_label` forwards to it.
Keep new enum helpers as plain functions, never as methods on the enum.

Consequence: renaming or removing a `TextChoices` value, or changing anything the
serialized `choices` depend on, requires regenerating migrations. If a migration
raises `AttributeError` on an enum, the migration file has captured a helper that
no longer exists — delete and re-run `makemigrations` (this is not a real schema
change, so it is safe pre-release).

## Correct-answer indexes are positional and break silently

`Question.correct_choices` is a list of indexes into `Question.choices`, and
`acceptable_answers[0]` is the key for text types. Dropping or reordering an option
shifts every later index.

There is now exactly **one** place that re-keys, so a new call site must go through
it rather than remapping by hand:

- `question_types/base.py:resolve_choice_selection(values, ticked)` — returns
  `(options, correct_indexes)`. Used by `forms/question_edit.py` for both the
  existing questions and the hand-written slots.
- `question_types/base.py:dedupe_choices` / `remap_indexes` — the primitives.
  `ChoiceHandler.validate` uses these on model output; a duplicate option resolves
  to its surviving twin.
- `ChoiceHandler.apply_edited` stores an already-rekeyed key; it does not remap.
  It returns `False` when no options are left, which drops the question.

Missing a remap yields a question keyed to the wrong option, not an error. The
end-to-end regression test is `test_key_follows_the_option_when_one_is_blanked` in
`test_views.EditFlowTests`; `tests/test_question_types.py` covers the helpers.

## Supported source formats live in one place

`services/extraction.py` owns `SUPPORTED_EXTENSIONS` and the `_HANDLERS` map, and
`forms/upload.py` imports that set rather than keeping its own list -- the form
runs first, so two lists meant a format could be added to the extractor and then
quietly refused by the form. `UploadFormatContractTests` in
`tests/test_extraction.py` pins the agreement, including that the rejection
message still lists every supported extension.

Adding a format means: a `_extract_<ext>` function, two `_HANDLERS` entries
(the extension is in `SUPPORTED_EXTENSIONS` and mapped to the handler), and the
`accept` attribute plus help text in `templates/upload.html`. `scripts/` and the
landing/FAQ copy in `views/pages.py` mention formats in prose and have to be
edited by hand.

Two things are worth knowing about the PowerPoint path, both because `pptx` is
not just a document with a different extension:

- A slide is the PowerPoint **page**, so `_extract_pptx` returns the slide count
  as `page_count` and reuses `_drop_repeated_lines` to strip a footer or slide
  number repeated across the deck. That is the same guard as the PDF's, and it
  matters as much here: a slide number is on every slide by default.
- Text is scattered across shape kinds, so `_slide_blocks` has to recurse into
  groups and check tables and charts, not just `has_text_frame`. Speaker notes
  are pulled in too, and prefixed inline (`Speaker notes: ...`) rather than on a
  line of their own -- a one-word marker repeated at the end of most slides
  looks like boilerplate and `_drop_repeated_lines` would delete it.

A deck of image-only slides has no text layer, exactly like a scanned PDF, and
gets its own OCR message rather than the generic "not enough characters" one.

## The edit screen plans before it writes

`services/editing.apply_question_edits` builds a plan, refuses the edit if it would
leave zero questions, and only then writes. This ordering is load-bearing:
`Model.delete()` sets `pk = None` on the in-memory instance and a transaction
rollback does not undo that, so checking *after* deleting leaves the re-rendered
edit page unable to name its own fields (`Failed lookup for key [hex] in None`).

## Generation runs in a thread, not a queue

`start_background_generation` spawns a daemon `threading.Thread`
(`services/generation.py`). Consequences:

- The process must stay alive; a dev-server restart loses in-flight quizzes,
  which stay stuck in `processing`.
- SQLite needs `OPTIONS={"timeout": 30}` (already set in settings) so the worker
  waits on the write lock instead of failing.
- The thread sleeps 2.5-4.5s between chunks to stay under the free tier's
  ~15 requests/minute. Do not "optimize" that sleep away.
- Tests must mock `quizzes.views.creation.start_background_generation`, or a real
  thread will hit the network. The `views` package re-exports view *names* only, so
  the patch target is the submodule that calls it.
- `run_generation`/`start_background_generation` take an optional `generator`.
  It defaults to `default_generator()`, which resolves
  `gemini.generate_questions` at call time — which is why tests can still patch
  `quizzes.services.generation.gemini.generate_questions`.

`quizzes/services/gemini.py` is the only module that knows about the provider.
Keep provider-specific code there.

## Django template gotchas that already bit this codebase

- `{% extends %}` must be the first tag in a template. `{% load %}` above it
  raises `TemplateSyntaxError`.
- Django templates have no inline ternary. `{{ "Retry" if x else "Status" }}`
  is a syntax error; use `{% if %}{% else %}`.
- `{% include name with a=1 b=2 %}` with several assignments is fine, and the
  template name may be a variable (`{% include question.handler.input_template %}`).
- The dashboard is rendered by three tests, so a template syntax error there
  shows up as three unrelated failures.

## The brand colour is a CSS override, not a Bootstrap theme

`static/css/tokens.css` holds the teal `--qc-primary` ramp and maps it onto
Bootstrap 5.3.3's custom properties; `app.css` is only a composer that `@import`s
the module files (see "CSS and JS are modular" below). Two things make the
override less obvious than it looks:

- Bootstrap **hardcodes** the primary variant colours as hex literals and does
  not read `--bs-primary` for them. `text-primary`, `bg-primary`, `link-primary`
  and plain `<a>` links follow `--bs-primary`, but `.btn-primary`,
  `.btn-outline-primary`, `.form-control:focus`, `.form-select:focus` and
  `.form-check-input:checked` each need their component variables re-declared by
  hand. A new primary-coloured component means adding another block to
  `base.css`.
- `--bs-primary-bg-subtle` and `--bs-primary-text-emphasis` are what theme the
  accordion's open state, so the FAQ is teal for free -- except its chevron,
  which is a data URI with the hex percent-encoded (`stroke='%230f766e'`).

A `var()` reference is silently invalid at computed-value time if the custom
property is not in scope, and the declaration is dropped with no error. This
already bit the timeline rail: `--bs-gutter-x` is defined on `.row` and nowhere
else, so `.qc-timeline` has to be on the row rather than a wrapper around it,
otherwise `left` falls back to `auto` and the rail is mispositioned. When a
new `var()`-based rule looks like it should work but renders as nothing, check
the variable's defining selector before anything else.

Both CDN dependencies (Bootstrap 5.3.3, Bootstrap Icons 1.13.1) are pinned and
loaded in `templates/base.html`; there is no npm build step. `landing.html` has
no `static/js/` dependency -- the FAQ accordion is driven by the Bootstrap bundle
already on every page.

## CSS and JS are modular

`static/js/` is one class per file under `modules/`, each with a single
responsibility and all collaborators injected through the constructor (no
globals, no hidden DOM lookups). The top-level files (`auth.js`, `upload.js`,
`quiz_edit.js`, `quiz_progress.js`) are thin composition roots loaded as
`<script type="module">`; they only wire a class to the live DOM. Pages hand
config to a module through `data-*` attributes (e.g. `data-status-url` on the
progress card, read as `dataset.statusUrl`), never through inline script, and
the module scripts are deferred by default so `defer` is unnecessary and
misleading. All of it is progressive enhancement.

`static/css/app.css` owns no rules of its own: it is a composer whose `@import`
list is the cascade. The files, in order, are `tokens.css` (every custom
property -- the only place a brand literal may live, so re-theming is a
one-file change), `base.css` (the document surface plus the Bootstrap primary
bridges above), `layout.css` (the shell: navbar, account menu), `components/*.css`
(one file per page area: landing, dashboard, upload, quiz, auth),
`utilities.css` (reduced-motion, print). A new rule goes in the matching
component file; a new component file goes in the right cascade position, not
appended blindly. The navbar lives in `templates/partials/navbar.html`, included
by `base.html`.

## The review screen masks the answer key, not the data

`quiz_edit` hides the answer key by default; the `data-answer-key-toggle`
button swaps the `qc-key-hidden` class on `#question-form` in place, and
`?answers=1` is the reload fallback (mirroring the export view's `reveal` GET
param). The masking is presentation only: the correct checkboxes stay `checked`
and the answer/explanation fields keep their values, so saving is unchanged.
Do not "hide" the key by rendering the inputs unchecked or blank -- that would
wipe it on the next save.

The controls to mask carry `data-answer-key`; `AnswerKeyMask` marks one
`data-touched` once it changes, so the creator's own edits are never left
masked (the CSS masks `:not([data-touched])`). `AnswerKeyToggle`, `AnswerKeyMask`
and `DeleteToggle` are all wired from the `quiz_edit.js` entry point.

## Django renders no CSS class on a widget

`{{ field }}` emits whatever the widget declares and nothing else. Django has
no idea Bootstrap exists, so a form that renders fields generically produces
unstyled native inputs unless the class comes from somewhere -- which is what
`login.html`/`signup.html` used to be: the login page hand-wrote its `<input>`
tags to force `form-control`, and the signup page rendered `{{ field }}` and got
no class at all, so the two auth pages did not match and the signup fields were
visibly broken.

`forms/auth.py` fixes that once for both pages: `BootstrapStyledForm` adds
`form-control` to every visible widget and `is-invalid` to whichever ones
failed, in `__init__`. Do not re-add the class in a template, and do not
hand-write `<input>` tags for a bound field -- that is what silently threw away
the `autocomplete`/`autocapitalize` attributes Django sets for you. Reading
`self.errors` in `__init__` is safe: it runs the same validation `is_valid()`
would and caches the result.

Note that `AuthenticationForm` reports bad credentials as a **non-field** error,
so nothing goes red on the login form by design. `is-invalid` is a Bootstrap
concept and Django will never add it on its own.

## PDF text must stay ASCII

`quizzes/services/export.py` renders with the base-14 Helvetica, which is
WinAnsi-encoded. A literal `✓` (U+2713) rendered as an empty box, so the answer
key uses the ASCII marker in `_CORRECT_MARK`. Keep any new PDF literal
ASCII-safe, or register a TTF font. `ExportTests` asserts the extracted text
round-trips through `str.encode("ascii", "strict")`.

The PDF writer switches on `AnswerView.kind` (`single`/`multi`/`boolean`/`text`),
which is a closed set of drawing shapes, not on `question_type`. A new type with a
genuinely new shape adds a branch here; one that reuses a shape does not.

## The test suite is slow, and why

`test_views` takes ~130s while the other files take ~20s combined, even though
test_views is only 49 of the 171 tests. The cause is password hashing, not the
views: every `setUp` calls `make_user()` in `quizzes/tests/factories.py`, which
runs PBKDF2 at production strength, and no fast hasher is configured for tests.
When adding many tests, expect each new `setUp` to add seconds. Fixing it means
adding a `MD5PasswordHasher` test settings override, which has not been done.
`test_question_types` is `SimpleTestCase`, so it runs in milliseconds and needs no
`setUp` — prefer that shape for anything that does not need the database.

## Tests write into the real MEDIA_ROOT

`factories.make_document` saves a real `FileField`, and no test overrides
`MEDIA_ROOT`, so every upload test leaves a file in `media/documents/<user_id>/`.
The database is rolled back but the files are not. `media/` has hundreds of
orphaned files from past runs. Don't be surprised by them, don't commit them, and
don't assume a file in `media/` corresponds to a live row. If you add a test that
uploads, consider cleaning up after it.

## Firestore is the connected data store

`settings.DATA_STORE` selects the business-data backend, and `.env` sets it to
`firestore` for the live project (`quizcraft-da152`, creds in `.env`, file
gitignored). Leave it empty/`sqlite` to keep everything on the ORM. Django's own
tables (users, sessions, admin, CSRF) stay on SQLite either way -- only
documents/quizzes/questions/attempts/responses move.

Read/write goes through one seam, `quizzes/services/repository.py`:

- `get_repository()` picks by `settings.DATA_STORE`. `""`/`sqlite` returns
  `OrmRepository` (the original queries); `firestore` returns
  `FirestoreRepository`, which fails loudly (`FirestoreUnavailable`) if Firebase
  is not configured, so a misconfigured swap never silently keeps writing to
  SQLite.
- Both implementations return **hydrated Django model instances** (pk set,
  relations attached, `_questions_cache`/`_responses_cache` populated) so
  templates, the edit form and the question-type handlers work unchanged.
  `Quiz.questions`/`is_ready` and `Attempt.responses`/`response_count`/
  `correct_count` read those caches first and only fall back to the ORM
  relation when no cache is present.
- Every business call site in `services/` and `views/` routes through the
  repository; nothing outside `services/store.py` imports `google.cloud` or
  `firebase_admin`.

`quizcraft/firebase.py` owns every Admin SDK import (lazy, inside functions) and
is offline-safe: `is_configured()`, `credential_path()`, `firestore_client()`
(None when unconfigured), `app()`, `reset()`. `manage.py firebase_status`
reports readiness without touching the network.

`quizzes/services/store.py` defines the Firestore record shapes
(`document_record`/`quiz_record`/`question_record`/`attempt_record`/
`response_record`) and `FirestoreStore` (client injected). Layout is
`users/{uid}/documents|quizzes|attempts`, plus
`users/{uid}/quizzes/{quiz_id}/questions/{question_id}` and
`users/{uid}/attempts/{attempt_id}/responses/{question_id}`. Quiz and Question
reuse their SQLite UUIDs; `SourceDocument`, `Attempt` and `Response` are
autoincrement in SQLite, so Firestore mints UUIDs for them (a response is keyed
by `question_id`). `points_earned` is stored as a string and parsed back to
`Decimal`; the raw upload stays on local disk and the record only carries the
path.

**One-off data move:** `manage.py migrate_to_firestore [--dry-run] [--user ID]`
copies the SQLite business rows into Firestore. It derives deterministic uuid5
ids for the integer-pk tables, so it is idempotent (re-running overwrites) and a
quiz's `document_id` is rewritten to the new document id.

Tests: `tests/test_repository.py` pins the seam over a **fake Firestore injected
as the client** (plus an end-to-end view flow and the generation pipeline);
`tests/test_store.py` pins the store shapes; `tests/test_migrate_command.py`
pins the migration; `tests/test_firebase.py` guards the "no-op until configured"
contract. All run offline -- no network, no credentials.

**The suite never touches Firestore.** `quizcraft/settings.py` sets
`TESTING = "test" in sys.argv` and forces `DATA_STORE = ""` while it is true, so
a developer `.env` of `DATA_STORE=firestore` does not make tests build a real
client. Tests that exercise Firestore inject a fake or use `override_settings`.

Two gotchas:

- The Admin SDK `app()`/`firestore_client()` are `lru_cache`d, so a test that
  configures Firebase and one that does not cannot share a process unless
  `firebase.reset()` runs in `setUp`. `FirebaseConfigTests` does this.
- Because `.env` now carries real credentials, "unconfigured" tests must force
  it with `override_settings(FIREBASE_CREDENTIAL_PATH="", FIREBASE_PROJECT_ID="")`
  — the default is configured, the opposite of what it used to be.

## Layout

```
quizzes/
  question_types/   registry + handlers. The OCP seam; see above.
  services/         pipeline stages: extraction (file to text), gemini (provider),
                    generation (chunking, validation, persistence, provider
                    injection), grading (thin facade over the registry),
                    attempts (score a submission), editing (persist an edit),
                    export (PDF), repository (Orm/Firestore seam), store
                    (Firestore record shapes + client).
  forms/            upload.py, question_edit.py. Validation only -- these do not
                    write to the database; services/editing does.
  views/            pages.py, creation.py, review.py. Thin: ownership checks,
                    messages, redirects. `__init__.py` re-exports the view names.
  management/       commands: firebase_status, migrate_to_firestore.
```

`templates/` is **not** under `quizzes/`. It is the project-level template dir
(`BASE_DIR / "templates"`, from the `TEMPLATES DIRS` setting), alongside
`registration/` and `partials/`. Bootstrap 5.3.3 and Bootstrap Icons 1.13.1 are
pinned in `templates/base.html`; there is no npm build step.

`services/` has no `__init__.py` — it works as an implicit namespace package, so
don't go adding one unless you also check nothing depends on the current import
behaviour. `grading.py` imports no Django directly and unit tests without a
database, but note it now pulls in `question_types`, and `kinds.py` imports
`django.db`, so the package is not Django-free.

Views and forms are packages, not modules. A file of the same name would be
shadowed by the package; that is what happened to the old `quizzes/views.py` and
it left dead code behind, so delete them rather than leaving both.
