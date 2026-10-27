# Friendly Tax Advisor

An advisor-facing Q&A assistant for educational questions about Section 1031 exchanges, Delaware Statutory Trusts (DSTs), and Section 721 contributions to a REIT operating partnership (often described as a “721 exchange”).

> **Status:** Sign-in, the grounded chat pipeline (retrieval, Gemini answers with validated citations, advice handling, disclaimer), and the admin-only exchange log are implemented. The linked sources below have **not been loaded yet**: until approved documents are in the database, every question gets the "not in the approved sources" response.

## Part 1: Advisor Q&A assistant

The application is a simple, authenticated chat for financial advisors. It answers only from its approved source set, cites the passages and source links supporting each answer, and says when the answer is not present in those materials. It does not fill gaps with uncited model knowledge.

### Answer guardrails

- Provide general educational information only. Do not give personalized tax, legal, or investment advice, determine whether a specific transaction qualifies, or recommend an investment.
- When a user asks what they personally should do, decline to make that decision. Offer relevant general information from the approved sources when possible and suggest consulting a qualified tax, legal, or investment professional.
- Include a short disclaimer with every answer: “For general educational purposes only; this is not tax, legal, or investment advice.”
- If the approved sources do not support an answer, say so clearly and do not guess.
- Record each question, answer, cited sources, and timestamp in an admin-only conversation log.

## Public reference set

These ten public references are the planned starting corpus for Part 1. The IRS and statutory materials cover like-kind exchanges, DST treatment, partnership contributions, and basis. SEC materials provide REIT background and one issuer-specific example of an operating-partnership structure.

1. [IRS Publication 544, Sales and Other Dispositions of Assets](https://www.irs.gov/publications/p544) — overview of like-kind exchanges, recognized gain, and basis.
2. [IRS Instructions for Form 8824, Like-Kind Exchanges](https://www.irs.gov/instructions/i8824) — reporting and deferred-exchange requirements.
3. [26 U.S.C. § 1031](https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section1031&num=0&edition=prelim) — statute governing qualifying exchanges of real property.
4. [26 C.F.R. § 1.1031(k)-1](https://www.ecfr.gov/current/title-26/section-1.1031%28k%29-1) — rules for deferred exchanges.
5. [IRS Revenue Ruling 2004-86](https://www.irs.gov/irb/2004-33_IRB#RR-2004-86) — federal tax classification and Section 1031 treatment of the DST arrangement described in the ruling.
6. [26 U.S.C. § 721](https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section721&num=0&edition=prelim) — general nonrecognition rule for property contributed to a partnership for a partnership interest, subject to statutory exceptions.
7. [26 C.F.R. § 1.721-1](https://www.ecfr.gov/current/title-26/section-1.721-1) — regulations on nonrecognition for partnership contributions.
8. [SEC Investor Bulletin: Real Estate Investment Trusts (REITs)](https://www.sec.gov/files/reits.pdf) — REIT structure and investor basics.
9. [ExchangeRight Income Fund 2025 Form 10-K](https://www.sec.gov/Archives/edgar/data/1771514/000119312526075069/none-20251231.htm) — issuer-specific example describing an UPREIT, its operating partnership, and OP-unit holders.
10. [IRS Publication 551, Basis of Assets](https://www.irs.gov/publications/p551) — basis concepts relevant to property exchanges and contributions.

Revenue Ruling 2004-86 applies to the facts it describes, and the SEC filing illustrates only that issuer’s structure. Neither establishes the terms or tax result for another DST, REIT, or investor. Before supporting product-specific answers, add current public transaction documents and ground each claim in the document that supports it.

## How the chat works

The language model never reads the database. Django retrieves the passages and sends the model only those, so "answer only from the documents" is enforced by code rather than by the prompt alone.

```
POST /chat/ask/
 1. Triage       Gemini (flash-lite) classifies the question as educational,
                 personalized advice, or out of scope, and rewrites follow-ups
                 into a standalone query using the last 3 turns.
                 If Gemini is unavailable, a keyword rule decides.
 2. Route        Out of scope: fixed scope message, no further calls.
                 Personalized advice: decline, then continue with the
                 depersonalized general question.
 3. Retrieve     PostgreSQL full-text search (GIN index) over passages of
                 approved documents only; top 8 above a relevance threshold.
                 No matches: "not in the approved sources", no answer call.
 4. Answer       Gemini (flash) gets only the labelled passages P1..P8 and
                 returns JSON: status, answer with [P#] markers, cited labels.
 5. Validate     Markers that don't match a passage that was sent are removed.
                 An answer with no valid citation becomes "not found".
 6. Disclaimer   Added by code to every response, whatever the outcome.
 7. Audit log    Question, answer, outcome, retrieved passages, and a snapshot
                 of each cited source are saved with a timestamp.
```

Each step lives in its own module in `chatbot/`: `llm.py` (Gemini client and prompts), `retrieval.py`, `safety.py` (fixed texts, keyword rule, citation validation), `answering.py` (the pipeline above), and `audit.py` (logging).

**Personalized advice.** The triage model flags questions that ask what a specific person or client should do, whether a specific transaction qualifies, what someone's own tax result would be, or for a product or investment recommendation. The answer always starts with a fixed decline and a referral to a qualified professional. It then answers the general version of the question from the sources, with citations, when the sources cover it. If Gemini can't classify a question, a keyword rule ("should I", "my client", "do you recommend", ...) catches the common phrasings.

**Free-tier limits.** Each question uses at most two Gemini calls, and fewer when it is out of scope or retrieval finds nothing. Each user can ask 10 questions a minute (`CHAT_RATE_LIMIT_PER_MINUTE`). When Gemini is rate-limited or unreachable, the user is asked to try again, and the exchange is still logged with outcome `error`.

**Privacy.** Gemini's free tier may use prompts to improve Google's products, so the UI asks users not to include client names or personal details.

### Admin conversation log

Every exchange is visible to staff at `/admin/` under **Chat exchanges**. Each entry shows the timestamp, user, outcome, category, question, standalone query, answer, disclaimer, retrieved passage IDs, model, latency, and the cited sources. Sources are snapshotted, so the log stays accurate if documents are re-ingested. The log is read-only in the admin: entries can't be added, edited, or deleted there.

### What's left out, and what I'd build next

- **Documents aren't loaded yet.** The reference set above still needs to be downloaded, split into passages, and approved (see [Loading documents](#loading-documents)).
- **Lexical retrieval only.** Full-text search misses paraphrases ("swap" vs. "like-kind exchange"); the triage rewrite helps. Next: measure misses with an evaluation set and add pgvector embeddings for hybrid search if needed.
- **No automated answer evaluation.** Next: a fixed set of supported, unsupported, and advice questions run on every change.
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
| `DJANGO_ALLOWED_HOSTS` | In production | Comma-separated host names (default `localhost,127.0.0.1`) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | In production | Comma-separated origins, e.g. `https://your-app.up.railway.app` |
| `DJANGO_SECURE_SSL_REDIRECT` | No | Redirect HTTP to HTTPS. Defaults to on when `DEBUG` is off |
| `GEMINI_API_KEY` | For answers | Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey). Without it, every question gets the "busy" response |
| `GEMINI_ANSWER_MODEL` | No | Default `gemini-3.8-flash` |
| `GEMINI_TRIAGE_MODEL` | No | Default `gemini-3.5-flash-lite` |
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
- Full-text search uses a GIN index on `to_tsvector('english', content)`. Queries must use `chatbot.models.passage_search_vector()` for PostgreSQL to use the index.
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

Passages are searched only when their `Document` has `status=approved`. For good retrieval and citations:

- Split each source into passages of roughly 150 to 400 words, numbered with `chunk_index` from 0.
- Set `section_heading` and `page_number` when the source has them; they appear in citations.
- Set `source_url` to the public page a reader should open, `publisher` (IRS, SEC, ...), and `retrieved_at`.
- Retrieval uses PostgreSQL's `english` text-search configuration.

## Deploy to Railway

The app runs on Railway with gunicorn and serves static files with WhiteNoise. `railway.json` sets the start command, which runs `collectstatic` and then gunicorn. `.python-version` pins Python 3.13.

1. Create a Railway project from this GitHub repository.
2. In the service's **Variables**, set:
   - `DATABASE_URL`: the Supabase session pooler URI;
   - `DJANGO_SECRET_KEY`: a new random value, not the local one;
   - `GEMINI_API_KEY`;
   - `DJANGO_ALLOWED_HOSTS`: the Railway domain, e.g. `your-app.up.railway.app`;
   - `DJANGO_CSRF_TRUSTED_ORIGINS`: `https://your-app.up.railway.app`.

   Leave `DJANGO_DEBUG` unset.
3. Generate a public domain under **Settings → Networking**, then set the two host variables to match it.
4. Migrations are not run on deploy. Run them yourself against the database, after confirming the target, with `python manage.py migrate`.
5. Create the administrator with `python manage.py createsuperuser`, and the advisor accounts as described in [User accounts](#user-accounts).

With `DEBUG` off, session and CSRF cookies are secure-only, and the app trusts Railway's `X-Forwarded-Proto` header for HTTPS.
