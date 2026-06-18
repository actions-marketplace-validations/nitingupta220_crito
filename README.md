# review-agent

**A lean, zero-infra, bring-your-own-key GitHub Action that reviews your PR diff with free OpenRouter models and posts one batched review.**

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![GitHub Action](https://img.shields.io/badge/GitHub-Action-2088FF.svg?logo=githubactions&logoColor=white)](action.yml)

---

## What it is

`review-agent` is a GitHub Action that reads a pull request's diff through the GitHub API, sends it to free [OpenRouter](https://openrouter.ai/) models, and posts a single batched code review — inline comments anchored to changed lines plus one sticky summary comment. It is **pure read-and-comment**: it never checks out, builds, merges, or executes PR code, and it never writes to your repository. You bring your own OpenRouter key (BYOK); there is no server, no database, and nothing to host.

## Why

Hosted PR reviewers like CodeRabbit, Qodo, and Graphite bill **per seat per month**. `review-agent` runs entirely inside GitHub Actions on **free OpenRouter models** using **your own API key**, so the marginal cost of a review is essentially zero. You trade a managed dashboard for a 30-line workflow file and full control over which models see your diff.

## Features

- **Ensemble of up to 3 models** — the same prompt fans out concurrently to up to 3 different models; findings are unioned and deduped (by file + category + overlapping line range) for better recall.
- **One batched review** — a single `POST /pulls/{n}/reviews` (event `COMMENT` — never approve or request-changes), with per-comment fallback if the atomic post is rejected.
- **Inline comments + sticky summary** — findings are anchored to new-side lines in *Files changed*; one summary comment in *Conversation* carries severity counts, models used, and skip notes.
- **Incremental review** — the summary comment stores a hidden `last_reviewed_sha` marker so re-runs skip already-reviewed commits.
- **Secret redaction** — gitleaks-style regex scrubs secrets to `[REDACTED_SECRET]` **before** the diff reaches any model, and raises a `critical` finding.
- **Prompt-injection defense** — the untrusted diff is fenced (`<UNTRUSTED_DIFF>`), and model output is sanitized (defanged `@`-mentions, stripped HTML) before posting.
- **Natural-language custom rules** — drop repo-specific rules in `.pr-review/rules.md`; they are injected as trusted instructions outside the untrusted fence.
- **Fork-safe `/review` gating** — fork PRs are reviewed on demand via a maintainer-gated `/review` comment; the agent enforces author-association + collaborator authz.
- **Zero infra** — two runtime dependencies (`httpx`, `pyyaml`), Python 3.11, no service to run.

## Example

A live run on a file with planted bugs produced inline comments like these in *Files changed*:

````text
**[critical · security]** SQL query is built with string interpolation, allowing SQL injection.
Use a parameterized query instead.

```suggestion
cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```

**[critical · security]** User input is passed to os.system, enabling command injection.

**[major · bug]** The sqlite connection is never closed — resource leak. Use a context manager.

**[major · correctness]** Mutable default argument `items=[]` is shared across calls.
````

…and one sticky summary comment in *Conversation*:

```text
Reviewed the diff and found 5 issue(s): 2 critical, 3 major.

Models: openai/gpt-oss-120b:free, google/gemma-4-31b-it:free, nvidia/nemotron-3-super-120b-a12b:free
```

## Quick start

Zero-config: with no `.pr-review.yaml` the action ships strong, live-verified defaults and reviews every non-draft PR automatically.

### 1. Add the workflow

Create `.github/workflows/pr-review.yml` in your repo:

```yaml
name: PR Review

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: pr-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  review:
    name: Review diff
    runs-on: ubuntu-latest
    if: ${{ github.event.pull_request.draft == false }}
    steps:
      - name: Checkout action
        uses: actions/checkout@v6

      - name: Run review agent
        uses: nitingupta220/review-agent@v1
        with:
          openrouter_api_key: ${{ secrets.OPENROUTER_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

### 2. Add your OpenRouter key as a repo secret

Get a key at [openrouter.ai/keys](https://openrouter.ai/keys), then in your repository go to **Settings → Secrets and variables → Actions → New repository secret** and add:

- **Name:** `OPENROUTER_API_KEY`
- **Value:** your OpenRouter key

`github_token` is the automatic `secrets.GITHUB_TOKEN` — you do not create it.

### 3. Open a PR

Open or push to a pull request on the same repo. The action runs automatically (drafts are skipped) and posts its review when it finishes.

## Fork PRs / the `/review` command

For security, the auto-review workflow uses the `pull_request` trigger, which gives fork PRs a **read-only** token and **no** `OPENROUTER_API_KEY`. So fork PRs are never auto-reviewed. Instead, a maintainer triggers a review on demand by commenting **`/review`** on the PR. The `issue_comment` event runs from the default branch with the base repo's secrets, and the action enforces commenter authorization (`author_association` + collaborator permission) so a random fork author cannot drain your quota. Fork code is still **never** checked out or executed — only the diff is read via the API.

Add a second workflow, `.github/workflows/pr-review-command.yml`:

```yaml
name: PR Review Command

on:
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: pr-review-cmd-${{ github.event.issue.number }}
  cancel-in-progress: true

jobs:
  review:
    name: Review on /review command
    runs-on: ubuntu-latest
    if: >-
      ${{ github.event.issue.pull_request &&
          startsWith(github.event.comment.body, '/review') }}
    steps:
      - name: Checkout action
        uses: actions/checkout@v6

      - name: Run review agent
        uses: nitingupta220/review-agent@v1
        with:
          openrouter_api_key: ${{ secrets.OPENROUTER_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

## Configuration

All configuration is optional. Place a `.pr-review.yaml` at your repo root. Precedence is **dataclass defaults → `.pr-review.yaml` → environment override**. A copy-paste-ready, fully commented sample lives in this repo at [`.pr-review.yaml`](.pr-review.yaml).

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `models` | list or comma-string | live default chain (see [Models](#models)) | OpenRouter model ids to ensemble. Capped to 3. Overridden only by the `OPENROUTER_MODELS` env var / `openrouter_models` input. |
| `profile` | string | `chill` | Review strictness directive injected into the prompt: `chill` (only material issues), `assertive` (more willing to push on design), `strict` (everything, incl. style nits). |
| `ignore` | list or comma-string of globs | `[]` | Globs that **extend** the built-in skip list (lockfiles, minified/bundled output, vendored/generated trees, binary assets). Matched files are dropped before prompting. |
| `max_diff_chars` | int | `60000` | Compression budget — the combined diff is packed under this many characters before prompting. |
| `max_files` | int | `60` | Hard cap on the number of changed files reviewed per run. |
| `max_findings` | int | `30` | Hard cap on findings posted (after union + dedupe). |
| `privacy_mode` | string | `zdr` | **Accepted but not yet wired — ZDR routing is roadmap, not enforced.** Setting this does not currently send zero-data-retention provider routing. See [Privacy](#privacy). |

**Custom rules.** Put natural-language, repo-specific rules in `.pr-review/rules.md`. They are injected into the prompt as **trusted** instructions, kept outside the untrusted-diff fence. Example contents:

```markdown
- All new HTTP handlers must validate the `Authorization` header.
- Flag any use of `print(` in library code; we use the logging module.
- Database migrations must be reversible.
```

**Environment override.** The `OPENROUTER_MODELS` env var (set by the action's `openrouter_models` input) is a comma-separated list that overrides the `models` key **only** — all other keys still come from `.pr-review.yaml` / defaults.

See [docs/custom-rules.md](docs/custom-rules.md) for more on writing effective rules.

## Models

The default chain (`config.DEFAULT_MODELS`, verified serving 2026-06-13) is:

```text
openai/gpt-oss-120b:free
nvidia/nemotron-3-super-120b-a12b:free
google/gemma-4-31b-it:free
```

These are chosen for liveness plus lineage diversity (OpenAI / NVIDIA / Google → different blind spots → better union recall). Override them three ways (highest precedence last): the `models:` key in `.pr-review.yaml`, the `openrouter_models:` action input, or the `OPENROUTER_MODELS` env var — each capped to 3.

> **Note on the `:free` roster.** OpenRouter's free model lineup churns — slugs get retired, renamed, or rate-limited upstream. Treat these IDs as **runtime config, not guarantees**. If a model starts 404-ing or saturating, override the chain with currently-serving slugs (or a paid / ZDR-capable provider). See [docs/model-strategy.md](docs/model-strategy.md).

## How it works

The pipeline is a staged, read-only flow (see [docs/architecture.md](docs/architecture.md)):

1. **Trigger** — a `pull_request` event (or a maintainer `/review` comment) starts the composite action; secrets are passed to the agent process only, never logged.
2. **Fetch diff** — the PR's changed files are read via the GitHub REST API; fork code is never checked out or run.
3. **Filter** — binary/minified/vendored/generated/deleted files and your `ignore:` globs are dropped; remaining files are sorted source-first.
4. **Render + budget** — the diff is rendered with reference line numbers and greedily packed under `max_diff_chars`, building the set of valid anchor lines.
5. **Redact secrets** — a gitleaks-style scan scrubs secrets to `[REDACTED_SECRET]` before the model sees anything, and seeds a `critical` finding.
6. **Prompt + ensemble** — one prompt (with your `profile` directive and `.pr-review/rules.md`) is sent concurrently to up to 3 models; the untrusted diff sits inside an `<UNTRUSTED_DIFF>` fence.
7. **Union + sanitize** — findings are normalized, anchors validated against real lines, deduped, sanitized (defanged mentions, stripped HTML), and capped at `max_findings`.
8. **Post** — one batched `COMMENT` review with inline comments plus a sticky summary carrying a hidden `last_reviewed_sha` marker for incremental skips; per-comment fallback on a `422`.

## Security

- **Pure read → comment** — zero write/merge/exec; the agent only reads the diff and posts comments.
- **`pull_request`, never `pull_request_target`** — avoids the classic "pwn-request" privilege-escalation hole; fork code is never checked out or executed.
- **Untrusted-diff fencing** — the diff is wrapped in `<UNTRUSTED_DIFF>` so model instructions can't be hijacked by content in the PR.
- **Output sanitization** — model output is defanged (`@`-mentions neutralized, HTML stripped) before it is posted.
- **Pre-model secret redaction** — gitleaks-style regex redacts secrets to `[REDACTED_SECRET]` before prompting, and flags a `critical` finding.
- **Key custody** — your OpenRouter key lives in your repo secret; it is never custodied by us, never logged, and never placed in the prompt.
- **`/review` authz** — the comment trigger is gated on `author_association` + collaborator permission so forks can't drain your quota.

More detail in [docs/security-and-ops.md](docs/security-and-ops.md).

## Cost & limits

`review-agent` runs on **your own** OpenRouter key against **free** models, so reviews are effectively free. The honest constraints:

- **Free-tier rate limits:** roughly **20 requests/min** and **~50 requests/day**, until a one-time **~$10** OpenRouter credit raises the daily cap to **~1000/day**.
- **Per-PR cost:** the ensemble uses **up to 3 LLM calls per PR** (one per model).
- **The real binding constraint** observed in practice is **upstream provider saturation** of free models (429s), which the model fallback array mitigates — if one free slug is saturated, the chain routes around it.

See [docs/spike-openrouter-quota.md](docs/spike-openrouter-quota.md) for the measured numbers.

## Privacy

Be deliberate about what you send to free models. Most OpenRouter **`:free`** models route to providers only if your account permits **training on prompt data** — so free-model review may expose your diff to providers for training. **v1 does not yet enforce zero-data-retention (ZDR) routing**; the `privacy_mode` key is accepted but not wired (see [Configuration](#configuration)).

**For proprietary code:** override the `models` key with a paid / ZDR-capable provider (or your own BYOK provider slug) instead of relying on the free chain or `privacy_mode`. ZDR routing is on the [roadmap](#roadmap).

## Local development

The agent is mostly stdlib; only `openrouter.py` and `github_client.py` import `httpx` directly (the orchestrator `review.py` pulls it in transitively by importing those two clients). The smoke test exercises only the stdlib-only transform modules — it never imports `review.py` — so it runs with **no dependencies and no network**.

```bash
git clone https://github.com/nitingupta220/review-agent.git
cd review-agent

# Stdlib-only smoke test — 11 checks, no deps, no network:
python tests/test_smoke.py

# Byte-compile everything:
python -m py_compile prreview/*.py
```

**Runtime dependencies (just two):** `httpx` and `pyyaml` (see `requirements.txt`).

**Module layout (`prreview/`):**

| Module | Responsibility |
| --- | --- |
| `review.py` | Orchestrator — the staged pipeline end to end |
| `diff.py` | File filtering + diff rendering with reference line numbers |
| `config.py` | Loads `.pr-review.yaml`, env override, custom rules |
| `secrets_scan.py` | Pre-model gitleaks-style secret redaction |
| `prompts.py` | Builds the system/user prompt, profile directive, fence |
| `schema.py` | Findings schema + enums + normalization |
| `ensemble.py` | Union + dedupe of multi-model findings |
| `openrouter.py` | OpenRouter chat-completions client (`httpx`) |
| `github_client.py` | GitHub REST client — read diff, post review (`httpx`) |
| `postprocess.py` | Anchor validation, capping, ordering |
| `sanitize.py` | Defang mentions / strip HTML from model output |
| `authz.py` | `/review` commenter authorization |

For design rationale and the constraints to respect before contributing, see [docs/decisions.md](docs/decisions.md).

## Roadmap

Deliberately deferred for after v1:

- **ZDR routing** — wire `privacy_mode` to send zero-data-retention provider routing.
- **Hybrid linters** — fold in `semgrep` / `ruff` signal alongside the LLM ensemble.
- **Learnings / memory** — persist accepted/dismissed feedback to tune future reviews.
- **Managed SaaS** — an optional hosted offering for teams that don't want BYOK.
- **More forges** — GitLab and Bitbucket support.
- **More commands** — `/describe` (PR summaries) and `/ask` (Q&A on the diff).

## Contributing

Contributions are welcome — open an issue or PR. Please read [docs/decisions.md](docs/decisions.md) for the design rationale and constraints to respect (stdlib-first, zero-infra, read-only) before sending changes.

## License

Licensed under the **GNU General Public License v3.0**. See [LICENSE](LICENSE).
