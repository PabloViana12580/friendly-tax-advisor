# CLAUDE.md

Sightbridge "Friendly Tax Advisor": a login-protected Q&A chat for financial advisors about 1031 exchanges, Delaware Statutory Trusts (DSTs), 721 exchanges, and REITs. It answers only from an approved document set, cites the passages it used, and logs every exchange for admins. Django + Supabase PostgreSQL + Gemini. It is a take-home exercise. `README.md` is written for the firm's stakeholders (demo link and account, sources, how it was built, results, costs, next steps) and stays non-technical; technical documentation (architecture, setup, environment variables, loading documents, readiness test, Render deployment) is in `docs/DEVELOPER.md`.

Project rules (stack, secrets, row level security, frontend skill, chatbot guardrails) are in `AGENTS.md` and apply here:

@AGENTS.md

## Commands

Windows with Git Bash. The virtualenv is `.venv` (Python 3.13); call its interpreter directly, no activation needed.

```bash
.venv/Scripts/python.exe manage.py runserver                     # needs .env
.venv/Scripts/python.exe manage.py check
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run
.venv/Scripts/python.exe manage.py migrate                       # writes to the shared Supabase DB (see Database)
.venv/Scripts/python.exe manage.py test --noinput                # all 114 tests, about 6 minutes
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
triage (Gemini flash-lite)  -> educational | personalized_advice | out_of_scope, a standalone query, and
                               search_terms (phrases in the sources' formal wording, e.g. boot -> money or unlike property)
route                       -> out_of_scope: fixed message. personalized_advice: decline text + continue with the general question
retrieve (Postgres FTS)     -> each triage search phrase + the question, IDF-weighted, merged by rank fusion;
                               approved documents only, top 8; none -> "not found" without calling Gemini again
answer (Gemini 3.6 flash)   -> sees only labelled passages P1..P8, returns JSON {status, answer with [P#], cited};
                               on 429/500/503/504 or timeout, one retry on the fallback model (flash-lite)
validate (safety.py)        -> drops markers for passages not sent, renumbers [P#] to [n]; no valid citation -> "not found"
disclaimer + audit          -> disclaimer added by code on every outcome; exchange and citation snapshots saved, including errors
```

| File | Responsibility |
| --- | --- |
| `chatbot/llm.py` | The only module that talks to Gemini (`google-genai`). Prompts, pydantic response schemas, `get_llm()`, per-call timeouts. `answer()` returns `(GroundedAnswer, model_used)` and does the single fallback retry. `LLMUnavailable.retryable` separates transient failures from permanent ones (bad key, unknown model). No SDK retries. |
| `chatbot/retrieval.py` | `search(text)`: one SQL query over the stored `search_vector` column (GIN index); stems the text, weights each word by IDF over all passages, ranks approved passages by the weighted average `ts_rank`. `search_combined(texts)`: searches each triage search phrase and the question separately and merges with reciprocal rank fusion. |
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

- `Document` (approved public source; `status` draft/approved/archived; unique `source_url`) and `DocumentPassage` (`chunk_index`, `content`, `page_number`, `section_heading`, and `search_vector`, a stored column generated by PostgreSQL from heading (weight A) and content (weight B)). Passages are `PROTECT`ed by their document and searchable only when the document is `approved`. Ingestion is not built; the user loads these tables themselves.
- `Conversation` (per user), `ChatExchange` (the audit log: question, standalone query, category, outcome, answer, disclaimer, retrieved passage ids, model, latency), `ExchangeCitation` (snapshot of title, URL, section, page, and passage text; `passage` is `SET_NULL`, so the log survives re-ingestion).
- `Document`, `DocumentPassage`, `Conversation`, and `ChatExchange` have UUID primary keys; `ExchangeCitation` uses the default auto id. Foreign keys that are already covered by a composite or unique index use `db_index=False`; keep that pattern.
- Migrations: 0001 documents, 0002/0003/0005 enable RLS with `RunSQL`, 0004 chat log, 0006 database defaults, 0007 generated `search_vector` column and its GIN index.
- Loaded corpus (2026-10-27): six approved sources, 481 passages (Pub 544, FS-2008-18, § 1.1031(k)-1, IRB 2004-33 with Rev. Rul. 2004-86, Rev. Rul. 99-5, SEC REIT investor bulletin). The CSVs are in `chatbot_sources/`. No UPREIT/OP-unit material, so those questions get "not found" (the `upreit` eval case checks this).
- **Documents are loaded outside Django** (Supabase dashboard, SQL, CSV), so the document tables have `db_default`s (`gen_random_uuid()`, `now()`, `'draft'`) next to the Python defaults. Keep both when adding columns to those tables. SQL updates don't refresh `updated_at`.

## Frontend

Django templates + Bootstrap 5.3.3 (jsDelivr, with SRI hashes) + `static/css/sightbridge.css` (brand tokens as `--sb-*` variables) + `static/js/sightbridge.js` (password toggle, composer autogrow, suggestion fill, sending questions with `fetch`). Fonts: Newsreader and Public Sans from Google Fonts. `chatbot/templates/chatbot/_exchange.html` renders one exchange and is shared by saved history and the `ask` response, so the two always match. Model text is never inserted as raw HTML. Read the `frontend-design` skill before UI work (see AGENTS.md).

## Database and deployment

- **Dev and production share one Supabase project** (the one in `.mcp.json`). `migrate` and any data you write affect both. Django connects with the session pooler URI (port 5432); the transaction pooler (6543) is not supported.
- There is no SQLite fallback; missing or non-TLS config raises `ImproperlyConfigured`.
- Tests create a temporary `test_postgres` database on the same server. Supavisor keeps a connection to it open, so `config/test_runner.py` drops it with `DROP DATABASE ... WITH (FORCE)`. If a failed run leaves `test_postgres` behind, drop it that way.
- Hosting is a Render free web service, configured in the dashboard. The build and start commands, health check path, and environment variables are in `docs/DEVELOPER.md` "Deploy to Render" (gunicorn: 1 worker, 4 threads, 90 s timeout). `/healthz` (in `config/urls.py`) is exempt from the HTTPS redirect. `RENDER_EXTERNAL_HOSTNAME` is added to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` in settings. WhiteNoise serves static files, and `.python-version` pins 3.13. Migrations are not run on deploy, and pre-deploy commands are paid-only. Railway was dropped (no free plan for the user). Gunicorn does not run on Windows, so production mode is only checked locally with `runserver`.
- `DEBUG` is off unless `DJANGO_DEBUG=true`; with it off, cookies are secure-only and HTTP redirects to HTTPS.

## Conventions and pitfalls

- **New table or model**: add a migration that enables RLS (copy `0005_enable_row_level_security_chat_log.py`). `test_row_level_security_enabled_on_all_public_tables` in `chatbot/tests.py` fails if any `public` table lacks it.
- **New environment variable**: add it to `config/settings.py`, `.env.example`, and the variable table in `docs/DEVELOPER.md`.
- **Changing answer behavior**: keep guardrails in code (`safety.py`, `answering.py`), not just in prompts, and add a `FakeLLM` pipeline test in `chatbot/test_chat.py`.
- **Full-text queries must use the `search_vector` column** (GIN-indexed), not recompute `to_tsvector`; `passage_search_vector()` is only the expression that generates it.
- **psycopg 3**: pass lists with `= ANY(%s)`; `IN %s` with a tuple does not work.
- **Citation markers** have no space before them (`text[1].`); the validator and the tests depend on that.
- **Gemini**: model ids come from Google's pricing page (2026-10-26). Measured on 2026-10-27 with the free tier: `gemini-3.8-flash` gave 503/504 or took over 100 s, `gemini-3.6-flash` took 3-5 s with occasional 503, `gemini-3.5-flash-lite` took about 1 s, and `gemini-2.5-flash` is closed to new users. Thinking level `MINIMAL` is rejected by 3.8 Flash. The free tier may use prompts to improve Google's products, which is why the UI warns users not to enter client details.
- **Checking Gemini by hand**: tests never call Gemini. To check the key and models, call `chatbot.llm.get_llm()` from a script (with `PYTHONPATH` and `DJANGO_SETTINGS_MODULE` set) and run `triage()` and `answer()` on in-memory sample passages; each check costs a few free-tier requests.
- **Line endings**: files are LF and Git warns about CRLF conversion (`core.autocrlf=true`). When editing the README or `docs/DEVELOPER.md` from a script, read and write bytes; avoid `sed` with backslashes.
- **Tooling**: the Bash tool is Git Bash, PowerShell is separate. Scripts run outside `manage.py` need `PYTHONPATH` set to the repo root and `DJANGO_SETTINGS_MODULE=config.settings`. The `gh` CLI is not installed, so pull requests are opened in the browser.
- README screenshots in `docs/screenshots/` were captured by rendering the templates with sample data and headless Chrome; the scripts are not in the repo. The answer screenshot is sample data, and the README says so. The login screenshot was re-captured from the running app at 125% scale (1842x1027).
- Never put credentials in the repo, with one exception the owner chose: the demo account `morgan.hale` (regular, non-staff) and its password are listed in the README for the live demo at https://friendly-tax-advisor.onrender.com/. Two other regular test users exist in the shared database; their passwords are not stored anywhere in the repo.

## Status and loose ends

As of 2026-10-27 work is on `main`. The six sources are loaded and approved, `run_eval` passes locally, and a superuser exists. Deployed on Render at https://friendly-tax-advisor.onrender.com/ (`docs/DEVELOPER.md` "Deploy to Render"). Then follow `docs/DEVELOPER.md` "Production readiness test" steps 5-7. Verify with `git status` and the database before relying on this list.

Known small items: the rate limit is per process (in-memory cache); there is no login throttling; the free tier allows only 20 `gemini-3.6-flash` requests per day per project, after which every answer comes from the `flash-lite` fallback (measured 2026-10-27).
