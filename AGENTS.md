# AGENTS.md

Rules for anyone (human or AI agent) changing this repository.

## Stack and ownership

- Backend: Python and Django (`config/` is the project package).
- Frontend: Django templates, Bootstrap 5, custom CSS (`static/css/sightbridge.css`), and minimal vanilla JavaScript (`static/js/sightbridge.js`). No React or separate frontend app.
- Authentication: Django's built-in auth (`auth.User`), sessions, permissions, and password hashing. No public registration and no Supabase Auth.
- Database: PostgreSQL hosted by Supabase. Django connects directly with database credentials; Supabase client SDKs and API keys are not part of the backend data path.
- The `chatbot` app owns the `Document` and `DocumentPassage` models (`chatbot/models.py`).
- Change application schema only through Django models and migrations. Do not create or alter app tables with hand-written SQL scripts or the Supabase dashboard. Migrations may use `RunSQL` for PostgreSQL-specific DDL (e.g. row level security).
- Never drop, truncate, or reset database objects you did not create for the task. Never apply migrations to production; confirm the target is the development database first.
- Every table in the `public` schema must have row level security enabled (Supabase exposes it through its Data API). The test suite enforces this.

## Frontend

- Always read and follow the installed `frontend-design` skill (`.claude/skills/frontend-design`) before implementing or modifying any frontend UI. If it cannot be read, stop and report it.
- Use the brand tokens in `static/css/sightbridge.css` (`--sb-ink` #080808, `--sb-graphite` #1E1E1E, `--sb-linen` #E9E2D6, `--sb-white` #FFFFFF); don't hardcode other brand colors.
- Extend `templates/base.html`, reuse `templates/components/`, and use named URLs and `{% static %}`.
- Keep pages responsive, keyboard accessible, and readable at AA contrast.

## Secrets

- Keep all credentials in environment variables (`.env` locally, which is git-ignored). Update `.env.example` with placeholders when adding a variable.
- Never commit credentials, connection strings, or keys, including in docs, tests, or fixtures.
- Never expose database credentials or privileged Supabase keys (`service_role`, secret keys) to frontend code. Never use a Supabase API key in place of database credentials.
- Never hardcode user credentials in source, templates, migrations, or docs.

## Chatbot behaviour

- Answer exclusively from approved (`status=approved`) document passages.
- Every citation must reference a passage actually retrieved for that answer.
- If the retrieved passages do not support an answer, say so; never fabricate.
- Never give personalized tax, legal, or investment recommendations.
- Never show fabricated or simulated answers in the UI.
- The LLM (Gemini API) is called only from `chatbot/llm.py`, server-side, and receives only retrieved passages; it never gets database access. Keep the API key server-side.
- Enforce guardrails in code, not only in prompts: citations are validated against the passages sent (`chatbot/safety.py`), the disclaimer is appended by code, and every exchange is logged (`chatbot/audit.py`), including errors.
- Tests never call the real LLM; use a fake client.
- Keep ingestion, retrieval, answer generation, safety controls, and audit logging in separate modules with single responsibilities.

## Working practices

- Implement only the explicitly requested scope; keep code modular and avoid unrelated refactoring.
- Test meaningful behavior with each change (`python manage.py test`). Database tests need PostgreSQL; the project has no SQLite fallback.
- Report actual verification results. Do not claim a check passed unless it ran and passed.
