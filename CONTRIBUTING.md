# Contributing to review-agent

Thanks for helping out. `review-agent` is a lean, zero-infra, BYOK GitHub Action
that reviews a PR diff with free OpenRouter models and posts **one** batched
review. It is intentionally small: two runtime dependencies, no database, no
server, no frontend. Keep changes in that spirit — practical, surgical, and
honest about what is and isn't wired.

By contributing you agree your work is licensed under the project's
**GPL-3.0** license (see [`LICENSE`](LICENSE)).

---

## Project layout

The agent lives in the `prreview/` package. Each module has one job:

| Module | Role |
| --- | --- |
| `review.py` | Orchestrator / entrypoint (`python -m prreview.review`): wires config → diff → secrets → prompt → ensemble → postprocess → GitHub. |
| `config.py` | Loads `.pr-review.yaml` + `.pr-review/rules.md`, applies env overrides, returns a `Config`. Precedence: dataclass defaults < yaml < env. |
| `diff.py` | Fetches/filters PR files, renders new-side hunks with reference line numbers, builds the set of valid anchors. |
| `secrets_scan.py` | Gitleaks-style regex redaction of secrets **before** the model; emits a critical security finding per hit. |
| `prompts.py` | Builds the system + user prompt, fences the untrusted diff (`<UNTRUSTED_DIFF>`), injects the review profile and trusted custom rules. |
| `schema.py` | Normalizes raw model findings into the canonical finding shape (severity/category/anchors). |
| `ensemble.py` | Sends the same prompt concurrently to ≤3 models, unions + dedups findings (file + category + overlapping range). |
| `openrouter.py` | Async OpenRouter client (`chat_json`) — the LLM transport. |
| `github_client.py` | Async GitHub REST client: reads the diff, posts the batched review + sticky summary comment. |
| `postprocess.py` | Anchor-gates findings (drops hallucinated lines), caps to `max_findings`, finalizes for posting. |
| `sanitize.py` | Sanitizes model output before posting (defang `@`-mentions, strip HTML). |
| `authz.py` | `/review` author gating: `is_authorized(author_association, permission)`. |

### Dependency discipline (load-bearing)

Only **two** modules import `httpx` directly:

- `openrouter.py`
- `github_client.py`

The orchestrator `review.py` pulls `httpx` in *transitively* (it imports those
two clients), so it is not stdlib-only either — but it contains no direct
`import httpx`.

**Everything else is stdlib-only.** This is not an accident — it is what lets
the smoke test (`tests/test_smoke.py`) run the entire transform pipeline
(diff → secrets → prompt → ensemble → postprocess → authz) with nothing but a
CPython install and no network. The smoke test deliberately imports only the
transform modules (never `review.py`), and asserts that `httpx`,
`prreview.openrouter`, and `prreview.github_client` were **not** imported.
`pyyaml` is imported *lazily inside* `config.load_config`, so even importing
`config.py` stays stdlib-only.

If you add an `import httpx` (or any third-party import) to a transform module,
you have broken the smoke test's dependency-free guarantee. Don't. Keep network
and third-party I/O behind `review.py` / `openrouter.py` / `github_client.py`.

---

## Local setup

```bash
git clone https://github.com/nitingupta220/review-agent.git
cd review-agent

# optional: isolate with a venv
python3.11 -m venv .venv && source .venv/bin/activate
```

You do **not** need to install anything to run the tests or compile the
package — they are stdlib-only. `pip install -r requirements.txt` (which pulls
`httpx` and `pyyaml`) is only needed for a **live run** that actually talks to
OpenRouter and GitHub:

```bash
pip install -r requirements.txt   # only for a live run, NOT for tests
```

Target runtime is **Python 3.11** (matches `action.yml`'s `setup-python@v6`).

---

## Running checks

Two checks, both fast and offline:

```bash
# 1. Smoke test: 11 stdlib-only checks, no network, no deps.
python tests/test_smoke.py

# 2. Byte-compile every module (catches syntax errors).
python -m py_compile prreview/*.py
```

`tests/test_smoke.py` is plain `assert`s inside a `main()` — **no pytest**. It
prints `PASS`/`FAIL` per check and exits non-zero on any failure. It also
asserts that `prreview.openrouter`, `prreview.github_client`, and `httpx` were
**not** imported, so a stray third-party import in a transform module fails the
run immediately.

Both checks must be green before you open a PR.

---

## How to add a config key

Adding a `.pr-review.yaml` key is a five-step change. A key that is *read but
not wired into behavior is a bug* — `privacy_mode` is the one accepted-but-not-
yet-wired key and it is documented honestly as roadmap; do not add more.

1. **Read it** in `config.load_config` (handle list/comma-string/scalar as
   appropriate, with a sane default).
2. **Add the field** to the `Config` dataclass with a default that matches the
   zero-config baseline. Use `default_factory` for mutable defaults.
3. **Wire it into behavior.** Thread the value through to the module that acts
   on it (e.g. `prompts.build_user_prompt`, `diff.filter_files`, a cap in
   `postprocess.finalize`). If the key doesn't change what the agent does, it
   doesn't belong yet.
4. **Add a smoke check** in `tests/test_smoke.py` that proves the wiring —
   exercise the *behavior*, not just that the value loaded (see
   `check_ignore_globs` and `check_profile_directive` as the pattern).
5. **Document it** in the README config table with its type, default, and what
   it does — and say plainly if any part is roadmap-only.

If you can't write a smoke check that observes the key changing behavior, the
key isn't ready.

---

## Coding conventions

- **Match the surrounding style.** Read the neighboring module first; mirror its
  docstrings, naming, and structure. No new formatters or lint configs.
- **Keep transform modules stdlib-only** (see Dependency discipline above).
- **Prefer small, pure functions** with explicit inputs — that is what keeps the
  pipeline testable without a network.
- **Security invariants — do not regress these:**
  - **Pure read → comment.** The agent only reads a diff and posts comments.
    Zero write/merge/exec on the target repo. Never add a write/merge call.
  - **Fence untrusted input.** The diff and any author-supplied text are
    UNTRUSTED and must stay inside the `<UNTRUSTED_DIFF>` fence. Trusted
    repo-authored custom rules go *outside* the fence; nothing else does.
  - **Sanitize model output** (`sanitize.py`) before posting — defang
    `@`-mentions, strip HTML.
  - **Redact secrets before the model** (`secrets_scan.py`) — secrets never
    reach the LLM or the prompt logs.
  - **Never custody the BYOK key.** The OpenRouter key lives in the user's repo
    secret. Never log it, never put it in a prompt, never persist it.
  - Use `pull_request` triggers, never `pull_request_target` (pwn-request). Fork
    code is never checked out or executed; the diff is read via the API only.

---

## Commit & PR guidelines

- **Atomic commits.** One logical change per commit, with a clear message.
- **Run both checks** (`python tests/test_smoke.py` and
  `python -m py_compile prreview/*.py`) before pushing.
- **Open a PR against `main`.** The review-agent reviews its own PRs — expect
  inline comments from the action on your diff and address (or reply to) them
  like any reviewer.
- Keep PRs focused; if you touched config, the README, or behavior, update all
  three together so docs never drift from code.
- **GPL-3.0 sign-off.** By submitting a PR you license the contribution under
  GPL-3.0. A `Signed-off-by` line (`git commit -s`) attesting to the
  [DCO](https://developercertificate.org/) is appreciated.

Thanks for contributing.
