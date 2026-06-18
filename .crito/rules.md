# Custom review rules

Natural-language rules for the crito. These are TRUSTED, repo-authored
instructions: the prompt builder injects them OUTSIDE the untrusted-diff fence,
so write them as direct guidance to the reviewer. Keep them short and specific —
they steer findings, they do not replace the model's general review.

This file dogfoods the agent on its own codebase and serves as a copy-paste
template. Edit freely for your project.

## Rules

1. **Keep `crito/` modules stdlib-only.** New or edited modules under
   `crito/` must not import third-party packages. The ONLY exceptions are the
   HTTP clients `crito/openrouter.py` and `crito/github_client.py`, which
   may import `httpx`. Flag any other stdlib-only module that starts importing
   `httpx`, `yaml`, or any non-stdlib package — it breaks the dependency-free
   smoke test.

2. **Flag new third-party dependencies.** The runtime has exactly two deps:
   `httpx` and `pyyaml`. Flag any new package added to `requirements.txt` (or
   any new top-level import of a non-stdlib library) and ask whether it is
   justified.

3. **Never post raw model output.** Anything written back to GitHub must pass
   through `crito/sanitize.py` (defang @-mentions, strip HTML). Flag any code
   path that posts model-derived text — comment bodies, suggestions, the summary
   — without sanitizing it first.

4. **Read-only posture only.** The agent must stay pure read -> comment. Flag any
   change that approves/requests-changes on a PR, writes to the repo, merges, or
   executes checked-out PR code. Reviews must use `event=COMMENT`.

5. **Secrets must be redacted before the model.** Any new field or diff content
   that could carry user secrets must go through `crito/secrets_scan.py`
   redaction before being placed in a prompt. Flag prompt-building code that
   bypasses redaction, and never log or echo the BYOK OpenRouter key.
