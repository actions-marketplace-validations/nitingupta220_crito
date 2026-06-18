---
name: Bug report
about: Report a defect in review-agent (wrong/missing review, crash, bad comment, auth/quota failure)
title: "[bug]: "
labels: bug
---

<!--
Thanks for filing a bug. review-agent is a pure read -> comment GitHub Action that
reviews a PR diff with free OpenRouter models and posts one batched review.
The more of the boxes below you fill in, the faster this gets fixed.

SECURITY NOTE: if you think you've found a vulnerability (e.g. a prompt-injection
escape, secret exposure, or an authz bypass), DO NOT file a public issue. See the
security note in docs/security-and-ops.md and report privately instead.
-->

### What happened

<!-- The actual behavior. If a comment was posted, paste it. If none was posted, say so. -->


### What you expected

<!-- The behavior you expected instead. -->


### Which trigger fired

<!-- Pick one -->

- [ ] Auto-review on `pull_request` (opened / synchronize / reopened / ready_for_review) — same-repo PR
- [ ] Maintainer `/review` comment on a PR (`issue_comment`) — typically a fork PR
- [ ] Other / not sure

### Model chain used

<!--
List the model IDs that actually ran. The :free roster churns, so the exact IDs
matter. Find them on the sticky summary comment ("Models used: ...") or in the
Actions run log. If you overrode the default chain, paste your `models` /
OPENROUTER_MODELS value.
-->

```
e.g. openai/gpt-oss-120b:free, nvidia/nemotron-3-super-120b-a12b:free, google/gemma-4-31b-it:free
```

### Relevant Actions run log snippet

<!--
Paste the relevant lines from the failing workflow run.

GitHub automatically masks registered secrets in logs (your OpenRouter key, the
GITHUB_TOKEN), but PLEASE double-check the snippet before pasting — masking is
best-effort and you are responsible for what you post in a public issue.
-->

```
paste log lines here
```

### Your `.pr-review.yaml` (if any)

<!--
Paste your config file if you have one. Honored keys: models, profile, ignore,
max_diff_chars, max_files, max_findings, privacy_mode. Omit this block for a
zero-config repo.
-->

```yaml
# paste .pr-review.yaml here, or delete this block if you have none
```

### Link to a PR that reproduces it

<!-- A public PR (or a minimal repro PR) where we can see the behavior. Optional but very helpful. -->


### Environment

- review-agent version / tag (e.g. `v1`, `v1.0.1`):
- Repo visibility: public / private
- Same-repo PR or fork PR:
