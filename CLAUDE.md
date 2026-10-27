# CLAUDE.md

Sightbridge "Friendly Tax Advisor": a login-protected Q&A chat for financial advisors about 1031 exchanges, Delaware Statutory Trusts (DSTs), and 721 exchanges. It answers only from an approved document set, cites the passages it used, and logs every exchange for admins. Django + Supabase PostgreSQL + Gemini. It is a take-home exercise; `README.md` is the reader-facing doc (setup, routes, screenshots, deployment, what's left out).

Project rules (stack, secrets, row level security, frontend skill, chatbot guardrails) are in `AGENTS.md` and apply here:

@AGENTS.md

## Commands

Windows with Git Bash. The virtualenv is `.venv` (Python 3.13); call its interpreter directly, no activation needed.

```bash
.venv/Scripts/python.exe manage.py runserver                     # needs .env
.venv/Scripts/python.exe manage.py check
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run
.venv/Scripts/python.exe manage.py migrate                       # writes to the shared Supabase DB (see Database)
.venv/Scripts/python.exe manage.py test --noinput                # all 107 tests, about 6 minutes
.venv/Scripts/python.exe manage.py test chatbot.test_chat        # chat pipeline only, about 3 minutes
.venv/Scripts/python.exe manage.py check_corpus                  # read-only readiness check of loaded documents
.venv/Scripts/python.exe manage.py run_eval -v 2                 # LIVE Gemini end-to-end eval; rolls back unless --record
.venv/Scripts/python.exe manage.py test chatbot.tests.DatabaseConfigTests chatbot.tests.ModelDefinitionTests chatbot.test_chat.CitationValidationTests   # no DB, instant
```

Tests are slow because every database test talks to Supabase over the network. Gemini is never called in tests (`FakeLLM` in `chatbot/test_chat.py`).

Configuration comes from `.env` at the repo root (git-ignored, loaded by `config/settings.py` with python-dotenv). `.env.example` lists every variable. Locally you need `DATABASE_URL` and `DJANGO_DEBUG=true` (without debug, `DJANGO_SECRET_KEY` is required), plus `GEMINI_API_KEY` for real answers. Without the key every question returns the "busy" message.

## How a question is answered

`POST /chat/ask/` (`chatbot/views.py: ask`) validates input, checks conversation ownership and the per-user rate limit, then calls `answering.answer_question()` and `audit.record_exchange()`, and returns JSON containing server-rendered HTML.

```
triage (Gemini flash-lite)  -> educational | personalized_advice | out_of_scope, plus a standalone query
route                       -> out_of_scope: fixed message. personalized_advice: decline text + continue with the general question
retrieve (Postgres FTS)     -> approved documents only, top 8 above a rank threshold; none -> "not found" without calling Gemini again
answer (Gemini 3.6 flash)   -> sees only labelled passages P1..P8, returns JSON {status, answer with [P#], cited};
                               on 429/500/503/504 or timeout, one retry on the fallback model (flash-lite)
validate (safety.py)        -> drops markers for passages not sent, renumbers [P#] to [n]; no valid citation -> "not found"
disclaimer + audit          -> disclaimer added by code on every outcome; exchange and citation snapshots saved, including errors
```

| File | Responsibility |
| --- | --- |
| `chatbot/llm.py` | The only module that talks to Gemini (`google-genai`). Prompts, pydantic response schemas, `get_llm()`, per-call timeouts. `answer()` returns `(GroundedAnswer, model_used)` and does the single fallback retry. `LLMUnavailable.retryable` separates transient failures from permanent ones (bad key, unknown model). No SDK retries. |
| `chatbot/retrieval.py` | `search(text)`: ORs the question's terms, filters `Document.Status.APPROVED`, ranks with `SearchRank`. Uses `passage_search_vector()` so the GIN index applies. |
| `chatbot/safety.py` | Fixed texts (`DISCLAIMER`, decline, scope, not-found, unavailable), the keyword fallback for advice requests, `validate_citations()`. |
| `chatbot/answering.py` | `answer_question(question, conversation, llm)` returns a `ChatResult`. No HTTP or database writes, so every outcome is testable with a fake LLM. |
| `chatbot/audit.py` | `record_exchange()`: one transaction for the conversation, exchange, and citation snapshots. |
| `chatbot/views.py` | `workspace` (new or saved conversation), `ask` (JSON), per-user rate limit via Django's cache. |
| `chatbot/templatetags/chatbot_tags.py` | `render_answer`: escapes model text first, then turns `[n]` into links only when a saved citation `n` exists. |
| `chatbot/admin.py` | Read-only admin for exchanges and conversations (the admin conversation log). |
| `chatbot/evaluation.py`, `chatbot/eval_questions.json` | Evaluation question set (expected outcome per question, optional `conversation` and `source_contains`) and scoring. |
| `chatbot/management/commands/` | `check_corpus` (read-only data and retrieval readiness) and `run_eval` (live evaluation inside a transaction rolled back by default). |
| `config/` | Settings, URLs, `test_runner.py`, auth tests (`config/tests.py`). |

URLs: `/` and `/c/<uuid>/` (workspace), `/chat/ask/`, `/accounts/login/`, `/accounts/logout/` (POST only), `/admin/`.

## Data model (`chatbot/models.py`)

- `Document` (approved public source; `status` draft/approved/archived; unique `source_url`) and `DocumentPassage` (`chunk_index`, `content`, `page_number`, `section_heading`). Passages are `PROTECT`ed by their document and searchable only when the document is `approved`. Ingestion is not built; the user loads these tables themselves.
- `Conversation` (per user), `ChatExchange` (the audit log: question, standalone query, category, outcome, answer, disclaimer, retrieved passage ids, model, latency), `ExchangeCitation` (snapshot of title, URL, section, page, and passage text; `passage` is `SET_NULL`, so the log survives re-ingestion).
- `Document`, `DocumentPassage`, `Conversation`, and `ChatExchange` have UUID primary keys; `ExchangeCitation` uses the default auto id. Foreign keys that are already covered by a composite or unique index use `db_index=False`; keep that pattern.
- Migrations: 0001 documents, 0002/0003/0005 enable RLS with `RunSQL`, 0004 chat log, 0006 database defaults.
- **Documents are loaded outside Django** (Supabase dashboard, SQL, CSV), so the document tables have `db_default`s (`gen_random_uuid()`, `now()`, `'draft'`) next to the Python defaults. Keep both when adding columns to those tables. SQL updates don't refresh `updated_at`.

## Frontend

Django templates + Bootstrap 5.3.3 (jsDelivr, with SRI hashes) + `static/css/sightbridge.css` (brand tokens as `--sb-*` variables) + `static/js/sightbridge.js` (password toggle, composer autogrow, suggestion fill, sending questions with `fetch`). Fonts: Newsreader and Public Sans from Google Fonts. `chatbot/templates/chatbot/_exchange.html` renders one exchange and is shared by saved history and the `ask` response, so the two always match. Model text is never inserted as raw HTML. Read the `frontend-design` skill before UI work (see AGENTS.md).

## Database and deployment

- **Dev and production share one Supabase project** (the one in `.mcp.json`). `migrate` and any data you write affect both. Django connects with the session pooler URI (port 5432); the transaction pooler (6543) is not supported.
- There is no SQLite fallback; missing or non-TLS config raises `ImproperlyConfigured`.
- Tests create a temporary `test_postgres` database on the same server. Supavisor keeps a connection to it open, so `config/test_runner.py` drops it with `DROP DATABASE ... WITH (FORCE)`. If a failed run leaves `test_postgres` behind, drop it that way.
- Railway runs `railway.json`'s start command (collectstatic, then gunicorn); WhiteNoise serves static files; `.python-version` pins 3.13. Migrations are not run on deploy. Gunicorn does not run on Windows, so production mode is only checked locally with `runserver`.
- `DEBUG` is off unless `DJANGO_DEBUG=true`; with it off, cookies are secure-only and HTTP redirects to HTTPS.

## Conventions and pitfalls

- **New table or model**: add a migration that enables RLS (copy `0005_enable_row_level_security_chat_log.py`). `test_row_level_security_enabled_on_all_public_tables` in `chatbot/tests.py` fails if any `public` table lacks it.
- **New environment variable**: add it to `config/settings.py`, `.env.example`, and the README variable table.
- **Changing answer behavior**: keep guardrails in code (`safety.py`, `answering.py`), not just in prompts, and add a `FakeLLM` pipeline test in `chatbot/test_chat.py`.
- **Full-text queries must use `passage_search_vector()`** from `chatbot/models.py`, the same expression as the GIN index.
- **psycopg 3**: pass lists with `= ANY(%s)`; `IN %s` with a tuple does not work.
- **Citation markers** have no space before them (`text[1].`); the validator and the tests depend on that.
- **Gemini**: model ids come from Google's pricing page (2026-10-26). Measured on 2026-10-27 with the free tier: `gemini-3.8-flash` gave 503/504 or took over 100 s, `gemini-3.6-flash` took 3-5 s with occasional 503, `gemini-3.5-flash-lite` took about 1 s, and `gemini-2.5-flash` is closed to new users. Thinking level `MINIMAL` is rejected by 3.8 Flash. The free tier may use prompts to improve Google's products, which is why the UI warns users not to enter client details.
- **Checking Gemini by hand**: tests never call Gemini. To check the key and models, call `chatbot.llm.get_llm()` from a script (with `PYTHONPATH` and `DJANGO_SETTINGS_MODULE` set) and run `triage()` and `answer()` on in-memory sample passages; each check costs a few free-tier requests.
- **Line endings**: files are LF and Git warns about CRLF conversion (`core.autocrlf=true`). When editing the README from a script, read and write bytes; avoid `sed` with backslashes.
- **Tooling**: the Bash tool is Git Bash, PowerShell is separate. Scripts run outside `manage.py` need `PYTHONPATH` set to the repo root and `DJANGO_SETTINGS_MODULE=config.settings`. The `gh` CLI is not installed, so pull requests are opened in the browser.
- README screenshots in `docs/screenshots/` were captured by rendering the templates with sample data and headless Chrome; the scripts are not in the repo. The answer screenshot is sample data, and the README says so.
- Never put credentials in the repo. Three regular test users exist in the shared database; their passwords are not stored anywhere in the repo.

## Status and loose ends

As of 2026-10-27 the pipeline, login, admin log, and Railway config are implemented and committed on `supabase-connection` (no pull request yet). `GEMINI_API_KEY` is set locally and the live check passed on 2026-10-27. A superuser exists. The user is loading documents through the Supabase dashboard; then follow README "Production readiness test" (`check_corpus`, tune `MIN_RANK`, adjust `eval_questions.json`, `run_eval`, deploy to Railway). Verify with `git status` and the database before relying on this list.

Known small items: the rate limit is per process (in-memory cache); there is no login throttling; `MIN_RANK` is uncalibrated until real data is loaded.
