# AGENTS.md

Rules for anyone (human or AI agent) changing this repository.

## Stack and ownership

- Backend: Python and Django (`config/` is the project package).
- Database: PostgreSQL hosted by Supabase. Django connects directly with database credentials; Supabase client SDKs and API keys are not part of the backend data path.
- The `chatbot` app owns the `Document` and `DocumentPassage` models (`chatbot/models.py`).
- Change application schema only through Django models and migrations. Do not create or alter app tables with hand-written SQL scripts or the Supabase dashboard. Migrations may use `RunSQL` for PostgreSQL-specific DDL (e.g. row level security).
- Never drop, truncate, or reset database objects you did not create for the task. Never apply migrations to production; confirm the target is the development database first.

## Secrets

- Keep all credentials in environment variables (`.env` locally, which is git-ignored). Update `.env.example` with placeholders when adding a variable.
- Never commit credentials, connection strings, or keys, including in docs, tests, or fixtures.
- Never expose database credentials or privileged Supabase keys (`service_role`, secret keys) to frontend code. Never use a Supabase API key in place of database credentials.

## Chatbot behaviour (for future work)

- Answer exclusively from approved (`status=approved`) document passages.
- Every citation must reference a passage actually retrieved for that answer.
- If the retrieved passages do not support an answer, say so; never fabricate.
- Never give personalized tax, legal, or investment recommendations.
- Keep ingestion, retrieval, answer generation, safety controls, and audit logging in separate modules with single responsibilities.

## Working practices

- Implement only the explicitly requested scope; avoid unrelated refactoring.
- Add or update tests with each change (`python manage.py test`). Database tests need PostgreSQL; the project has no SQLite fallback.
- Report actual verification results. Do not claim a check passed unless it ran and passed.
