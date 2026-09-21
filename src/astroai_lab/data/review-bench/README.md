# Review bench — an eight-persona DeepSeek-Harness panel for code, analyses, and papers

> **Just want to use it? Read [`HOWTO.md`](HOWTO.md) instead — three steps, one page.** The rest of
> this file is the design record: what the harness can do, why the protocol is shaped this way, and
> what it cannot do.

A dsh agent preset (`review-bench`) plus a protocol skill (`review-panel`) that turn a session into a
**chaired review panel**: eight specialist reviewers, each with its own system-prompt persona, its own
pinned model, a research-and-run tool set, and no ability to delegate further. A blind parallel round,
a targeted cross-examination, evidence-gated verdicts, a persisted panel record, and claim labels the
write-up has to respect.

```
~/dsh/
  presets/review-bench/agent.cordis.yml   the panel composition (chair persona + 8 delegation tools)
  presets/review-bench/preset.yml         picker display name/description
  skills/review-panel/SKILL.md            the protocol the chair runs
  skills/review-panel/references/rubric.md          finding schema, severities, verdicts, labels
  skills/review-panel/references/panel-round1.js    scripted breadth-round template (workflow tool)
  skills/review-panel/references/brief-template.md   what phase 0 fills in
  skills/review-panel/references/report-template.md  what you read at the end
  HOWTO.md                                the simple user guide (start here)
  cordis.bench.patch.yml                  registers the preset root with dsh (web profile)
  install.sh                              validates YAML + installs the patch layer
  validate.mjs                            schema-checks every row against an installed dsh,
                                          then runs the harness's own preset discovery
```

## Run it

```sh
~/dsh/install.sh                  # once per machine; safe to re-run, --check to dry-run
~/dsh/dsh-web.sh /scratch/src/torchregress   # any repo under /scratch/src; web UI on http://127.0.0.1:3080
```

Create a session and pick **Review bench** in the preset picker. Then ask in plain language:

> Panel this: `panel/2026-09-11-cqr-coverage/00-brief.md` — claims C1–C3, artefact torchregress @
> HEAD. Run round 1 blind, then cross-examine anything you can't settle.

The chair does the rest: freeze, brief, dispatch, triage, cross-examine, verdict, report under
`panel/<id>/`. Needs `DEEPSEEK_API_KEY` (or another provider configured on the Models page).

## Verify

```sh
node ~/dsh/validate.mjs --node-modules <a-dsh-install>/node_modules
```

Checks every row's `config` against that plugin's own schema, resolves every row's package name,
asserts the panel contract per row (spawn provider, continuable, `maxDepth: 1`, a persona with its
alternative-explanation open and ≥3 mandatory probes, an allow filter, a pinned model), confirms
every filter name is registered by a mounted row, cross-checks the three places the roster and the
severity vocabulary are written down (skill table ↔ preset tool names, rubric ↔ workflow template
enum), checks the workflow script body parses and stays inside the tool's schema subset, and — the
final gate — runs the harness's own `discoverPresets()` over the root, which is the same call the
web picker makes. 48 checks. Negative controls run: a typo'd filter name, a renamed tool, a deleted
probe, a broken severity vocabulary, an unsupported schema keyword, and an unresolvable plugin row
(each caught, the last one exactly as the picker would show it). Then:

```sh
dsh --profile web --dump-config | grep -A6 agent-presets   # the overlay lands on the row
```

**Verified on this machine** (dsh 0.1.5-rc.1, 2026-09-11): YAML parses; 48/48 schema, contract,
consistency and discovery checks pass; the composed web-profile tree carries `~/dsh/presets` on the
`agent-presets` row with no unmatched patch targets; the web profile boots with the installed patch
layer. **Not verified**: a live session on the preset and a real child run — that needs a model
credential, and presets exist only in the web profile in this version, so it cannot be exercised
headlessly (the headless-browser route was tried and the host has no Chrome shared libraries; the
harness's own discovery call, which gates the picker, does pass). The first panel call is the end-to-end test: if a `toolFilter` name were wrong, the
child start fails loudly with the list of known tool names, which is a one-line fix.

---

## 1. What dsh actually gives you (verified against 0.1.5-rc.1)

Everything below was read out of the shipped packages, not inferred from marketing.

| Mechanism | What it buys | Where it comes from | Honest limit |
|---|---|---|---|
| **Agent presets** | Per-session composition: tools, prompt sections, skills, and an agent-scoped **persona row** that shadows the deployment persona for that session. One process runs sessions on different presets side by side. | `@deepseek-ai/dsh-agent-presets`, `@deepseek-ai/dsh-persona`. Roots: shipped + configured `roots` + `$DSH_HOME/.agent-presets`. | Presets exist only in the **web** profile in this version; headless/sdk/acp mount the host-plane composition. A preset file is a full composition — no inheritance, so this one is a copy of `standard`. |
| **Per-child persona** | `dsh-tool-subagent` config `persona` installs a child-local `deployment:persona-prefix` section **before the child is published**: the child's first request already sees it, and no parent/sibling text leaks in. This is what makes a delegation tool a *specialist*, not a prompt-stuffed generic child. | `packages/subagent/tool-subagent` + the in-process `spawn` provider (capability `persona`). | Same-process role-play, not confinement. One shared base model means correlated blind spots; heterogeneity has to be bought with different models. |
| **Per-child reasoning effort** | `agentOptions: {reasoningEffort, maxTokens}` (no `model` — astroai does not preset models; children inherit the session route from dsh Settings). | `dsh-tool-subagent` config. | Heterogeneity across models is the user's Settings choice, not a per-row pin. |
| **Per-child tool filter** | `toolFilter.allow/deny` masks ancestor-layer tools for the child, applied to schemas *and* execution, before child-local tools are added. The panel uses `allow: [read, read_image, glob, grep, bash, skill, web_search, todo_write]` — a reviewer that can reproduce numbers but owns no editor tool. | `ctx.tools.restrict()`; since the fix recorded at `packages/core/tools/src/index.ts:1128`, preset-layer tools are ancestor contributions and *are* filterable. | **Visibility is not authority.** `bash` can still write; the sandbox (workspace-write) is the real boundary and the protocol's tree check is the detector. Unknown names fail a child start loudly. |
| **Depth cap** | `maxDepth: 1` on every panel row: the chair (depth 0) may start a child (depth 1); a child calling any delegation tool derives depth 2 and is rejected. The panel cannot sprawl or recurse. | `SubagentCapabilities.depthLimit`; the cap is checked at every start. | The cap is per tool row, not a global budget; a child still *sees* the tool and gets an errored result if it tries. |
| **Parallel sibling delegations** | Several `subagent` calls in one assistant message **overlap** (`isConcurrencySafe`), so round 1 is a genuine parallel panel, not eight serial runs. | Agent Note `2026-08-09-parallel-subagent-delegations` (implemented, archived). | Child results commit in model order, so a slow reviewer holds up later results. Concurrent children share the workspace. |
| **Continuable children + `send_message`** | A child keeps its conversation: the chair can question it again, and a resident child can message its parent. Cross-examination is a real dialogue, not a fresh re-prompt. | `backgroundMode: continuable`, `dsh-tool-subagent-control`, `send_message` / `list_agents` / `interrupt_agent`. | One-way reach: parent ↔ direct children. Peer-to-peer between specialists needs the experimental Team packages (§6). |
| **`workflow` tool** | Model-written JS that fans out children with `parallel`/`pipeline`, per-call `provider`/`model` overrides, and structured JSON outputs — the breadth tool (one lens per file/claim/dataset with deterministic aggregation). | `@deepseek-ai/dsh-tool-workflow` + `dsh-workflow-worker-thread`. | Workflow children inherit the parent's persona: they are the *same* identity with a task, not specialists. Persona fidelity comes from the delegation tools; breadth comes from the script. No filesystem/network in the script itself. |
| **Experimental Agent Teams** | Named durable teammates, any member messaging any other, shared task board with CAS revisions, `wait_agent`, `interrupt_agent`. The only true peer-to-peer surface in the harness. | `@deepseek-ai/dsh-experimental-agent-team` + `-tool-agent-team` (+ `agent-team-profile` patch), published under an experimental name with no stability promise. | Teammates inherit the Lead's composition, so per-member *system-prompt* personas are not available; identity has to ride the `spawn_teammate` prompt. One process, one shared checkout, no worktree isolation. |
| **External orchestration** | `dsh --profile sdk` speaks JSON-RPC over stdio; a Python SDK (`deepseek-harness-sdk`) drives sessions and can hold per-session state. A router process could relay messages between N persona sessions. | `packages/bundle/sdk-app`, `python/sdk`. | Session creation takes model/patches but no preset selector in this version; you would drive personas as profiles/patches, and write the relay yourself. |

**What is *not* available** (so the design does not pretend otherwise): no cross-session messaging, no
per-teammate preset, no worktree isolation for children, no hard read-only reviewer, no grounding in
ground truth. A panel is a *critic ensemble*, not an oracle.

## 2. The bench as built

One preset, copied from the shipped `standard` preset and extended:

- **Chair persona** (`dsh-persona`): the session agent is a chair, not a reviewer. It frames, freezes,
  dispatches, verifies, records; it never adds an opinion of its own without attaching a test.
- **Eight delegation tools** (`ask_statistician`, `ask_mathematician`, `ask_data_scientist`,
  `ask_ml_engineer`, `ask_physicist`, `ask_astrophysicist`, `ask_software_engineer`,
  `ask_writing_editor`), each `provider: spawn`, `backgroundMode: continuable`, `maxDepth: 1`,
  research-and-run `toolFilter`, a pinned model, and a persona that states its remit, its check
  order, its required evidence, and its **out-of-scope rule** (cross-domain issues get one line —
  no padding, no persona drift).
- **Generic delegation + workflow tools** kept from `standard`: `subagent`, `subagent_fork`,
  `workflow`, `ralph`, jobs, todo, web search, plan mode, compaction. The panel is an addition, not
  a replacement.
- **Skill root**: `skill-filesystem.customSkillDirs = !!js process.env.HOME + '/dsh/skills'`
  (portable on purpose — `resolve()` does no `~` expansion, so a literal `~/dsh/skills`
  would resolve under the repo cwd), so `review-panel` is
  in the catalog of every agent on this preset (the chair loads it; specialists can too). Repo-local
  `.dsh/skills` (rank 100) and user roots still override.

The eight roles were chosen so their *failure modes* are disjoint: inference (statistician), proof
(mathematician), data (data scientist), training (ML engineer), physical model and error budget
(physicist), domain conventions and selection (astrophysicist), code as a re-runnable artefact
(software engineer), language and claim strength (writing editor). Overlap is wasted budget; the
protocol pushes a finding to the one role that can test it.

## 3. The protocol, and why it is shaped this way

`skills/review-panel/SKILL.md` is the operational core; the design rules and their reasons:

| Rule | Why |
|---|---|
| **Freeze first** (commit, tree status, env, reproduce the headline command before any review) | Reviewing an unfrozen artefact produces untraceable findings; a panel that cannot run the thing is reviewing a claim, not a result. |
| **Round 1 blind and parallel, all eight in one message** | Independent errors are the only reason a panel beats one critic. Sequential or visible-round-1 panels collapse into anchoring. Parallel sibling delegation is what makes this cheap. |
| **Different personas, pinned models** | Diverse role prompts are what make multi-agent evaluation work at all (Chan et al., *ChatEval*, ICLR 2024, [arXiv:2308.07201](https://arxiv.org/abs/2308.07201)); debate between model instances improves factual validity (Du et al., [arXiv:2305.14325](https://arxiv.org/abs/2305.14325)); and a panel of smaller models from *disjoint* families beats a single large judge while costing less (Verga et al., *PoLL*, [arXiv:2404.18796](https://arxiv.org/abs/2404.18796)). |
| **Anonymised cross-examination, targeted at contested findings only** | Judges carry position, verbosity and self-enhancement biases (Zheng et al., [arXiv:2306.05685](https://arxiv.org/abs/2306.05685)), and models recognise and favour their own output (Panickssery et al., [arXiv:2404.13076](https://arxiv.org/abs/2404.13076)). Names and authorship come out; only evidence stays in. Re-running the whole panel is not a second opinion, it is a second draft. |
| **Evidence or discard; every dropped finding gets a reason** | Task-verification and inter-agent misalignment are the dominant failure clusters in multi-agent systems (Cemri et al., *MAST*, [arXiv:2503.13657](https://arxiv.org/abs/2503.13657)). A finding without a pointer is noise that survives consensus. |
| **Devil's advocate rotation; dissent preserved verbatim; no majority verdicts** | Consensus among correlated critics is not truth. Structured elicitation (the Delphi tradition — Dalkey & Helmer 1963; Linstone & Turoff 1975) keeps disagreement visible and asks for the test that would settle it. |
| **Mandatory probes per persona, and the strongest alternative explanation stated first** | A reviewer that only opines has not reviewed. Each persona carries 3 probes it must run and report (coverage recomputation, duplicate/split check, degenerate-input run, convention table, suite run, claim-to-table map, …), and must open with how the result could be wrong. |
| **Evidence audit before belief (chair re-runs the cited command)** | Reviewers overreach and occasionally hallucinate outputs. A quoted output that does not reproduce demotes the finding to `UNVERIFIED` and becomes a finding about the reviewer. |
| **Independent replication of headline claims, by a fresh child with no persona lens** | "Established" has to mean *reproduced by a different route*, not *agreed upon*. Eight critics agreeing on an unreproducible number is the exact failure the panel exists to catch. |
| **Machine-readable ledger (`01-findings.json`) with carry-forward** | Persistence before reasoning: compaction eats raw returns, and a diffable ledger is what makes a second panel over a later commit meaningful instead of a fresh opinion. |
| **Conformity risk on position flips** | A reviewer that reverses after reading peer findings has demonstrated social influence, not evidence; the discriminating test is re-run by someone who did not flip. |
| **Phase 6 "raise the ceiling" (generalization, distinguishing prediction, highest information gain per unit cost)** | A critic-only panel makes work safer, not better. The constructive pass is two or three children and produces owned opportunities — never evidence, never a label change. |
| **Open falsification tests + `panel/history.jsonl` calibration** | Every surviving claim leaves with the cheapest test that could still kill it, and every panel appends its labels and elicited credences to a history file whose outcomes get filled in later. An instrument nobody checks against reality is a ritual. |
| **Report audit against the ledger (writing editor)** | The report is the artefact people read. Every ledger entry must appear with its status, every dropped finding must carry its reason, and the prose must not outrun the label. |
| **Claim labels: established / suggestive / speculative, monotonically binding on the prose** | The usual failure of an exciting result is a wording that outruns its evidence; the label is what the writing editor enforces. Cheap, and it is the difference between a panel and a vibe check. |
| **Re-review is scoped to the finding; fixes land after the verdict** | Re-running everything invites motion without progress and hides whether the specific defect is gone. |
| **Impact pass only for publication-bound work** | Closest prior work, the delta, who acts on it, what a hostile referee says first — with citations that were actually fetched. End-to-end agentic-research systems already pass workshop review (Yamada et al., *AI Scientist-v2*, [arXiv:2504.08066](https://arxiv.org/abs/2504.08066)); the panel's job is not to write the paper but to be the referee that system does not have. |

## 4. Operations

**Add or drop a role.** Copy a sibling row in `presets/review-bench/agent.cordis.yml`, change
`id`, `toolName`, `persona`, model. Tool names are what the chair sees: use
`ask_<role>`. The tool row's `id` must be unique inside the preset.

**Cost.** Round 1 is eight full child runs (each with its own context and tool calls); round 2 adds
up to three children per contested finding. Expect a real panel to dominate the token bill of a
review session, and budget by trimming: drop rows for roles that are irrelevant to the artefact, use
`deepseek-v4-flash` everywhere, or run round 1 as a scripted breadth round
(`references/panel-round1.js`) when per-role identity does not matter. The chair's own preset adds
eight tool schemas to every request in the session — that is the standing cost of the bench.

**Tuning the panel.** Two dials matter more than the rest. *Model diversity*: a panel of one model
family shares that family's blind spots, so as soon as a second provider is configured on the
Models page, repoint 2–3 rows at it (the data scientist, ML engineer and software engineer are the
cheapest to move — they are the empirical lenses). *Persona count*: eight is a starting point, not a
law; four well-chosen lenses with mandatory probes beat eight without, and the chair's standing
schema cost scales linearly with the row count.

**Persistence and resume.** Every child is a normal dsh session (JSONL under `$DSH_HOME/sessions`)
with a descriptor carrying its persona; the panel record lives in the repo under `panel/`. Both
survive a restart, and `list_agents` plus `send_message` reach children that are still alive.

**Drift.** The preset is a copy: a harness upgrade does not update it. When
`@deepseek-ai/dsh-agent-presets` moves, re-copy the shipped `standard` preset and re-apply the three
marked sections at the top of the file (identity, skill root, panel rows).

**Windows.** The shell rows are platform-gated, so `bash` does not exist there; drop it from the
eight `toolFilter.allow` lists or the first child start fails loudly with the known-names list.

## 5. What this cannot do

- **It is not ground truth.** Eight critics agree on a wrong answer happily; correlated base models
  share blind spots. The protocol mitigates this with blind rounds, anonymised cross-examination,
  disjoint roles and (if you add them) different model families — it does not solve it.
- **Role-play is not expertise.** A persona changes what the model attends to; it does not add
  knowledge or guarantees about a domain.
- **No hard confinement.** `toolFilter` is composition, not a security boundary; `bash` inside a
  workspace-write sandbox can still modify the repository. Freeze the artefact, check the tree.
- **Preset-only surface.** Headless one-shots in 0.1.5-rc.1 do not mount presets; the panel is a web
  session feature (or an SDK orchestration you write yourself).
- **Experimental dependencies.** The Team packages carry no stability promise; pin versions.

## 6. Extension paths, in order of payoff

1. **Heterogeneity.** Add a second provider family on the Models page (`llm-pi-ai` settings), then
   repoint 3–4 roles at it. A panel that spans families has less intra-model bias (PoLL) and
   cross-checks its own priors.
2. **Peer-to-peer specialists (experimental).** The panel is a star: specialists talk to the chair,
   not to each other. For durable teammates that message any member and share a task board:
   `dsh plugin --profile web add @deepseek-ai/dsh-experimental-agent-team @deepseek-ai/dsh-experimental-tool-agent-team`,
   restart the profile, then edit `presets/review-bench/agent.cordis.yml`: inside the `delegation`
   group disable the `tool-subagent-control` and `tool-subagent-list-agents` rows (the Team tools
   shadow those names for members), set both `tool-subagent` rows to `backgroundMode: one-shot`, and
   append

   ```yaml
       - id: agent-team
         name: '@deepseek-ai/dsh-experimental-agent-team'
         config: {maxMembers: 8, maxTasks: 256, maxPendingMessagesPerMember: 64, maxMessageBytes: 65536, disposalTimeoutMs: 5000}

       - id: tool-agent-team
         name: '@deepseek-ai/dsh-experimental-tool-agent-team'
         config: {freshProvider: spawn, forkProvider: fork}
   ```

   Teammates inherit the chair's composition, so their *identity* rides the `spawn_teammate` prompt
   while the chair keeps the eight persona lenses for itself; peer messages land in each member's
   history as user-role text with a stable sender, and the task board is compare-and-set. Carry the
   same evidence rules into teammate prompts — a peer that reports "looks fine" is exactly the
   failure mode the protocol exists to prevent.
3. **External orchestration.** A Python SDK process per persona profile, with your own relay, buys
   process isolation, per-persona profiles, and transcripts you control — at the cost of writing the
   router, the durability, and the aggregation yourself.
4. **Scheduled and CI panels.** `dsh-timeout-policy`, jobs and the `schedule` bundle make a
   nightly panel over the day's commits possible; the cost model is the same, so gate it on
   diff size.
