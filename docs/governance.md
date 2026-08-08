# Governance: Argus as a reviewer (and the "second approver" question)

Argus is designed to **strengthen** review, not launder it. This page is the honest
guide to wiring it into branch protection — including the ways it can quietly go
wrong.

## The core tension

A bot that **auto-approves the author's own PRs** to satisfy a *required review*
doesn't add a reviewer — it **removes** the human gate and repaints it. If a
maintainer can merge anything with only `argus[bot]`'s approval, then for that
maintainer there is effectively no review requirement at all. That is a real
weakening of branch protection, and you should adopt it, if ever, with eyes open.

Argus therefore ships **`allow_approve: false`**. Out of the box it comments and
requests-changes; it never approves. Everything below is opt-in and is a decision
for the repository owner, not for Argus.

## Recommended patterns (strong → permissive)

### 1. Argus as a required *status check* (recommended)
Make "Argus review" a **required check**, not an approver. Configure the gate so a
`request-changes` verdict fails the check. Keep `allow_approve: false`.
- ✅ Every PR gets a substantive automated review that can *block* merge.
- ✅ Human approval is still required and never bypassed.
- ✅ No "bot approved my own PR" footgun.

### 2. Argus **plus** a human (belt-and-suspenders)
Require **2 approvals**: one human + `argus[bot]`. Set `allow_approve: true`.
- ✅ Adds a rigorous automated pass on top of, not instead of, a human.
- ⚠️ Slightly slower; needs the bot's approval to *count* (see wiring below).

### 3. Argus as second approver on **low-risk paths only**
Use CODEOWNERS / path rules so `argus[bot]` can satisfy the required review **only**
for low-risk globs (docs, tests, config), while `strict:` paths (auth, billing,
migrations) still require a human. This is the most defensible version of "an agent
unblocks me on weekends."
- ✅ Removes the human bottleneck for genuinely low-risk changes.
- ✅ Keeps humans on the code that can actually hurt you.

### 4. Argus as the second approver, everywhere (highest risk — owner's call)
Let `argus[bot]`'s approval satisfy the required review on any PR, including the
author's own.
- ⚠️ This effectively lets a maintainer self-merge with only an automated review.
  Only appropriate for a small, high-trust team that has *explicitly* accepted the
  trade-off, and ideally never for `strict:` paths. Document the decision.

> Whatever you choose, **write it down** (in this file) and have a human other than
> the author sanction the change. Governance you can't point to isn't governance.

## Wiring: making a bot approval "count"

For `argus[bot]`'s approval to satisfy a required review, GitHub needs the approving
identity to have write access and to not be the PR author. The `claude-code-action`
submits the review as the app's bot identity. To require it, add `argus[bot]`
(or your app's bot) as a required reviewer via a ruleset/CODEOWNERS entry scoped to
the paths you chose above. Reviews submitted with the default `GITHUB_TOKEN`
(`github-actions[bot]`) do **not** reliably count — use the app identity.

## What Argus will never do
- Approve a PR authored by a bot/automation account (`never_approve_authors`).
- Approve a PR with an unresolved `blocker`/`major` finding.
- Approve "just to unblock." If Argus wouldn't sign off as a careful human, it
  comments instead.

## Turning approval on
1. Read this page.
2. Set `verdict.allow_approve: true` in `config/argus.yml`.
3. Choose a pattern (2 or 3 above, ideally) and wire the ruleset accordingly.
4. Have a second human approve *that* change. Now it's a governed decision.
