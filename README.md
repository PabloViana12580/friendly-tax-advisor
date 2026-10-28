# Friendly Tax Advisor

An advisor-facing Q&A assistant for educational questions about Section 1031 exchanges, Delaware Statutory Trusts (DSTs), Section 721 contributions to a REIT operating partnership (often described as a “721 exchange”), and real estate investment trusts (REITs).

> **Status:** Sign-in, the grounded chat pipeline (retrieval, Gemini answers with validated citations, advice handling, disclaimer), and the admin-only exchange log are implemented. The six approved sources below are loaded, and the live evaluation (`run_eval`) passes locally. See [Production readiness test](#production-readiness-test).

## Part 1: Advisor Q&A assistant

The application is a simple, authenticated chat for financial advisors. It answers only from its approved source set, cites the passages and source links supporting each answer, and says when the answer is not present in those materials. It does not fill gaps with uncited model knowledge.

### Answer guardrails

- Provide general educational information only. Do not give personalized tax, legal, or investment advice, determine whether a specific transaction qualifies, or recommend an investment.
- When a user asks what they personally should do, decline to make that decision. Offer relevant general information from the approved sources when possible and suggest consulting a qualified tax, legal, or investment professional.
- Include a short disclaimer with every answer: “For general educational purposes only; this is not tax, legal, or investment advice.”
- If the approved sources do not support an answer, say so clearly and do not guess.
- Record each question, answer, cited sources, and timestamp in an admin-only conversation log.

## Approved source set

The chat answers only from these six public documents, loaded into the database as approved (481 passages). The passage CSV files are in `chatbot_sources/`.

1. [IRS Publication 544 (2025), Sales and Other Dispositions of Assets](https://www.irs.gov/publications/p544) (176 passages): like-kind exchanges, deferred exchanges and qualified intermediaries, partially nontaxable exchanges (money or unlike property received, often called "boot"), basis, and reporting.
2. [IRS Fact Sheet FS-2008-18, Like-Kind Exchanges Under IRC Section 1031](https://www.irs.gov/pub/irs-news/fs-08-18.pdf) (12 passages): a plain-language overview covering who qualifies, exchange structures, the 45-day and 180-day limits, basis, and reporting on Form 8824.
3. [26 C.F.R. § 1.1031(k)-1, Treatment of deferred exchanges](https://www.ecfr.gov/current/title-26/chapter-I/subchapter-A/part-1/subject-group-ECFRbf83dcc4bd89326/section-1.1031%28k%29-1) (51 passages): the Treasury regulation on identification and receipt rules, safe harbors including qualified intermediaries, and basis in deferred exchanges.
4. [Internal Revenue Bulletin 2004-33](https://www.irs.gov/irb/2004-33_IRB) (209 passages): contains Revenue Ruling 2004-86 on the tax classification of a Delaware statutory trust and the Section 1031 treatment of DST interests. The bulletin's other items (health savings accounts, S corporations, procedures) are loaded too, but questions about them are out of scope.
5. [Revenue Ruling 99-5, Section 721 nonrecognition of gain or loss on contribution](https://www.irs.gov/pub/irs-drop/rr-99-5.pdf) (16 passages): how Section 721 and related sections apply when a single-member LLC becomes a partnership.
6. [SEC Investor Bulletin: Real Estate Investment Trusts (REITs)](https://www.sec.gov/files/reits.pdf) (17 passages): what a REIT is, how a company qualifies, equity, mortgage, and hybrid REITs, publicly traded versus non-traded REITs, and tax considerations for shareholders.

**Coverage limits.** The set covers REIT basics but has no material on UPREIT operating partnerships or OP units. Questions about 721 exchanges into a REIT's operating partnership are answered only from the general Section 721 rule in Revenue Ruling 99-5 and the SEC's REIT basics; anything beyond that gets the "not covered" response. Revenue Ruling 2004-86 applies to the facts it describes and does not establish the terms or tax result of any other DST, and the SEC bulletin is general investor education, not guidance on any specific REIT.

## How the chat works

The language model never reads the database. Django retrieves the passages and sends the model only those, so "answer only from the documents" is enforced by code rather than by the prompt alone.

```
POST /chat/ask/
 1. Triage       Gemini (flash-lite) classifies the question as educational,
                 personalized advice, or out of scope, rewrites follow-ups
                 into a standalone query using the last 3 turns, and lists
                 2-4 search phrases in the sources' formal wording ("boot" ->
                 "money or unlike property received").
                 If Gemini is unavailable, a keyword rule decides.
 2. Route        Out of scope: fixed scope message, no further calls.
                 Personalized advice: decline, then continue with the
                 depersonalized general question.
 3. Retrieve     PostgreSQL full-text search (GIN index) over passages of
                 approved documents only. Each search phrase and the question
                 are searched separately, words weighted by rarity (IDF), and
                 the lists merged (reciprocal rank fusion); top 8 are kept.
                 No matches: "not in the approved sources", no answer call.
 4. Answer       Gemini (3.6 flash) gets only the labelled passages P1..P8 and
                 returns JSON: status, answer with [P#] markers, cited labels.
                 If it is overloaded or times out, flash-lite is tried once.
 5. Validate     Markers that don't match a passage that was sent are removed.
                 An answer with no valid citation becomes "not found".
 6. Disclaimer   Added by code to every response, whatever the outcome.
 7. Audit log    Question, answer, outcome, retrieved passages, and a snapshot
                 of each cited source are saved with a timestamp.
```

Each step lives in its own module in `chatbot/`: `llm.py` (Gemini client and prompts), `retrieval.py`, `safety.py` (fixed texts, keyword rule, citation validation), `answering.py` (the pipeline above), and `audit.py` (logging).

**Personalized advice.** The triage model flags questions that ask what a specific person or client should do, whether a specific transaction qualifies, what someone's own tax result would be, or for a product or investment recommendation. The answer always starts with a fixed decline and a referral to a qualified professional. It then answers the general version of the question from the sources, with citations, when the sources cover it. If Gemini can't classify a question, a keyword rule ("should I", "my client", "do you recommend", ...) catches the common phrasings.

**Free-tier limits.** Each question normally uses at most two Gemini calls, and fewer when it is out of scope or retrieval finds nothing. The free tier is often overloaded: in testing, `gemini-3.8-flash` returned 503 and 504 errors and took over 100 seconds, so answers use `gemini-3.6-flash` (about 3 to 5 seconds). When the answer model returns 429, 500, 503, or 504, or times out (20 seconds), one retry goes to `gemini-3.5-flash-lite`; other errors, such as a bad key, are not retried. The exchange log records which model answered. Each user can ask 10 questions a minute (`CHAT_RATE_LIMIT_PER_MINUTE`). When Gemini is rate-limited or unreachable, the user is asked to try again, and the exchange is still logged with outcome `error`.

**Privacy.** Gemini's free tier may use prompts to improve Google's products, so the UI asks users not to include client names or personal details.

### Admin conversation log

Every exchange is visible to staff at `/admin/` under **Chat exchanges**. Each entry shows the timestamp, user, outcome, category, question, standalone query, answer, disclaimer, retrieved passage IDs, model, latency, and the cited sources. Sources are snapshotted, so the log stays accurate if documents are re-ingested. The log is read-only in the admin: entries can't be added, edited, or deleted there.

### What's left out, and what I'd build next

- **No ingestion pipeline.** Documents are loaded by hand (see [Loading documents](#loading-documents)). Next: a script that downloads each source, splits it into passages, and records `content_hash` so changed sources can be re-ingested.
- **Lexical retrieval only.** Full-text search misses paraphrases ("swap" vs. "like-kind exchange"). Triage search phrases in the sources' wording and IDF-weighted ranking cover the common cases in the evaluation set. Next: add pgvector embeddings for hybrid search if misses show up in the log.
- **The evaluation is manual.** `run_eval` checks the outcome of a fixed question set, but not the wording or accuracy of answers, and it isn't run in CI. Next: grade answers against expected facts and run it on every change.
- **No streaming.** Answers appear when complete, usually in a few seconds.
- **No login rate limiting or account lockout.**
- **The rate limit is per process.** It uses Django's in-memory cache, so with several workers the effective limit is higher. Next: a shared cache.
- **Free-tier model.** For real client use, move to a paid tier whose terms exclude prompts from training.

## Screenshots

The workspace screenshots show a sample signed-in user.

### Sign in

![Sign-in page on desktop: the form on the left and the approved source list on the right](docs/screenshots/login-desktop.png)

A failed sign-in shows field-level messages:

![Sign-in page showing a required-field error under the password](docs/screenshots/login-error-desktop.png)

### Chat workspace

A new conversation starts with suggested questions:

![Chat workspace on desktop: sidebar with New chat and conversations, welcome question, and suggested questions](docs/screenshots/workspace-desktop.png)

A conversation showing a cited answer, a declined advice request with general information, and a question the sources don't cover. The answer text in this screenshot is illustrative; it was rendered from sample data, not generated by the model:

![Chat thread on desktop: an answer with numbered citations and sources, a declined advice request followed by cited general information](docs/screenshots/chat-answer-desktop.png)

### Mobile

| Sign in | Workspace | Answer | Navigation drawer |
| --- | --- | --- | --- |
| <img src="docs/screenshots/login-mobile.png" alt="Sign-in page on a phone" width="200"> | <img src="docs/screenshots/workspace-mobile.png" alt="Chat workspace on a phone" width="200"> | <img src="docs/screenshots/chat-answer-mobile.png" alt="Cited answer on a phone" width="200"> | <img src="docs/screenshots/workspace-mobile-menu.png" alt="Navigation drawer with conversations open on a phone" width="200"> |

## Development setup

Requirements: Python 3.13, a Supabase project used for **development**, and optionally a local PostgreSQL server for running database tests. Contributor and agent rules are in [AGENTS.md](AGENTS.md).

### Install

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
```

### Configure environment variables

```bash
cp .env.example .env
```

Settings load `.env` from the repository root; variables already set in the shell take precedence. `.env` is git-ignored. Never commit it.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Yes, unless `DB_*` is used | Supabase PostgreSQL connection string |
| `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`, `DB_NAME` | Alternative to `DATABASE_URL` | Individual connection settings (port defaults to `5432`, name to `postgres`) |
| `DB_SSLMODE` | No | `require` (default), `verify-ca` or `verify-full`. Weaker modes are allowed only for `localhost` |
| `DB_SSLROOTCERT` | For `verify-*` | Path to Supabase's CA certificate |
| `DB_CONN_MAX_AGE` | No | Persistent-connection lifetime in seconds (default `60`) |
| `DJANGO_SECRET_KEY` | Yes, unless `DJANGO_DEBUG=true` | Django secret key |
| `DJANGO_DEBUG` | No | `true` for local development only. Off by default |
| `DJANGO_ALLOWED_HOSTS` | For a custom domain | Comma-separated host names (default `localhost,127.0.0.1`). On Render, the `onrender.com` host is added automatically from `RENDER_EXTERNAL_HOSTNAME` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | For a custom domain | Comma-separated origins, e.g. `https://advisor.example.com`. The Render origin is added automatically |
| `DJANGO_SECURE_SSL_REDIRECT` | No | Redirect HTTP to HTTPS. Defaults to on when `DEBUG` is off |
| `GEMINI_API_KEY` | For answers | Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey). Without it, every question gets the "busy" response |
| `GEMINI_ANSWER_MODEL` | No | Default `gemini-3.6-flash` |
| `GEMINI_TRIAGE_MODEL` | No | Default `gemini-3.5-flash-lite` |
| `GEMINI_FALLBACK_MODEL` | No | Tried once when the answer model is overloaded or times out. Default `gemini-3.5-flash-lite`; empty disables it |
| `GEMINI_TRIAGE_TIMEOUT_SECONDS`, `GEMINI_ANSWER_TIMEOUT_SECONDS` | No | Per-call timeouts, default `10` and `20`. Keep triage + 2 × answer under gunicorn's 90-second timeout |
| `CHAT_RATE_LIMIT_PER_MINUTE` | No | Questions per user per minute (default `10`) |

Use the **Session pooler** connection string from Supabase Dashboard → **Connect**. It works on IPv4 networks, while the direct connection (`db.<ref>.supabase.co`) is IPv6-only unless the IPv4 add-on is enabled. The transaction pooler (port `6543`) is not supported by this configuration, because it does not support prepared statements or server-side cursors.

Use the database password from the Supabase dashboard. Do not use the `service_role` key or any other Supabase API key; those keys are not database credentials and must never reach frontend code.

If no database is configured, Django raises `ImproperlyConfigured`. There is intentionally no SQLite fallback.

### Verify the connection and apply migrations

Confirm that `DATABASE_URL` points to the development project, never production, before running `migrate`.

```bash
python manage.py check --database default   # connects and runs database checks
python manage.py showmigrations              # lists applied/unapplied migrations
python manage.py migrate                     # applies Django and chatbot migrations
```

### Run the app

```bash
python manage.py runserver
```

| Route | Name | Access | Purpose |
| --- | --- | --- | --- |
| `/` | `chatbot:workspace` | Signed-in users | New conversation (GET only) |
| `/c/<id>/` | `chatbot:conversation` | The conversation's owner | Reopen a saved conversation; other users get 404 |
| `/chat/ask/` | `chatbot:ask` | Signed-in users | JSON endpoint the composer posts to (POST with CSRF token) |
| `/accounts/login/` | `login` | Everyone | Sign-in page; signed-in users are redirected to `/` |
| `/accounts/logout/` | `logout` | Signed-in users | Sign out (POST with CSRF token only) |
| `/admin/` | `admin:index` | Staff users | Django admin |

Anonymous visitors are redirected to the sign-in page, and `next` redirects only go to this site. Session cookies are HttpOnly, last 8 hours, and are marked secure when `DEBUG` is off. There is no public registration or password reset; accounts are managed by an administrator.

### User accounts

Accounts live in Django's `auth_user` table in Supabase. To create an administrator, run this interactive command; it prompts for the password, so the password never appears in files or shell history:

```bash
python manage.py createsuperuser
```

Create regular users from the Django admin, or with Django's `User.objects.create_user()` in `python manage.py shell`. Both hash the password, and new users get `is_staff=False` and `is_superuser=False`. Regular users can use the workspace but not the admin. Never put real usernames or passwords in this repository.

### Frontend

Pages are Django templates styled with Bootstrap 5.3 (loaded from jsDelivr with integrity hashes) and the Sightbridge design system in `static/css/sightbridge.css`. The four brand colors are CSS variables there. Shared layout lives in `templates/base.html` and `templates/components/`. `static/js/sightbridge.js` adds the password visibility toggle, composer auto-resize, suggested-question fill, and sending questions. The answer HTML is rendered and escaped by the server (`chatbot/templates/chatbot/_exchange.html`, shared with saved history); model text is never inserted as raw HTML. The mobile navigation uses Bootstrap's offcanvas component.

### Schema

The `chatbot` app owns all application models in `chatbot/models.py`. Django migrations are the source of truth for this schema.

- **`Document`** is an approved public source: `title`, `publisher`, unique `source_url`, `status` (`draft` / `approved` / `archived`, also enforced by a check constraint), `content_hash`, `retrieved_at`, and timestamps.
- **`DocumentPassage`** is an extracted passage: `document` (FK), `chunk_index`, `content`, `page_number`, `section_heading`, `created_at`. `(document, chunk_index)` is unique, and the unique index also serves FK lookups. A document that still has passages cannot be deleted (`PROTECT`). Retire sources by archiving them so that citations keep resolving.
- **`Conversation`** groups a user's exchanges for follow-up questions.
- **`ChatExchange`** is the audit log: question, standalone query, category, outcome (`answered` / `not_found` / `declined_advice` / `out_of_scope` / `error`), answer, disclaimer, retrieved passage IDs, model, latency, and timestamp. Deleting a user or conversation that has exchanges is blocked (`PROTECT`).
- **`ExchangeCitation`** stores each cited source with a snapshot of the document title, URL, section, page, and passage text.
- Full-text search uses `search_vector`, a stored column that PostgreSQL generates from `section_heading` (weight A) and `content` (weight B) on every insert and update, including SQL and CSV loads, with a GIN index on it (migration `0007`). `chatbot/retrieval.py` queries it directly.
- Migrations `0002`, `0003`, and `0005` enable row level security, with no policies, on every chatbot table and on Django's built-in tables (`auth_*`, `django_*`). This blocks Supabase's Data API (`anon` / `authenticated` roles), while Django, which connects as the table owner, is unaffected. Any new table in `public` also needs RLS enabled; the test suite fails if one does not have it.

### Tests

```bash
python manage.py test
```

This covers sign-in, sign-out, workspace access, admin separation, user creation, the database configuration, the document schema, retrieval, citation validation, every chat outcome, the ask endpoint (CSRF, ownership, input limits, rate limit), answer rendering, and the read-only admin log. Gemini is replaced by a fake in tests, so no API calls are made. Database tests run in a temporary `test_postgres` database on the configured server. The real database is not touched, and the temporary one is dropped afterwards. This works with the Supabase session pooler: `config/test_runner.py` drops the test database with `WITH (FORCE)`, because the pooler keeps a connection to it open. A local PostgreSQL server also works:

```bash
DATABASE_URL=postgresql://postgres:YOUR_LOCAL_PASSWORD@localhost:5432/postgres DB_SSLMODE=disable python manage.py test
```

The configuration and model-definition tests do not need a database:

```bash
python manage.py test chatbot.tests.DatabaseConfigTests chatbot.tests.ModelDefinitionTests
```

### Loading documents

Load the documents into `chatbot_document` and their passages into `chatbot_documentpassage`, from the Supabase dashboard (Table Editor, SQL editor, or CSV import) or with Django. Only documents with `status = 'approved'` are searched, so you can load as `draft`, check, then approve.

**`chatbot_document`**, one row per source:

| Column | Fill in? | Notes |
| --- | --- | --- |
| `title` | Required | Shown in citations, up to 500 characters |
| `publisher` | Required | e.g. IRS, SEC, FINRA |
| `source_url` | Required | The public page a reader should open; must be unique |
| `status` | Optional | `draft` (default), `approved`, or `archived` |
| `retrieved_at` | Recommended | When you downloaded the source |
| `content_hash` | Optional | e.g. SHA-256 of the downloaded file, to detect changes |
| `id`, `created_at`, `updated_at` | Leave empty | Filled in by the database |

**`chatbot_documentpassage`**, one row per passage:

| Column | Fill in? | Notes |
| --- | --- | --- |
| `document_id` | Required | The `id` of its document |
| `chunk_index` | Required | 0, 1, 2, ... in reading order; unique within a document |
| `content` | Required | The passage text; must not be empty |
| `section_heading` | Recommended | Shown in citations |
| `page_number` | Optional | 1 or higher; shown in citations |
| `id`, `created_at` | Leave empty | Filled in by the database |

Guidance for good answers and citations:

- Split each source into passages of about 150 to 400 words, preferably along section boundaries. Each answer sends up to 8 passages to Gemini, so very long passages waste quota, and very short ones lose context.
- Search uses PostgreSQL's `english` text-search configuration on `section_heading` and `content`, so keep the source's own terms ("like-kind", "Section 1031", "Delaware statutory trust") and fill in descriptive headings.
- Use the SQL editor or Table Editor (they run as the database owner). Inserts through Supabase's REST API with the `anon` key are rejected on purpose, because every table has row level security without policies.
- If you change rows with SQL later, set `updated_at = now()` yourself; only Django saves refresh it.
- To retire a source, set its status to `archived`. A document that still has passages can't be deleted, which keeps logged citations intact.

Example of adding a sixth source (SQL editor):

```sql
with doc as (
  insert into chatbot_document (title, publisher, source_url, retrieved_at)
  values ('Instructions for Form 8824', 'IRS', 'https://www.irs.gov/instructions/i8824', now())
  returning id
)
insert into chatbot_documentpassage (document_id, chunk_index, section_heading, content)
select id, 0, 'Deferred exchanges', 'In a deferred exchange, ...' from doc;

-- When the passages look right:
update chatbot_document set status = 'approved', updated_at = now()
where source_url = 'https://www.irs.gov/instructions/i8824';
```

For CSV import, import documents first, then passages with the `document_id` values the documents received. Leave `id` and the timestamp columns out of the column mapping.

## Production readiness test

Run this after loading the documents. `check_corpus` is read-only. `run_eval` calls Gemini (about 2 requests per question) and saves nothing unless you pass `--record`.

1. **Check the data.** `python manage.py check_corpus`

   It lists documents and passages and flags the following.
   - **Errors:** no approved documents, an approved document with no passages, whitespace-only passages, a missing `GEMINI_API_KEY`.
   - **Warnings:** passages outside 30 to 600 words, gaps in `chunk_index`, empty `retrieved_at`, no superuser.

   It also shows how each document and each evaluation question ranks in search. It exits with an error status when something would break the chat.
2. **Tune retrieval if needed.** If evaluation questions that your sources do cover are reported as matching nothing above `MIN_RANK`, lower `MIN_RANK` in `chatbot/retrieval.py`. On the six loaded sources, relevant passages rank 0.2 to 0.6 and unrelated ones down to about 0.01, so the 0.02 cutoff only drops incidental matches. Whether the passages actually answer the question is left to the answer model.
3. **Adjust the questions.** Edit `chatbot/eval_questions.json` so that the questions expecting `answered` match what your sources cover. A case can also require a cited source with `"source_contains": "rr-99-5"`. Follow-up questions share a `"conversation"` key.
4. **Run the live evaluation.** `python manage.py run_eval`

   Add `-v 2` to read every answer and its sources, `--only ID ...` to rerun cases, and `--delay 8` if Gemini rate-limits you. A case that fails because Gemini was busy is marked as such; run it again.
5. **Deploy to Render** ([below](#deploy-to-render)). Production shares the database with development, so `check_corpus` and `run_eval` results apply to it as well. Then ask a few evaluation questions in the browser.
6. **Record a demo** for the admin log if you want one: `python manage.py run_eval --record --user <username>`. Then confirm the exchanges, sources and timestamps in `/admin/` → **Chat exchanges**.
7. **Check Supabase's security advisor.** It should report no tables with row level security disabled.

## Deploy to Render

The app runs on a [Render](https://render.com) free web service with gunicorn, and WhiteNoise serves the static files. The database stays on Supabase. Render reads `.python-version` and installs Python 3.13.

### Service settings

Create a **Web Service** from this GitHub repository, then fill in:

| Field | Value | Why |
| --- | --- | --- |
| Language | Python 3 | |
| Branch | `main` | Each push to `main` redeploys automatically |
| Region | Oregon (US West) | Same region as the Supabase database (`us-west-2`), so database queries stay fast |
| Root directory | Leave empty | `manage.py` is at the repository root |
| Build command | `pip install -r requirements.txt && python manage.py collectstatic --noinput` | Installs dependencies and collects static files into the build |
| Start command | `gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 90 --access-logfile -` | One process with 4 threads fits the free instance's 512 MB, and keeps the in-memory rate limit accurate. Up to 4 questions are answered at once while others wait on Gemini. The 90-second timeout covers the slowest question (triage 10 s + answer 20 s + fallback 20 s) |
| Instance type | Free | |
| Health check path | `/healthz` | Returns `ok` without a login or a database query |
| Pre-deploy command | Leave empty | Available on paid instances only. Migrations aren't run on deploy anyway (see below) |
| Secret files | None | All configuration is environment variables. Never upload `.env`: the app loads a `.env` file at the repository root, and the local one turns debug mode on |

### Environment variables

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | The same Supabase **session pooler** URI as in your local `.env` (port `5432`). The direct connection is IPv6-only, and the transaction pooler (port `6543`) isn't supported |
| `DJANGO_SECRET_KEY` | A new random value, not your local one. Use Render's **Generate** button |
| `GEMINI_API_KEY` | Your Gemini API key |

That is all that's required. Don't set `DJANGO_DEBUG`. Render sets `RENDER_EXTERNAL_HOSTNAME` (e.g. `your-app.onrender.com`) itself, and the settings add it to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`. Set `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` only if you add a custom domain. The optional Gemini and rate-limit variables in the table above keep their defaults.

### Migrations and accounts

Production uses the same Supabase database as development, so it already has every migration, the documents, and the user accounts. Render doesn't run `migrate`. When a future change adds a migration, apply it once from your machine with `python manage.py migrate`, after confirming the target database, before or right after pushing. Create more users locally as described in [User accounts](#user-accounts); they work on the deployed site immediately.

### After the first deploy

1. Open `https://your-app.onrender.com/healthz`. It should show `ok`.
2. Sign in, ask one of the suggested questions, and check that the answer cites a source.
3. Open `/admin/` as the superuser and find the exchange under **Chat exchanges**.

With `DEBUG` off, cookies are secure-only and plain HTTP redirects to HTTPS. The app trusts the `X-Forwarded-Proto` header from Render's proxy. `/healthz` is exempt from the redirect so that health checks over plain HTTP succeed.

**Free-tier behavior.** The service sleeps after 15 minutes without traffic, and the next visit takes about a minute while it wakes up. Each deploy builds a fresh copy, which is fine because nothing is stored on the service's disk. Gemini's free tier allows 20 `gemini-3.6-flash` requests a day per project; after that, `gemini-3.5-flash-lite` answers. When both are used up, users see the "busy" message until the quota resets. With no billing account on the Gemini project, nothing is charged.
