# Contributing to cr8

cr8 is open source. That means the code is public and you can open pull requests.
It does **not** mean the public can push to `main`, merge unchecked, or touch
anyone’s production machine.

## Who can do what

| Actor | Can |
|-------|-----|
| Anyone | Read code, fork, open issues/PRs |
| Collaborators (invited) | Push branches, review, merge (per branch rules) |
| Repo owners | Admin, protection rules, invites |
| Nobody remote | Push to a maintainer’s MacBook or production host |

Production (`cr8.li` and similar) is **gated**: deploy only via operator tools
and credentials that never live in the public repo. Casual development is
**fully local** — see the [handoff](https://hareeshnagaraj.github.io/cr8/handoff.html).

## Day-to-day for collaborators

1. Run local (handoff page or README).
2. Open an **issue** for bugs/features (templates under **New issue**).
3. Branch off `main`, PR back.
4. Do not commit secrets, `ops/env`, or personal hostnames.

## Feature requests as background work

Prefer small issues labeled `enhancement` or `good first issue`. One problem +
smallest proposal. Maintainers triage when they have time — this project is
meant to move casually in the background.

## Agents

Paste the agent block on the [handoff page](https://hareeshnagaraj.github.io/cr8/handoff.html)
into Claude / Cursor / Codex / Grok. Agents should stay local-only unless
explicitly asked to touch ops.
