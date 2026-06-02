# Custom Rules & Configuration

Two-file, config-as-code (the proven CodeRabbit / PR-Agent pattern). Rules are **natural-language and prompt-injected — no rule DSL / regex / AST engine in v1** (even PR-Agent left structured rules as an open feature request; the whole industry uses NL rules).

## File 1 — `.pr-review.yaml` (settings, at repo root)

```yaml
model: qwen/qwen3-coder:free
fallback_models:
  - deepseek/deepseek-v4-flash:free
  - openai/gpt-oss-120b:free

review:
  profile: chill            # chill | assertive | strict   (default chill — nit-overload kills adoption)
  tone: ""                  # short string, <= 250 chars
  focus: [correctness, security, style, design]   # category toggles
  auto_review:
    enabled: true
    drafts: false
    base_branches: []       # empty = all
    ignore_authors: []
    skip_label: skip-review
  request_changes_on: []    # reserved; v1 is always event=COMMENT (advisory)

privacy:
  mode: zdr                 # zdr (default) | community

ignore:
  paths:                    # EXTENDS the built-in defaults, does not replace them
    - "**/*.generated.*"

rules:                      # inline rules OR point at the markdown file below
  - path: "src/app/api/**/*.ts"
    severity: warning       # info | warning | error  (advisory hint to the model)
    instructions: "Every endpoint must check authentication before any DB access."
rules_file: .pr-review/rules.md

tools: {}                   # reserved for the hybrid-linter stage (eslint/ruff/semgrep/gitleaks), default off

remote_config:              # org-centralized config (relevant for multi-repo)
  repo: ""
  ref: ""
  path: ""

memory:                     # RESERVED so v2 learnings is non-breaking
  enabled: false
  scope: repo
  opt_out: false
```

### Built-in default ignore globs
`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `Cargo.lock`, `poetry.lock`, `go.sum`, `Gemfile.lock`, `*.min.js`, `*.min.css`, `dist/**`, `build/**`, `vendor/**`, `node_modules/**`, `*.snap`, `*.svg`, `*.pb.go`, `*.generated.*`, plus `linguist-generated` files via `.gitattributes`. Users **extend**, not replace.

## File 2 — `.pr-review/rules.md` (natural-language rules)

```markdown
# Review rules

- Flag any API endpoint that does not check authentication.
- New public functions must have a docstring/JSDoc.
- Prefer returning early over deeply nested conditionals.
- Never log secrets, tokens, or full request bodies.
```

Easier for humans to write than YAML strings, and maps cleanly to a prompt section.

## How rules reach the model

1. At review time, for each changed file, select rules whose `path` glob matches (**picomatch-style globs, not regex** — that's what users expect).
2. Assemble a `## Custom rules (must apply)` section listing each matched rule + its severity hint, plus the path-relevant slice of `rules.md` (token-budgeted, ~600–800 line cap).
3. Place that section in the **static prompt prefix** for cache reuse.
4. Instruct the model to tag each finding with the originating `rule_id` and map the rule's severity onto the finding, and to **stay silent when no rule/issue applies**.
5. Severity is **advisory** — no hard CI gate by default. `error`-severity rules can later opt into a non-blocking Check Run.

## Precedence (document explicitly; log which won)

```
PR comment command  >  repo .pr-review.yaml  >  org remote_config  >  built-in defaults
```

Every key is optional with strong defaults, so a zero-config repo still gets a good review and the file can be added incrementally.

## v1 "learnings" for free

Treat `.pr-review/rules.md` as the user-editable memory: when the agent posts a finding the user disagrees with, they append a one-line rule. Real embeddings-backed learnings (with dismissal capture + retrieval) is v2 and requires a webhook/event stream a stateless Action doesn't have.
