---
name: Feature request
about: Propose an enhancement for review-agent
title: "[feat]: "
labels: enhancement
---

<!--
Thanks for the idea! review-agent is intentionally lean: a zero-infra, BYOK
GitHub Action that does a pure read -> comment review and nothing else. Proposals
that keep it in that lane land fastest.

NOT a security report. If you've found a vulnerability, do NOT file a public
issue — see the security note in docs/security-and-ops.md.
-->

### The problem

<!-- What's painful or missing today? Describe the use case, not just the solution. -->


### Proposed solution

<!-- What you'd like to happen. Be concrete: new config key, new output, new trigger, etc. -->


### Alternatives you've considered

<!-- Other ways to solve the problem, including workarounds you're using now. -->


### Does it respect the design constraints?

<!--
review-agent's non-negotiables. Check the ones your proposal upholds, and note
any it would bend.
-->

- [ ] **Lean** — two runtime deps (httpx, pyyaml), stdlib-only transform modules; no heavy new dependency
- [ ] **Zero-infra** — runs inside the user's GitHub Action on their own OpenRouter key; no hosted service / database / backend
- [ ] **Pure read -> comment** — no write/merge/exec/tool capability; approval and merge stay human
- [ ] **BYOK / privacy-honest** — doesn't custody the user's key; doesn't silently expose more diff content to providers

<!-- If your proposal breaks one of these on purpose, explain why it's worth it here: -->
