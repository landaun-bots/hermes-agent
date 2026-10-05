---
sidebar_position: 95
title: "CI `disabled_manually` on forks"
description: "Why the ci.yaml workflow can show state disabled_manually on this fork and how to recover cleanly"
---

# CI workflow showing `disabled_manually` on a fork

`landaun-bots/hermes-agent` is a **fork-of-a-fork** (upstream is
`NousResearch/hermes-agent`). GitHub Actions treats forks more strictly than
regular repos, and that interacts badly with a workflow that leans on reusable
workflows (`on: workflow_call`). This note records what was diagnosed and how it
was resolved so a future session doesn't have to rediscover it.

## Symptom

The main CI workflow (`.github/workflows/ci.yaml`) repeatedly reports:

```
state: "disabled_manually"
```

`disabled_manually` is the state GitHub writes when the workflow is disabled via
an explicit `PUT /actions/workflows/{id}/disable` call (i.e. repo-admin action).
It is **not** the automatic fork states:

- `disabled_fork` — scheduled (`on: schedule`) workflows in forks auto-disable.
- `disabled_inactivity` — 60 days with no activity.

`ci.yaml` has no `schedule` and is heavily used, so neither automatic state
applies — which made the state look like an external "attacker" at first.

## Root cause

Two things combined:

1. **Fork-of-a-fork + `default_workflow_permissions: read`.** The fork's Actions
   permission defaulted to `read`, and the CI orchestrated many jobs through
   reusable workflows (`workflow_call`). GitHub's fork-safety logic can disable
   a workflow file when the fork's Actions setup doesn't clearly signal "this
   fork actually runs its workflows."

2. **The original CI was `workflow_call`-heavy.** The CI re-architected into a
   flat single file (see "option 3" rework) removed every
   `uses: ./.github/workflows/...` dependency, eliminating the `workflow_call`
   surface that trips the fork guard.

> Note on an important lesson: a long follow-up investigation mistook *our own*
> `gh api .../disable` probe calls (used to test a self-heal watcher) for a
> recurring external "disabler." The "≤10s live actor" was our own test
> command. Any future "who is disabling CI?" question should first grep the
> session transcript (`session.v4.jsonl.zstd`) for
> `workflows/373315761/disable` before assuming an external actor.

## Resolution

1. **Inline all `workflow_call`** sub-workflows into `ci.yaml` (no more
   `uses: ./.github/workflows/*`).
2. **Set `default_workflow_permissions` to `write`** on the fork so GitHub's
   fork-guard doesn't flag the CI:
   ```
   gh api --method PUT "repos/landaun-bots/hermes-agent/actions/permissions/workflow" \
     -f default_workflow_permissions=write
   ```
3. **Scope the `landaun-bots` tokens down** (remove `admin:*`, `delete_repo`,
   `audit_log`). Owner identity still implies repo-admin, so scope reduction is
   hygiene, not the mechanism — but it limits blast radius.
4. **Re-enable the workflow once and leave it alone** — do not wrap it in a
   self-heal watcher, which only masks the state and can itself be the source
   of a "flip."

## Recovery command

```
gh api --method PUT "repos/landaun-bots/hermes-agent/actions/workflows/373315761/enable"
```

(`373315761` is the workflow id GitHub assigned to `ci.yaml` on this fork.)
