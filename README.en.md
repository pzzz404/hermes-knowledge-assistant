# Knowledge Support Assistant

A browser chat that searches a fictional product knowledge base, answers with source links, and prepares an editable support request draft.

![Chat with source](artifacts/answer-desktop.png)

[Demo video](artifacts/demo.webm) · [Russian case study](docs/PORTFOLIO_RU.md) · [GitHub repository](https://github.com/pzzz404/hermes-knowledge-assistant)

The real generation path is FastAPI → SQLite FTS5 → isolated Hermes → answer and evidence-ID validation. There are no embeddings or vector database. The 12 Markdown articles describe the fictional Example Workspace product and are served as read-only `/kb/<slug>` pages.

Run `uv sync --frozen --extra dev`, `uv run python scripts/import_corpus.py`, and `uv run pytest -q`. For a clearly marked technical preview use `ADAPTER=mock uv run uvicorn app.main:app --host 127.0.0.1 --port 8087`. Live answers require a server-side OpenRouter key and a separate Hermes profile; see the [Russian README](README.md) for setup.

The assistant cannot access real workspaces, personal data or tickets. It may make mistakes, so follow the linked source before acting. This is a portfolio demonstration by Alexey Yudin, developed with coding-agent assistance; no customer deployment or measured business impact is claimed.
