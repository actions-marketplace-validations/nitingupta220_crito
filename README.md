# 🤖 AI PR Review Assistant

A production-ready, multi-agent AI system that automatically reviews GitHub Pull Requests — an open-source alternative to GitHub Copilot PR Reviews.

## ✅ What's Built

| Component | Status | Description |
|-----------|--------|-------------|
| FastAPI server | ✅ | Async, auto-reload, OpenAPI docs at `/docs` |
| GitHub webhook | ✅ | HMAC-SHA256 signature verification |
| GitHub service | ✅ | Fetch PR diff, files, metadata; post comments |
| OpenRouter service | ✅ | LLM gateway with retry + JSON parsing |
| Security Agent | ✅ | OWASP Top 10, secrets, injection flaws |
| Bug Detection Agent | ✅ | Logic errors, null deref, race conditions |
| Performance Agent | ✅ | N+1 queries, algorithm complexity, memory |
| Code Quality Agent | ✅ | SOLID, DRY, naming, complexity |
| Documentation Agent | ✅ | Docstrings, type hints, README gaps |
| Aggregator Agent | ✅ | Synthesizes all reports → single GitHub comment |
| Static Analysis | ✅ | Bandit (security) + Pylint (quality) on diff |
| Pipeline Orchestrator | ✅ | Parallel agents + static analysis + DB persist |
| Reviews REST API | ✅ | `GET /reviews/`, `GET /reviews/{id}`, `POST /reviews/trigger` |
| DB Schema | ✅ | PullRequest, Review, AgentOutput, Finding |

## 🚀 Quick Start (Local)

```bash
# 1. Activate venv
.\venv\Scripts\activate

# 2. Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Server runs at: http://localhost:8000  
API docs at: http://localhost:8000/docs

## 🔗 Connecting GitHub Webhook

### Option A — Use ngrok (recommended for local dev)
```bash
# Install ngrok from https://ngrok.com/download
ngrok http 8000
```
Copy the HTTPS URL (e.g. `https://abc123.ngrok.io`)

### Option B — Deploy to a server
Deploy the app to any server with a public IP.

### Set up the webhook on GitHub
1. Go to your repo → **Settings** → **Webhooks** → **Add webhook**
2. **Payload URL:** `https://YOUR_URL/webhook/github`
3. **Content type:** `application/json`
4. **Secret:** `360ai` (from your `.env`)
5. **Events:** Select **"Pull requests"** only
6. Click **Add webhook**

Now open or update any PR — the review will be posted automatically as a comment!

## 🧪 Manual Trigger (no webhook needed)

```bash
curl -X POST http://localhost:8000/reviews/trigger \
  -H "Content-Type: application/json" \
  -d '{"repo": "owner/repo-name", "pr_number": 1}'
```

## 📁 Project Structure

```
PR review/
├── app/
│   ├── agents/
│   │   ├── base_agent.py          # Abstract base for all agents
│   │   ├── security_agent.py      # OWASP + secrets detection
│   │   ├── bug_agent.py           # Logic errors + runtime bugs
│   │   ├── performance_agent.py   # N+1, memory, complexity
│   │   ├── quality_agent.py       # SOLID, DRY, naming
│   │   ├── docs_agent.py          # Docstrings, type hints
│   │   └── aggregator_agent.py    # Final synthesis agent
│   ├── services/
│   │   ├── github_service.py      # GitHub API client
│   │   ├── openrouter_service.py  # LLM gateway
│   │   ├── static_analysis_service.py  # Bandit + Pylint
│   │   └── pipeline_service.py    # Main orchestrator
│   ├── routers/
│   │   ├── webhook.py             # POST /webhook/github
│   │   └── reviews.py             # GET/POST /reviews/
│   ├── models/
│   │   └── db_models.py           # SQLAlchemy models
│   ├── config.py                  # Pydantic settings
│   ├── database.py                # Async SQLAlchemy
│   └── main.py                    # FastAPI app
├── .env                           # Your secrets (never commit!)
├── .env.example                   # Safe template
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## 🤖 AI Models Used

| Agent | Model |
|-------|-------|
| Security | `deepseek/deepseek-r1` |
| Bug Detection | `deepseek/deepseek-chat-v3-0324` |
| Performance | `qwen/qwen3-coder` |
| Code Quality | `deepseek/deepseek-chat-v3-0324` |
| Documentation | `meta-llama/llama-4-maverick` |
| Aggregator | `deepseek/deepseek-r1` |

All configurable in `.env`.

## 🗄️ Database (Optional)

The system runs fully without a database. When PostgreSQL is available, it persists all reviews, agent outputs, and findings for querying via the `/reviews/` API.

To set up PostgreSQL:
```bash
# Update .env with your PostgreSQL credentials
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/prreviewdb

# Or use Docker Compose
docker compose up db -d
```
