# AI Knowledge Base & Support Chatbot

**Turn documentation into a support chat with source-cited answers.** Users ask in their own words,
get a link to the relevant article, and can prepare an editable support request when the docs fall short.
Use it as a foundation for customer self-service, internal knowledge search, and onboarding.

![Chat with source](artifacts/answer-desktop.png)

[Russian-language video](https://pzzz404.github.io/hermes-knowledge-assistant/) · [Russian case study](docs/PORTFOLIO_RU.md) · [GitHub repository](https://github.com/pzzz404/hermes-knowledge-assistant)

## Where to use it

- **Customer support and help centers:** answer recurring questions from product documentation and show the supporting article.
- **Internal help desks:** find leave, schedule, bonus and IT support rules without manual document search.
- **Employee and customer onboarding:** explain first-week procedures and answer recurring questions.
- **Support agent assistance:** locate the original instruction and prepare an editable request draft when the docs are insufficient.

The application searches local Markdown articles. The repository includes 12 original Russian-language employee handbook articles for a fictional company; replace the corpus and organization-specific rules for your own use case.

The generation path is FastAPI → SQLite FTS5 → isolated Hermes → answer and evidence-ID validation. There are no embeddings or vector database. Articles are served as read-only `/kb/<slug>` pages.

The interface, sample corpus and recorded walkthrough are in Russian. See the [24-case Russian evaluation](docs/EVALUATION.md) for current results.

Run `uv sync --frozen --extra dev`, `uv run python scripts/import_corpus.py`, and `uv run pytest -q`. For a clearly marked technical preview use `ADAPTER=mock uv run uvicorn app.main:app --host 127.0.0.1 --port 8087`. Live answers require a server-side OpenRouter key and a separate Hermes profile; see the [Russian README](README.md) for setup.

This is a working web application that can be deployed and adapted to a product's knowledge base. The included Russian articles and product rules describe a fictional company; operational use requires replacing them, configuring a provider key, and evaluating answers on real support questions. The assistant does not integrate with customer workspaces or ticketing systems. Answers may be wrong, so the chat links to the source article. Personal data should not be entered into the public example. Alexey Yudin designed and reviewed this project with coding-agent assistance; no customer deployment or measured business impact is claimed.
