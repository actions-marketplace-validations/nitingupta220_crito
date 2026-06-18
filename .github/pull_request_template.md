<!--
Thanks for contributing to crito! Keep changes lean and in-lane:
zero-infra, BYOK, pure read -> comment. Fill in the summary and tick the
checklist below.
-->

## Summary

<!-- What does this PR change, and why? Link any related issue (e.g. "Closes #123"). -->


## Type of change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that changes existing behavior/config)
- [ ] Docs only
- [ ] Chore / refactor / CI (no behavior change)

## Checklist

- [ ] Smoke test passes: `python tests/test_smoke.py` (11 checks, no network, no deps)
- [ ] `python -m py_compile crito/*.py` is clean
- [ ] Transform modules kept stdlib-only — only `openrouter.py` and `github_client.py` import `httpx` directly (`review.py` pulls it in transitively), so the smoke test stays dependency-free
- [ ] Any new `.crito.yaml` config key is **wired** in `config.load_config` **and** documented in the README (don't document keys `load_config` doesn't read)
- [ ] Security invariants upheld — still pure read -> comment (zero write/merge/exec); `pull_request` not `pull_request_target`; untrusted diff stays fenced; model output sanitized; secrets redacted before the model; BYOK key never logged/in-prompt; `/review` stays author-gated
- [ ] Docs updated where relevant (README and/or `docs/*.md`)

## Notes for reviewers

<!-- Anything reviewers should know: tradeoffs, follow-ups, things you're unsure about. -->
