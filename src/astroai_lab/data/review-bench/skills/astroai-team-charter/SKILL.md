---
name: astroai-team-charter
description: >-
  How to run an AstroAI Studio team as its Lead — choosing between a
  one-shot specialist consult and a durable teammate, the standard role
  roster and what each role is for, the brief a delegated member needs,
  how to split work that touches shared files, and when to escalate from
  the chat to real CANFAR batch compute. Use when a task is large enough
  to split, when a team or roster is mentioned, or before delegating work
  to more than one helper.
whenToUse: >-
  The user asks for a team, several perspectives, parallel investigation, or
  a named specialist; or a task spans more than one discipline (coding plus
  statistics plus figures, say) or would take several independent
  investigations to settle.
metadata:
  domains: orchestration, delegation, multi-agent, canfar
  panel: review-bench preset, agent teams
---

# AstroAI Studio team charter

Two delegation mechanisms are mounted, and they are **not** interchangeable.
Pick one per piece of work; do not run both at the same task.

| | Who is on the team | Lifetime | Use it for |
|---|---|---|---|
| **Team** (roster + shared board) | teammates *you* create by name | durable, survives reloads; messages queue | work you will revisit, parallel workstreams, anything whose state must outlive one turn |
| **Specialist consult** (`ask_<role>`, Studio Team preset) | a fixed 14-persona roster | one fresh child per call, continuable | a specific expert judgement, a blind opinion, a rubric-driven review |

A teammate is a real session with the same workspace, tools, and permissions you
have. A consult is a bounded specialist you question and then stop paying for.
Neither one is a substitute for running the experiment.

## Before delegating anything

Delegation costs tokens, wall-clock, and — on CANFAR — session quota. Earn it:

1. **Can you settle it yourself in one command?** Then do that instead.
2. **Is the task falsifiable as written?** A delegated brief with no observable
   outcome returns prose, which is worse than nothing because it looks like work.
3. **Do you know what the answer would change?** If not, the task is not ready.
4. **Check headroom first.** Call `session_resources` before promising heavy work,
   and load the `canfar-session` skill for storage and quota rules.

## Standard roles

Split by *discipline of evidence*, not by file. A role earns a slot when it can
produce a test, a derivation, or a measurement the others cannot.

| Role | Owns | Typical deliverable |
|---|---|---|
| `engineer` | execution paths, tests, determinism, resource use | a diff, a failing-then-passing test, a benchmark number |
| `astrophysicist` | astronomical conventions, sample selection, catalogue/photo-z systematics | a check against a reference catalogue or a standard |
| `cosmologist` | inference, priors, likelihoods, cosmology-specific systematics | a posterior sanity check, a prior-sensitivity run |
| `statistician` | uncertainty calibration, multiplicity, leakage, power | an error budget, a calibration plot, a null test |
| `ml` | training/eval correctness, ablations, seeds, numerics | seed-variance sweep, ablation table |
| `system` | dependencies, lockfiles, containers, CI, runbooks | a reproducible environment, a CI gate |
| `distributed` | queues, parallelism, DAGs, data movement | a job graph with resource forecasts |
| `writing` | claim calibration, structure, captions, figure honesty | a claim-by-claim evidence table |
| `plots` | axes, units, colormaps, regenerability | a figure regenerated from a committed script |
| `innovator` | the strongest next falsifiable experiment | one experiment, with its cost and its decision rule |
| `skeptic` | the strongest case the headline result is wrong | a specific counter-hypothesis plus the test that separates them |

Scale the roster to the task: two teammates for a scoped bug, four to six for a
science result, more only when the workstreams are genuinely independent. The
domain's ceiling is 16 teammates, and every name is spent forever once reserved
— a failed creation still burns its name. Choose names before creating anyone.

## The brief

A fresh teammate has **no memory of your conversation**. A fork teammate has your
completed turns but a fork is a one-shot inheritance, not a conversation. Either
way, the brief is the whole context. Six parts, no preamble:

```
Role:        <one of the standard roles above>
Question:    <the single question this member must answer>
Evidence:    <paths, claim ids, commands, commit — the minimum to act>
Method:      <what it must actually run or measure, not "investigate">
Done when:   <the observable outcome, e.g. "a number and the command that produced it">
Boundaries:  <paths it may write, or "read-only; report, do not modify">
```

Rules that follow from how the harness works:

- **Never paraphrase the artefact into the brief.** Give paths and line numbers.
  Summarising pre-forms the judgement and destroys the second opinion's value.
- **One question per member.** Two questions in one brief returns the easier
  answer for the whole lot.
- **State the write scope.** Teammates share one workspace and one working tree.
  Two members editing the same file is a self-inflicted merge conflict; the task
  board only *warns* about overlapping paths, it does not lock them.
- **A teammate that returns nothing must list what it checked.** An empty answer
  is only legitimate as "I ran these checks and found nothing".
- **A failed member is an infrastructure event, not a clean result.** Record the
  tool result; do not read silence as "no findings".

## Coordinating

The shared task board is the durable record; use it for anything longer than one
message. Create a task with a title, the details, its dependencies
(`blockedBy`), and the paths it expects to touch. Dependencies must stay acyclic
— a task is claimable only once everything it blocks on is complete. Claim a task
before working it, complete it when done, and expect an update based on an
outdated revision to be *rejected* rather than merged: re-read, then retry.

Messaging is durable and exactly-once. A send reports `accepted` (delivered now)
or `queued` (stored, delivered when the target wakes). **A queued message is
already saved — never resend it.** Steer a running member with a message rather
than interrupting it; interrupt only to stop a turn you no longer want.

Lead-only operations: creating teammates, and interrupting them. Any member may
message any other member and use the board.

## Escalating to real compute

The chat runs on the interactive session: modest CPU/RAM, a shared GPU, ~4 days,
and a small contributed-session quota. Long training, wide sweeps, and anything
GPU-heavy belong on batch compute, not here.

The escalation ladder, cheapest first:

1. **In-session, in a subagent** — fan out with the workflow tool when the
   subagents run inside one session's budget. Good for many small
   investigations, not for many CPUs.
2. **`cluster_start` → `job_submit` / `job_run` → `jobs_report`** — the MCP job
   tools. Autoscaling Ray workers, real CPU/RAM/GPU per job, durable run ids,
   logs and status. Submit once, keep working, then report.
3. **A DAG of jobs** — when the work has real dependencies, express the shape as
   the task board's `blockedBy` graph (the visible plan) *and* as submitted jobs
   (the execution), then report with `jobs_report`.

Rules for the ladder:

- Call `session_resources` and `cluster_status` before promising throughput;
  report what the cluster actually has, never what was requested.
- Interactive work is not a queue: never block a chat turn on a job you could
  submit and poll.
- Job ids are durable handles. Put the run id in the task board so a later
  session — or another teammate — can pick the work up.
- `/scratch` dies with the session. Anything that must outlive it goes to
  `/arc`, and saying so is part of the report.

## Reporting

- Answer the question asked, with the evidence that settles it, then the caveat.
- Label every claim **established** (a command ran and its output says so),
  **suggestive** (consistent evidence, no decisive test), or **speculative**
  (reasoning only). Nothing in the write-up may speak more strongly than its
  label.
- Preserve dissent. When the team is split, say so and name the test that would
  settle it — do not average opinions into a confident-sounding middle.
- Report cost honestly: what was submitted, what it consumed, what is still
  running, and where its outputs live.

## Anti-patterns

- A team created to make a task *look* thorough. If there is one question,
  answer it.
- Polling `list_agents` in a loop. Wait for a change instead, then read state once.
- Teammates that only read and summarise. A member with no test attached to its
  brief will return prose.
- Reassuring the user that something works because a member said so. Evidence is
  a command and its output, or a `file:line`.
- Reporting a cluster as available, fast, or big without `session_resources` /
  `cluster_status` output backing the claim.
