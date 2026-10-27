# Friendly Tax Advisor

An advisor-facing Q&A assistant for educational questions about Section 1031 exchanges, Delaware Statutory Trusts (DSTs), and Section 721 contributions to a REIT operating partnership (often described as a “721 exchange”).

> **Status:** This repository contains a Django project connected to Supabase PostgreSQL and the (empty) document schema for the assistant. There is no chatbot, ingestion, or retrieval code yet. The linked sources below are the planned public reference set; they have not been downloaded, indexed, or used by an assistant.

## Part 1: Advisor Q&A assistant

The planned application is a simple, authenticated chat for financial advisors. It should answer only from its approved source set, cite the passages and source links supporting each answer, and say when the answer is not present in those materials. It should not fill gaps with uncited model knowledge.

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

## Current state and next steps

The database schema for approved documents and their passages exists (see [Development setup](#development-setup)). The chat app, login, source ingestion and retrieval, passage-level citations, advice-request handling, answer disclaimer, admin log, and deployment are not implemented yet.

Next, build the small chat application, ingest and keep the approved sources current, return citations to the specific supporting passages, and implement the guardrails and admin-only log described above. Then evaluate answers for supported questions, questions outside the source set, and personalized advice requests before deploying it.

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
| `DJANGO_SECRET_KEY` | Yes outside local development | Django secret key |

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

### Schema

The `chatbot` app owns the two document models in `chatbot/models.py`. Django migrations are the source of truth for this schema.

- **`Document`** is an approved public source: `title`, `publisher`, unique `source_url`, `status` (`draft` / `approved` / `archived`, also enforced by a check constraint), `content_hash`, `retrieved_at`, and timestamps.
- **`DocumentPassage`** is an extracted passage: `document` (FK), `chunk_index`, `content`, `page_number`, `section_heading`, `created_at`. `(document, chunk_index)` is unique, and the unique index also serves FK lookups. A document that still has passages cannot be deleted (`PROTECT`). Retire sources by archiving them so that citations keep resolving.
- Full-text search uses a GIN index on `to_tsvector('english', content)`. Queries must use `chatbot.models.passage_search_vector()` for PostgreSQL to use the index.
- Migrations `0002` and `0003` enable row level security, with no policies, on both chatbot tables and on Django's built-in tables (`auth_*`, `django_*`). This blocks Supabase's Data API (`anon` / `authenticated` roles), while Django, which connects as the table owner, is unaffected. Any new table in `public` also needs RLS enabled; the test suite fails if one does not have it.

### Tests

```bash
python manage.py test chatbot
```

Database tests run in a temporary `test_postgres` database on the configured server. The real database is not touched, and the temporary one is dropped afterwards. This works with the Supabase session pooler: `config/test_runner.py` drops the test database with `WITH (FORCE)`, because the pooler keeps a connection to it open. A local PostgreSQL server also works:

```bash
DATABASE_URL=postgresql://postgres:YOUR_LOCAL_PASSWORD@localhost:5432/postgres DB_SSLMODE=disable python manage.py test chatbot
```

The configuration and model-definition tests do not need a database:

```bash
python manage.py test chatbot.tests.DatabaseConfigTests chatbot.tests.ModelDefinitionTests
```
