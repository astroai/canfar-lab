# How to use the review bench

Eight specialist AI reviewers — statistician, mathematician, data scientist, ML engineer, physicist,
astrophysicist, software engineer, writing editor — review your code or paper in one guided session
and write you a report with evidence attached.

You do three things: start dsh, pick the preset, paste one prompt.

---

## 1. Set up (once per machine)

```sh
~/dsh/install.sh
```

That's it. (Re-run it any time; `--check` only looks, `--force` overwrites.)

A model credential, first match wins:

| Credential | Setup | Panel rows |
|---|---|---|
| opencode Zen key | `~/dsh/use-opencode-go.sh --smoke` (needs `opencode auth login` first if the stored key is dead) | native `deepseek-v4-pro/flash` — zero remaps |
| Gemini API key ([aistudio](https://aistudio.google.com)) | `export GEMINI_API_KEY=…` + the `google` route below | remap §1b |
| DeepSeek key | `export DEEPSEEK_API_KEY=sk-...` | as shipped |
| ChatGPT Plus/Pro (Codex sorts) | laptop dsh Models page → add `openai-codex` via OAuth (pi-ai can't reuse `~/.codex/auth.json`) | remap to `gpt-5.x` ids |
| Cursor token | **not usable** — no pi-ai provider speaks Cursor's service-scoped token | — |

```sh
~/dsh/use-opencode-go.sh --smoke   # opencode Zen key → opencode-go route (no DeepSeek key needed)
```

### 1b. Gemini route (when you have the key)

Append to `~/.dsh/settings.yaml` (create it if missing):

```yaml
llm-pi-ai:
  providers:
    google:
      apiKeyEnv: GEMINI_API_KEY
agent-default-model:
  provider: google
  model: gemini-2.5-flash
```

Then remap the eight `model:` pins in `~/dsh/presets/review-bench/agent.cordis.yml`:
statistician, mathematician, physicist, writing_editor → `gemini-2.5-pro`
(keep their `reasoningEffort`); data_scientist, ml_engineer, software_engineer,
astrophysicist → `gemini-2.5-flash`. Re-run `~/dsh/install.sh --check`
(50 checks) and go: `export GEMINI_API_KEY=…` plus `~/dsh/panel.sh` or `dsh-web.sh`.

## 2. Review something

```sh
cd /scratch/src/torchregress          # the repo you want reviewed
npx -y @deepseek-ai/dsh web           # opens http://127.0.0.1:3080
```

In the browser:

1. Click **New session**.
2. Pick **AstroAI Studio Team** in the preset list. (This is the important click — without it you get the
   ordinary coding agent.)
3. Paste the prompt below, with your claims filled in.

If the repo has its own `.dsh/cordis.patch.yml` (all of them except
torchregress-research), keep both — `--patch` is a launcher flag, so it goes
before `web` (`web` rejects it: `error: unknown option '--patch'`):

```sh
npx -y @deepseek-ai/dsh --patch .dsh/cordis.patch.yml web
```

Or use the wrapper, which adds the repo patch when present and does the right
thing inside a CANFAR session (see §10):

```sh
~/dsh/dsh-web.sh /scratch/src/torchregress
```

## 3. The prompt to paste

```
Panel-review this repo at HEAD.

Claims under review:
  C1: <one sentence, with a number and a threshold — e.g. "90% conformal intervals cover 90% on the 2024 split">
  C2: <...>

Write the brief, run the panel, and give me the report.
```

That's enough. Two variations:

- Not sure what the claims are? Write *"Propose the claims and confirm them with me."* The chair
  will ask you one question, then run.
- Small job (one file, one table)? Add *"Three lenses is fine."* Full panel is the default for a
  paper or a whole result.

## 4. What happens (you mostly watch)

| Step | What the chair does | Roughly |
|---|---|---|
| Freeze | records commit, checks the tree is clean, runs your reproduce command | seconds |
| Round 1 | sends all eight reviewers at once, blind — each must run its own probes | the long part |
| Audit | re-runs the commands reviewers cited | minutes |
| Replication | a fresh, unbiased agent re-derives your headline number another way | minutes |
| Cross-examination | only the disputed findings get a second look | minutes |
| Report | writes the verdict, the fix list, and the open tests | minutes |

You can interrupt at any point — the chair takes messages.

## 5. What you get

In `panel/<date>-<name>/` inside the repo:

- `00-brief.md` — what was frozen and what was claimed.
- `01-findings.json` — every finding with its evidence and status.
- `02-report.md` — **read this one**: verdict per claim, fixes, disagreements, open tests.

Every claim in the report gets a label:

- **established** — reproduced independently, uncertainty checked.
- **suggestive** — reproduced once, limits remain.
- **speculative** — plausible, not tested here.

And one line you asked for without realising it: the **open falsification tests** — the cheapest
experiment that could still prove you wrong. That table is your next week's work.

## 6. What it costs

Round 1 is eight full agent runs, so a panel costs roughly what eight careful agent sessions cost;
the audit and cross-examination add a few more. It is worth it before a submission, not before
every commit.

Cheaper options that keep the rigour:

- Name three lenses instead of eight (say "three lenses" in the prompt).
- Or ask for a scripted breadth pass: *"Use a workflow script, one lens per file."*
- Point the cheaper roles at a cheaper model: edit `~/dsh/presets/review-bench/agent.cordis.yml`,
  change `model:` on a row.

## 7. If something goes wrong

| What you see | What to do |
|---|---|
| No "AstroAI Studio Team" in the preset list | Re-run `install.sh`, then restart dsh. Check with `~/dsh/install.sh --check`. |
| The preset is listed as broken | `~/dsh/install.sh --check` prints the reason (usually a package name or an edited row). |
| A reviewer fails mid-round | The report lists it under infrastructure events. The round is incomplete — ask the chair to re-run that one reviewer. |
| "Provider authentication" errors | `DEEPSEEK_API_KEY` is not set for the process that started dsh. Export it, restart. |
| You edited the preset and nothing changed | Start a **new** session; a running one keeps the composition it started with. |
| Panel is too slow | Interrupt, and re-ask with fewer lenses or "one pass each". |

## 8. Example asks

```
Panel-review this repo. Claims: C1 the photo-z bias correction removes the magnitude trend
(|slope| < 0.01 mag); C2 calibration holds at 68% and 95% on the held-out field.
```

```
Referee this draft before I submit. claims.md has C1-C4. Full panel, then the impact pass.
```

```
Something is wrong with our CRPS number — audit just that. Three lenses: statistician,
ML engineer, software engineer.
```

```
Panel the last three commits only. Claims: C1 the refactor is behaviour-preserving (parity
tests pass); C2 runtime did not regress more than 5%.
```

## 9. Cheat sheet
```
SETUP     ~/dsh/install.sh
RUN       ~/dsh/dsh-web.sh <repo>   → New session → "AstroAI Studio Team"   (laptop, browser)
TERM      ~/dsh/panel.sh <repo> "C1 <claim>; C2 <...>" <slug>        (any terminal, §10)
ASK       "Panel-review this repo at HEAD. Claims: C1 <falsifiable sentence>, C2 <...>."
OUTPUT    panel/<date>-<name>/02-report.md
LABELS    established | suggestive | speculative  (and the fix list + open tests)
CHECK     ~/dsh/install.sh --check
DETAILS   ~/dsh/README.md   (design, mechanisms, limits — not needed to use it)
```

One rule worth remembering: the panel is a critic, not an oracle. If the report says *established*,
it means someone reproduced your number a different way — that is the only thing the word means
here.

## 10. Terminal sessions (terminal, vscode/notebook terminals): `panel.sh`

```sh
~/dsh/panel.sh /scratch/src/torchregress "C1: <falsifiable claim>; C2: ..." my-slug
```

Same protocol and same artifacts as the web bench — freeze, blind parallel
round, evidence audit → `panel/<YYYY-MM-DD>-<slug>/{00-brief.md,01-findings.json,02-report.md}`
with established/suggestive/speculative labels. Needs `DEEPSEEK_API_KEY`, works
in any shell on CANFAR or your laptop. The repo patch is added automatically.
Manual equivalent (same thing, spelled out):

```sh
cd /scratch/src/torchregress
npx -y @deepseek-ai/dsh --profile headless --patch .dsh/cordis.patch.yml \
  "Load ~/dsh/skills/review-panel/SKILL.md and follow phases 0–2 for claims C1…Cn: \
freeze the artefact, run the mandatory probes via the workflow script \
(~/dsh/skills/review-panel/references/panel-round1.js), audit the evidence, \
and write panel/<date>-<name>/00-brief.md + 01-findings.json + 02-report.md."
```

Honest delta vs the web preset: headless workflow children share one generic
persona, so `panel.sh` pastes each lens's specialist text verbatim from
`~/dsh/presets/review-bench/agent.cordis.yml` into its workflow task instead of
a child-local system prompt, and cross-examination is one targeted follow-up,
not continuable dialogue. Brief, probes, audit, ledger, labels, and
falsification tests are identical.

**B. Full eight-persona panel — only where a browser reaches the web UI.**
That means your laptop (`~/dsh/install.sh` there too; nothing here is
machine-specific), via:

```sh
~/dsh/dsh-web.sh /scratch/src/torchregress   # → http://127.0.0.1:3080 → AstroAI Studio Team
```

`~/dsh/dsh-web.sh` adds `--patch .dsh/cordis.patch.yml` when the repo has one
and passes trailing flags to the web app (`-- --trusted-host <host>`);
change the port with `DSH_WEB_PORT=8080 ~/dsh/dsh-web.sh <repo>`.

## 11. Where each surface stands (verified in the shipped bundle)

The web UI needs a browser reaching it at its own origin. Every CANFAR session
forwards exactly one port (its own app's), and the dsh client resolves all RPC
to `location.origin + /api` (absolute — `resolveBase()` in
`dsh-client-connection`), with no base-path option on the server and an
explicit refusal of `--host 0.0.0.0`. So the UI **cannot** ride a subpath proxy
(`/proxy/3080/` on vscode/notebook): the page shell would load (relative
assets) but every `/api` call escapes the proxy and dies. The one upstream fix
that would unlock all of these: a `--path-prefix` server flag plus a relative
API base. Until then:

| Surface | What works |
|---|---|
| Laptop (`~/dsh/dsh-web.sh <repo>`) | **Full panel.** localhost:3080, zero proxy risk. The default. |
| `astroai/vscode` session | **`panel.sh` + files.** Real terminal + editor for running the panel and reading `panel/*/02-report.md`; the dsh web UI itself won't survive the `/proxy/` path (see above). |
| `astroai/notebook` session | **`panel.sh` in a JupyterLab terminal** (proven pattern across these repos). Same UI caveat as vscode. |
| `astroai/terminal` | **`panel.sh`.** This is the terminal-native experience: same brief/ledger/report, no browser needed. |
| `astroai/marimo` | No. No shell, no port proxy. |
| Plain terminal CLI | `panel.sh`. Presets are web-profile-only; no CLI flag mounts them. (A real CLI panel would need an SDK router driving 8 persona sessions — not built.) |

### Laptop runbook (for when you're back on it)

```sh
~/dsh/install.sh                  # 50 checks incl. preset discovery
[ -n "${DEEPSEEK_API_KEY:-}" ] && echo key-ok || echo KEY_MISSING -- export it first
~/dsh/dsh-web.sh /path/to/repo    # → http://127.0.0.1:3080 → New session → AstroAI Studio Team
```

Nothing here is machine-specific: the skill root is `!!js process.env.HOME +
'/dsh/skills'`, the preset root `~/dsh/presets` expands per user. Needs
`DEEPSEEK_API_KEY` (or another provider on the Models page) and dsh 0.1.5-rc.1
(`npx -y @deepseek-ai/dsh --version`).

### vscode-session walkthrough (panel without the laptop)

```sh
canfar create contributed astroai/vscode:latest -n review
```

Open the session → VS Code in your browser → new terminal:

```sh
export DEEPSEEK_API_KEY=sk-...
~/dsh/panel.sh /scratch/src/torchregress "C1: ...; C2: ..." my-slug
```

Read the verdict in the terminal and `panel/<date>-my-slug/02-report.md` in the
editor. If you still want to try the web UI behind the proxy (expect `/api`
failures): `~/dsh/dsh-web.sh /scratch/src/torchregress`, forward 3080 in the
Ports view, open `…/proxy/3080/`.

## astroai-lab integration (canonical entrypoint)

This bench now ships inside `astroai-lab` (`astroai panel --help`): one install
(`uv pip install git+https://github.com/astroai/canfar-lab.git@main`) provisions
the preset, skill, and credentials on laptop, any contributed session, marimo,
or plain CLI. `~/dsh` remains the dev source; the pip package carries a
build-time copy (`scripts/sync-review-bench.sh`).

```sh
astroai agent setup
astroai panel run /scratch/src/torchregress "C1: ...; C2: ..." my-slug
```
