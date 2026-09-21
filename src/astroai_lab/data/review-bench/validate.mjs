#!/usr/bin/env node
// Validate the review-bench configuration against the *installed* dsh plugins.
//
// Checks, in order of what actually breaks:
//   1. every row's `config` against that plugin's own Schemastery schema — catches
//      misspelled fields, wrong types, and values the loader would reject at boot;
//   2. every row `name` is resolvable from a dsh installation (typo in a package name);
//   3. the skill bundle: frontmatter has name + description, and every relative file
//      it references exists.
//
// Usage:  node validate.mjs [--node-modules <dir>] [--root <dir>]
// Exit 0 = all checks passed. Exit 1 = at least one failure, printed with the row id.
//
// A dsh installation ships every plugin package inside its own node_modules, so point
// this at one (an `npx` cache, a global install, or a profile's node_modules).

import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { join, dirname, resolve, relative } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const argv = process.argv.slice(2)
const flag = (name, fallback) => {
  const i = argv.indexOf(`--${name}`)
  return i >= 0 && argv[i + 1] !== undefined ? argv[i + 1] : fallback
}
const root = resolve(flag('root', here))
const presetFile = join(root, 'presets/review-bench/agent.cordis.yml')
const skillFile = join(root, 'skills/review-panel/SKILL.md')

/** Candidate dsh installations, first one that has the plugins wins. */
function findModules() {
  const explicit = flag('node-modules', undefined)
  if (explicit) return resolve(explicit)
  const home = process.env.DSH_HOME ?? join(process.env.HOME ?? '', '.dsh')
  const candidates = [
    join(home, 'profiles/node_modules'),
    join(here, 'node_modules'),
    join(here, '..', 'node_modules'),
  ]
  for (const dir of candidates) {
    if (existsSync(join(dir, '@deepseek-ai'))) return dir
  }
  return undefined
}

const modulesDir = findModules()
if (modulesDir === undefined) {
  console.error('no dsh installation found; pass --node-modules <dir>')
  process.exit(1)
}

import { createRequire } from 'node:module'
const requireFrom = createRequire(join(modulesDir, 'package.json'))
const jsYaml = requireFrom('js-yaml')
// dsh compositions use the loader's `!!js <expr>` extension tag, which stock
// js-yaml has no type for; accept it as an opaque value so the rest of the file
// still parses (the schema check below validates the resulting row config).
const jsTag = new jsYaml.Type('tag:yaml.org,2002:js', { kind: 'scalar', construct: (text) => ({ $js: text }) })
const yamlSchema = jsYaml.DEFAULT_SCHEMA.extend([jsTag])
const parse = (text) => jsYaml.load(text, { schema: yamlSchema })
// The real loader evaluates `!!js` before schema-checking, so validate the
// evaluated shape: an opaque `{ $js }` node stands in for whatever the
// expression returns. Substitute a neutral string — string-typed fields (like
// skill `customSkillDirs`) accept a `HOME`-derived expression, and a wrong-type
// expression still fails loudly at boot, which is the backstop that matters.
const dejs = (value) => {
  if (Array.isArray(value)) return value.map(dejs)
  if (value !== null && typeof value === 'object') {
    if (Object.keys(value).length === 1 && typeof value.$js === 'string') return '__js__'
    return Object.fromEntries(Object.entries(value).map(([key, val]) => [key, dejs(val)]))
  }
  return value
}
const failures = []
const checks = []
const ok = (what) => checks.push(what)
const fail = (what, why) => failures.push(`${what}: ${why}`)

/** Rows of a composition: top-level entries plus each group's `config:` list. */
function rows(entries) {
  const out = []
  for (const entry of entries) {
    if (entry === null || typeof entry !== 'object') continue
    out.push(entry)
    if (Array.isArray(entry.config)) out.push(...rows(entry.config))
  }
  return out
}

const entries = parse(readFileSync(presetFile, 'utf8'))
if (!Array.isArray(entries)) fail('preset', 'composition is not a top-level list of rows')

const schemaCache = new Map()
async function schemaFor(name) {
  if (schemaCache.has(name)) return schemaCache.get(name)
  let mod
  try {
    let url
    try {
      url = import.meta.resolve(name, pathToFileURL(join(modulesDir, 'package.json')).href)
    } catch {
      url = pathToFileURL(requireFrom.resolve(name)).href
    }
    mod = await import(url)
  } catch (error) {
    schemaCache.set(name, undefined)
    return undefined
  }
  schemaCache.set(name, mod)
  return mod
}

for (const row of rows(entries)) {
  const label = row.id ?? row.name ?? '(row without id)'
  if (row.disabled === true) { ok(`${label}: disabled`); continue }
  if (typeof row.name !== 'string') { fail(label, 'row has no plugin name'); continue }
  if (row.name.startsWith('cordis:')) { ok(`${label}: ${row.name}`); continue }

  const mod = await schemaFor(row.name)
  if (mod === undefined) { fail(label, `cannot resolve plugin ${row.name} from ${modulesDir}`); continue }

  const schema = mod.Config
  if (schema === undefined) { ok(`${label}: ${row.name} (no schema)`); continue }
  if (row.config === undefined) {
    try { schema({}) ; ok(`${label}: ${row.name} (defaults)`) }
    catch (error) { fail(label, `${row.name} rejects empty config: ${error.message}`) }
    continue
  }
  try {
    schema(dejs(row.config))
    ok(`${label}: ${row.name}`)
  } catch (error) {
    fail(label, `${row.name} schema: ${error.message}`)
  }
}

// Panel rows: Studio Team specialists, each with the persona/model/filter contract.
const panelNames = [
  'ask_statistician', 'ask_mathematician', 'ask_data_scientist', 'ask_ml_engineer',
  'ask_physicist', 'ask_astrophysicist', 'ask_software_engineer', 'ask_writing_editor',
  'ask_canfar_expert', 'ask_devops', 'ask_plot_master', 'ask_desloper',
  'ask_scientific_innovator', 'ask_devils_advocate',
]
const toolRows = rows(entries).filter((row) => row?.config?.toolName !== undefined)
const toolNames = toolRows.map((row) => row.config.toolName)
for (const want of panelNames) {
  if (!toolNames.includes(want)) fail('panel', `missing delegation tool ${want}`)
}
if (new Set(toolNames).size !== toolNames.length) fail('panel', 'duplicate toolName across rows')
for (const row of toolRows.filter((row) => panelNames.includes(row.config.toolName))) {
  const { provider, backgroundMode, maxDepth, persona, toolFilter, agentOptions } = row.config
  if (provider !== 'spawn') fail(row.id, `panel row must use provider spawn, got ${provider}`)
  if (backgroundMode !== 'continuable') fail(row.id, 'panel row must be continuable')
  if (maxDepth !== 1) fail(row.id, `panel row must cap depth at 1, got ${maxDepth}`)
  if (typeof persona !== 'string' || persona.length < 200) fail(row.id, 'persona missing or too thin')
  if (!toolFilter?.allow || toolFilter.allow.length === 0) fail(row.id, 'panel row needs an allow filter')
  // astroai never presets models — children inherit the session route.
  if (agentOptions?.model) {
    fail(row.id, 'panel row must not pin model (choose models in dsh Settings)')
  }
}

// Filter names must be tools the composition actually registers: `tools.restrict()` rejects
// an unknown name at child start with the known-names list, which would fail the first panel
// call. This is a text scan of each mounted plugin's build for its registered tool names —
// heuristic, but it catches the realistic failure (a typo or a tool the preset does not mount).
const resolveFromModules = (spec) => {
  try { return requireFrom.resolve(spec) } catch { return undefined }
}
const registered = new Set()
for (const row of rows(entries)) {
  if (typeof row?.name !== 'string' || row.disabled === true) continue
  const path = resolveFromModules(row.name)
  if (path === undefined) continue
  const text = readFileSync(path, 'utf8')
  for (const match of text.matchAll(/name:\s*["']([a-z][a-z0-9_]*)["']/g)) registered.add(match[1])
}
if (registered.size === 0) fail('filters', 'could not scan any registered tool names')
for (const row of toolRows.filter((row) => panelNames.includes(row.config.toolName))) {
  for (const name of row.config.toolFilter?.allow ?? []) {
    if (!registered.has(name)) fail(row.id, `allow names "${name}", which no mounted row registers`)
  }
}
ok(`filters: ${registered.size} registered tool names scanned`)

// Cross-file consistency: the protocol tables, the rubric, and the workflow template must agree
// with the preset. These are the pieces a hand-edit silently desynchronises.
const skillText = readFileSync(skillFile, 'utf8')
const rubricFile = join(root, 'skills/review-panel/references/rubric.md')
const flowFile = join(root, 'skills/review-panel/references/panel-round1.js')
const rubricText = readFileSync(rubricFile, 'utf8')
const flowText = readFileSync(flowFile, 'utf8')

// The skill's roster table is what the chair is told to dispatch by name: each tool needs a row
// with a non-empty lens and a model column.
for (const name of panelNames) {
  const row = new RegExp(`^\\|\\s*\`${name}\`\\s*\\|[^|]+\\|[^|]+\\|`, 'mu')
  if (!row.test(skillText)) fail('skill', `roster table row missing or incomplete for ${name}`)
  if (!/^ask_[a-z_]+$/u.test(name)) fail('panel', `tool name ${name} is not ask_<role>`)
}

// Every person is told to open with the alternative explanation and to run mandatory probes.
for (const row of toolRows.filter((row) => panelNames.includes(row.config.toolName))) {
  const persona = row.config.persona ?? ''
  if (!persona.includes('strongest alternative explanation')) {
    fail(row.id, 'persona omits the alternative-explanation open')
  }
  const probes = persona.match(/^\d\. [A-Z]/gmu)?.length ?? 0
  if (!persona.includes('Mandatory probes') || probes < 3) {
    fail(row.id, `persona carries ${probes} mandatory probes (need >=3)`)
  }
}
if (!readFileSync(presetFile, 'utf8').includes('INFRA_ERROR')) {
  fail('persona', 'chair persona does not define INFRA_ERROR handling')
}

// The severity vocabulary is written down three times; they must not drift.
const rubricSeverities = [...rubricText.matchAll(/^\| `(BLOCKER|MAJOR|MINOR|NOTE)` \|/gmu)]
  .map((m) => m[1])
const templateEnums = (flowText.match(/severity: \{ type: 'string', enum: (\[[^\]]*\]) \}/) ?? [])[1] ?? ''
const templateSeverities = [...templateEnums.matchAll(/'([A-Z_]+)'/gu)].map((m) => m[1])
if (templateSeverities.length === 0) fail('workflow template', 'severity enum not found')
for (const severity of rubricSeverities) {
  if (!templateSeverities.includes(severity)) fail('workflow template', `enum lacks ${severity}`)
}
if (rubricSeverities.length !== 4) fail('rubric', `expected 4 severities, found ${rubricSeverities.length}`)
if (!rubricText.includes('p_claim_true')) fail('rubric', 'finding schema lost p_claim_true')
ok(`consistency: ${panelNames.length} personas, ${rubricSeverities.length} severities, roster table in sync`)

// The workflow template is a script body the model pastes into the `workflow` tool: it must parse
// with top-level await, and its schema must stay inside the keyword subset the tool supports.
try {
  // eslint-disable-next-line no-new-func -- parse-only: the body is never executed here
  new Function('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', `return (async () => {\n${flowText}\n})`)
  ok('workflow template: script body parses with top-level await')
} catch (error) {
  fail('workflow template', `script body does not parse: ${error.message}`)
}
for (const keyword of ['pattern', 'format', 'minLength', 'maxLength', 'minimum', 'maximum']) {
  if (new RegExp(`\\b${keyword}:`, 'u').test(flowText)) {
    fail('workflow template', `schema uses unsupported keyword "${keyword}"`)
  }
}

// Skill bundle: frontmatter and referenced resources.
const fence = skillText.match(/^---\n([\s\S]*?)\n---\n/)
if (fence === null) fail('skill', 'missing YAML frontmatter')
else {
  const meta = parse(fence[1])
  if (!meta.name) fail('skill', 'frontmatter has no name')
  if (!meta.description) fail('skill', 'frontmatter has no description')
  ok(`skill: ${meta.name}`)
}
for (const ref of skillText.matchAll(/`references\/[A-Za-z0-9._-]+`/g)) {
  const path = join(dirname(skillFile), ref[0].replaceAll('`', ''))
  if (!existsSync(path)) fail('skill', `referenced file does not exist: ${ref[0]}`)
}
for (const file of readdirSync(join(root, 'skills/review-panel/references'))) {
  if (!statSync(join(root, 'skills/review-panel/references', file)).isFile()) continue
  ok(`skill resource: references/${file}`)
}

// Final gate: run the harness's OWN preset discovery over this root. This is the check the web
// picker runs — id validity, composition parse in the loader's dialect, every row resolving from
// the installation, and the display metadata — so a pass here means the preset is offered, not
// merely well-formed.
try {
  const resolved = resolveFromModules('@deepseek-ai/dsh-agent-presets')
  if (resolved === undefined) {
    fail('discovery', 'cannot resolve @deepseek-ai/dsh-agent-presets from the installation')
  } else {
    const { discoverPresets } = await import(pathToFileURL(resolved).href)
    const base = pathToFileURL(`${modulesDir}/`).href
    const presets = await discoverPresets([{ path: join(root, 'presets'), trust: 'system' }], base)
    if (!Array.isArray(presets)) fail('discovery', 'discoverPresets did not return a roster')
    else {
      const bench = presets.find((preset) => preset.id === 'review-bench')
      if (bench === undefined) fail('discovery', `roster does not offer review-bench: ${presets.map((p) => p.id).join(', ') || '(empty)'}`)
      else if (bench.broken !== undefined) fail('discovery', `harness marks review-bench broken: ${bench.broken}`)
      else if (bench.name !== 'AstroAI Studio Team') fail('discovery', `display name is ${JSON.stringify(bench.name)}, expected "AstroAI Studio Team"`)
      else ok(`discovery: harness roster offers ${bench.id} as ${JSON.stringify(bench.name)} with no broken reason`)
      for (const preset of presets) {
        if (preset.id !== 'review-bench') fail('discovery', `unexpected preset in root: ${preset.id}`)
      }
    }
  }
} catch (error) {
  fail('discovery', `harness discovery threw: ${error.message}`)
}

// Docs must point at things that exist: every `~/dsh/...` path mentioned in the how-to
// and the README is checked against the tree, so a moved file cannot leave a dead instruction.
for (const doc of ['HOWTO.md', 'README.md']) {
  const path = join(root, doc)
  if (!existsSync(path)) { fail('docs', `${doc} is missing`); continue }
  const text = readFileSync(path, 'utf8')
  const mentions = new Set([...text.matchAll(/(~\/dsh\/[A-Za-z0-9._\/-]*)/gu)].map((m) => m[1].replace(/[.,)]+$/u, '')))
  for (const mention of mentions) {
    const target = mention.startsWith('~/') ? join(process.env.HOME ?? '', mention.slice(2)) : mention
    if (!existsSync(target)) fail('docs', `${doc} points at a missing path: ${mention}`)
  }
  ok(`docs: ${doc} (${mentions.size} internal paths checked)`)
}
const howto = readFileSync(join(root, 'HOWTO.md'), 'utf8')
for (const required of ['install.sh', 'AstroAI Studio Team', 'panel/', 'DEEPSEEK_API_KEY']) {
  if (!howto.includes(required)) fail('docs', `HOWTO.md does not mention ${required}`)
}

console.log(`validated against ${relative(process.cwd(), modulesDir) || modulesDir}`)
for (const check of checks) console.log(`  ok   ${check}`)
for (const failure of failures) console.error(`  FAIL ${failure}`)
console.log(`${checks.length} checks passed, ${failures.length} failed`)
process.exit(failures.length === 0 ? 0 : 1)
