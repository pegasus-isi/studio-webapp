# Knowledge Single-Source & Deployment Profiles — Design

Status: **Draft for review**
Scope: studio-webapp (`knowledge/`, `studio-api/knowledge/`), pegasus-isi/claude-plugin-marketplace, the prospective OOD app
Goal: **exactly one authored copy** of the Pegasus AI skills/agents/reference
content, consumable by Claude Code and by every other tool Studio supports, on
both a deployment that owns `$HOME` (Studio) and one that does not (OOD).

---

## 1. Guiding constraints

> **C1 — One authored copy.** Skills and agents are authored in exactly one
> repository. Any other on-disk instance must be a symlink or a boot-time
> generated artifact, never a committed file.

> **C2 — On OOD, `$HOME` belongs to the site.** The app runs inside a home
> directory provisioned by someone else, typically on quota'd NFS, very likely
> already containing the user's own `~/.claude`. We may read it. We do not
> write to it without consent, and we never place churning state (git clones,
> caches) there at all.

C1 is the correctness constraint. C2 is the reason the current install
mechanism cannot be reused as-is, because it works by mutating `$HOME`.

## 2. The problem, measured

`studio-webapp/knowledge/` and the `pegasus-ai` plugin in
pegasus-isi/claude-plugin-marketplace are two hand-maintained copies of the
same body of work, and they have already diverged.

Byte-identical today: `mcp/servers.json` ↔ `.mcp.json`, the `kiso` skill,
`run_manual_template.sh`, `wrapper_template.{py,sh}`, and four examples.

Diverged, as differing-line counts between the studio `canonical.md` and the
plugin `SKILL.md`:

| studio skill | plugin skill | differing lines |
|---|---|---|
| convert | pegasus-convert | 125 |
| debug | pegasus-debug | 114 |
| dockerfile | pegasus-apptainer | 205 |
| help | pegasus-help | 93 |
| review | pegasus-review | 147 |
| scaffold | pegasus-scaffold | 415 |
| wrapper | pegasus-wrapper | 235 |

Only in studio: three agents (`workflow-architect`, `data-engineer`,
`pipeline-debugger`), plus the `provd` skill (see D3a — it was never duplicated,
and is now withdrawn from both repos pending validation).
Only in the plugin: 13 real workflow generators under `assets/examples/`, and
`skills/kiso/references/experiment-configuration.md`.

The consequence is already user-visible. The plugin moved to Apptainer
(marketplace commit `dbec9fa`, "Switch from Docker to Apptainer"); studio still
ships a Docker skill and Docker templates. Inside one Studio container, a
Claude Code user gets the plugin cloned live from GitHub at `main` (Apptainer)
while an opencode user gets the baked `knowledge/` tree (Docker). Same question,
two answers, in the same product.

The reference docs are not two copies of one thing and should not be treated as
such: the plugin's `references/PEGASUS.md` is a 44 KB portable Pegasus
development guide, while studio's `references/PEGASUS_AI.md` is 18.5 KB of which
the distinctive part is Studio-environment description (`## Environment`,
`## Workspace Organization`). See D5.

## 3. Root cause of the `$HOME` and cwd problems

The current adapter design conflates **content** (the knowledge tree) with
**config** (telling a tool where to find it), and implements config as
filesystem mutation: workspace-scoped files in `~/work` plus `$HOME` fallbacks.

That is why both reported problems exist, and they are the same problem:

- Workspace scope is cwd-bound, so a user who leaves `~/work` loses the skills.
- The `$HOME` writes exist *only* to compensate for that. `common.py:128-132`
  says so outright: "the workspace `~/work` is only visible when the tool is
  launched from there."

Present blast radius, 10 call sites across 2 adapters plus opencode's
`_global_config_dir()`: `~/.claude/CLAUDE.md` (managed block), `~/.claude.json`
(`mcpServers` merge), `~/.claude/settings.json`, and the `~/.antigravity/`
equivalents.

If discovery becomes a **launch-time environment concern** instead of a
file copied into whichever directory the tool started in, both problems close at
once: environment is cwd-independent by construction, and it does not need a
`$HOME` fallback to be found. Writing to `$HOME` is still fine — it is where a
per-user tool's config belongs — it just stops being the workaround.

Worth stating plainly, because it removes the original motivation: **Studio does
not require anything to live under `~/work`.** Content and config can sit
directly under `$HOME`, which Studio owns and which is cwd-independent by
construction. The workspace scoping that forced the `$HOME` fallbacks was solving
a constraint Studio does not actually have. On OOD the same code path works
because the pointers come from the environment, and only the state location
differs.

## 4. Verified mechanism (Claude Code 2.1.220)

These were confirmed by reading the shipped binary and by running the chain end
to end in a scratch directory, not from documentation.

**Marketplace source schema** is a discriminated union over
`url | github | git | npm | file | directory | skills-dir | settings`. Relevant
members:

- `{"source": "directory", "path": "..."}` — "Local directory containing
  `.claude-plugin/marketplace.json`". A read-only path works.
- `{"source": "npm", "package": ...}` — an npm package containing
  `marketplace.json`.

**Relocation env vars** (all present in the binary):

| var | effect |
|---|---|
| `CLAUDE_CODE_PLUGIN_CACHE_DIR` | relocates the mutable plugin root. `known_marketplaces.json`, `installed_plugins.json`, `marketplaces/` and `cache/` all resolve under it. Default is inside `~/.claude`. |
| `CLAUDE_CODE_PLUGIN_SEED_DIR` | `PATH`-style list of read-only seed dirs, synced at startup with `autoUpdate: false` |
| `CLAUDE_CONFIG_DIR` | relocates the whole config root, **including credentials** |
| `CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL` | no network on first run |

**Seed dir layout**, from the sync function:

```
<seedDir>/known_marketplaces.json          # {name: {source, installLocation, lastUpdated}}
<seedDir>/marketplaces/<name>/             # dir with .claude-plugin/marketplace.json
<seedDir>/marketplaces/<name>.json         # or a bare manifest
<seedDir>/cache/<mkt>/<plugin>/<version>/  # optional pre-extracted plugin payload
```

Three gotchas found the hard way, all of which cost test iterations and all of
which will bite the implementation:

1. `installLocation` and `lastUpdated` are **required** by the entry schema. An
   entry with only `source` fails `safeParse` and is skipped with a warning that
   is invisible at normal verbosity.
2. **The registration key must equal the marketplace manifest's own `name`
   field**, and this is enforced. You cannot register the tree under an alias, so
   Studio and OOD must both use `scitech`. Registering it as, say, `scitechlocal`
   fails with the misleading error *"Plugin 'pegasus-ai' not found in marketplace
   'scitechlocal'"*.
3. **Marketplaces materialize at agent startup, not in `claude plugin`
   subcommands.** A bare `claude plugin marketplace list` or `plugin install` on
   a cold cache reports nothing configured; one `claude -p` first populates the
   cache, after which the subcommands work. Any provisioning script must account
   for this ordering.

Seed-managed marketplaces are deliberately immutable to the user. The binary's
own error text: *"Marketplace 'X' is seed-managed and its content is controlled
by the seed image. To update: ask your admin to update the seed."* They are also
skipped during bulk refresh, so no network calls.

**Settings-declared marketplaces** also work, from a file passed to `--settings`
or from `<dir>/.claude/settings.json` in any working/added directory, via
`extraKnownMarketplaces` + `enabledPlugins`. `strictKnownMarketplaces` accepts a
filesystem allow-pattern (for example `^/opt/approved/`).

**End-to-end result, zero writes to `~/.claude`:**

```sh
# managed-settings.json declares extraKnownMarketplaces (source: directory)
#   + enabledPlugins {"demo@scitechtest": true}
CLAUDE_CODE_PLUGIN_CACHE_DIR=$S/pcE claude --settings $S/managed-settings.json \
    plugin install demo@scitechtest
# → installs to $S/pcE/cache/scitechtest/demo/0.0.1

CLAUDE_CODE_PLUGIN_CACHE_DIR=$S/pcE claude --settings $S/managed-settings.json \
    -p "Do you have a skill named demo-skill? YES or NO only."
# → YES
```

`installed_plugins.json`, `known_marketplaces.json`, the marketplace clone and
the extracted plugin all landed under the redirected cache dir. Nothing was
written to the real config dir.

The same route was then run against a real local clone of the marketplace with
the phase-1 content in place, confirming the inventory Claude Code actually
loads:

```
Skills (8)  kiso, pegasus-apptainer, pegasus-convert, pegasus-debug, pegasus-help,
            pegasus-review, pegasus-scaffold, pegasus-wrapper
Agents (3)  workflow-architect, data-engineer, pipeline-debugger
MCP servers (2)  pegasus-docs, kiso-docs
Always-on: ~665 tok added to every session
```

Other tools: opencode honors `XDG_CONFIG_HOME` / `XDG_DATA_HOME`. Codex uses
`CODEX_HOME` (the npm entrypoint is a JS shim; the var lives in the platform
binary — **still to confirm**).

## 5. Decisions

D1-D5, D7 and D8 are **approved**, and D1-D5 and D7 are implemented. D6 is
partially done: the content/config split landed, the `$HOME` write path has not
changed yet.

| # | Decision | Status | Consequence |
|---|---|---|---|
| D1 | **pegasus-isi/claude-plugin-marketplace is the single source of truth.** | ✅ approved | It is the public artifact with outside users, it is currently ahead (Apptainer), and Studio already reaches for it at runtime (`claude_code.py:34`). |
| D2 | **`studio-webapp/knowledge/{skills,agents}` is deleted, not synced.** | ✅ approved | Enforces C1. A CI check fails the build if either path reappears. |
| D3 | **The three agents move into the plugin.** | ✅ approved, implemented | `agents/` is a native Claude Code plugin directory, so plugin users gain them rather than carrying dead weight. |
| D3a | **`provd` ships nowhere until it is fully tested and validated.** Held out of the plugin *and* removed from studio-webapp; kept only as a local working copy. | ✅ approved, implemented | It has never been exercised end to end against a running provd daemon, and both destinations are shipping targets — the marketplace is public, and a studio-webapp merge redeploys to live user pods. Removing it is safe: no code references it, only its own two files. It re-enters under D3 once it works, with the frontmatter already worked out (unprefixed name per the `kiso` precedent, `allowed-tools` matching `pegasus-scaffold`). |
| D4 | **Distribute the one tree two ways from one repo: the git marketplace, and a `pegasus-ai-knowledge` wheel whose package data *is* that tree.** | ✅ approved, implemented | Submodules assume you control a build; OOD has no image. Both targets `pip install` a pinned version. Supersedes the submodule idea. |
| D5 | **Split the reference doc rather than dedupe it.** Plugin keeps `references/PEGASUS.md`; studio-webapp keeps a small `knowledge/overlay/STUDIO_ENV.md` describing only the container (workspace root, PVC, where runs land). | ✅ approved, implemented | The Studio environment is genuinely not portable and does not belong in a plugin someone installs on a laptop. Everything else in `PEGASUS_AI.md` is a condensed restatement and is deleted in favour of the real guide. Generated context gets *better*. |
| D6 | **Discovery comes from the environment, not from copying files into whatever directory a tool happens to start in.** Writing to `$HOME` stays: Studio owns it, and on OOD it is permitted with consent (D8). What goes away is the *workspace-scoped* copy and the `$HOME` fallback that existed only to compensate for it being cwd-bound. | partially done | This is what actually fixes the cwd problem: env pointers are cwd-independent by construction, so a user who leaves the workspace keeps their skills. The `$HOME` writes were never the problem; being the *mechanism* was. Two things still move off `$HOME` on OOD regardless: the plugin cache (git clones on quota-limited NFS) and nothing at all from `CLAUDE_CONFIG_DIR` (credentials). |
| D7 | **`metadata.json` is deleted; skill name/description come from `SKILL.md` frontmatter,** with `order:` as an optional frontmatter key. | ✅ approved, implemented | Removes 9 files and one entire class of drift (a description can no longer disagree with its own skill). |
| D8 | **On OOD, writing to `$HOME` is permitted, but ask the first time and remember the answer.** Redirect `CLAUDE_CODE_PLUGIN_CACHE_DIR` regardless of the answer. | ✅ approved | Asking once is cheap and avoids anything appearing in a site-owned home directory as a surprise. `CLAUDE_CONFIG_DIR` is still left alone even when consent is given: it holds credentials, so relocating it forces a re-login every session. The plugin cache moves either way, because it performs git clones and those do not belong on quota-limited NFS. |

## 6. Target architecture

Three tiers, kept apart on purpose.

| tier | what | Studio | OOD |
|---|---|---|---|
| **content** | the one marketplace tree, read-only | `/opt/pegasus-ai/seed/` (baked) | `$PEGASUSAI_ROOT/share/pegasus-ai/seed/` (module or venv package data) |
| **config** | env vars + a generated read-only settings file | `docker/entrypoint.sh` | OOD `before.sh` / app wrapper |
| **state** | credentials, sessions, plugin cache | `$HOME` (owned) | `$TMPDIR`/scratch; opt-in `~/.pegasus-ai` |

OOD launcher, no `$HOME` writes:

```sh
export PEGASUSAI_ROOT=$(python -c 'import pegasus_ai_knowledge as k; print(k.root())')
export PEGASUSAI_STATE=${PEGASUSAI_STATE:-$TMPDIR/pegasus-ai}

export CLAUDE_CODE_PLUGIN_CACHE_DIR=$PEGASUSAI_STATE/claude-plugins
export CLAUDE_CODE_PLUGIN_SEED_DIR=$PEGASUSAI_ROOT/share/pegasus-ai/seed
export CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1
export XDG_CONFIG_HOME=$PEGASUSAI_STATE/xdg-config     # opencode
export XDG_DATA_HOME=$PEGASUSAI_STATE/xdg-data
export CODEX_HOME=$PEGASUSAI_STATE/codex               # pending confirmation

exec claude --settings "$PEGASUSAI_ROOT/share/pegasus-ai/managed-settings.json" "$@"
```

Studio uses the same content and the same seed, with state left on the owned
`$HOME`. The difference between deployments is three env vars, not a different
code path.

### Deployment profiles

A profile object replaces the unconditional `home_path()` behaviour:

```python
STUDIO_DEPLOYMENT = "studio" | "ood"   # -> {content_root, state_dir, may_write_home}
```

Adapters ask the profile. This is the change that lets one knowledge package
serve both targets, and it is small: 10 call sites.

### Non-Claude tools and C1

The adapters currently `copy_skills_to_dir()`. Under C1 the ranked options are:

1. Point the tool at the single tree by config, where it supports a directory
   pointer. Preferred.
2. Symlink the single tree into the tool's expected layout.
3. Generate into `$PEGASUSAI_STATE` at boot — derived, disposable, never
   committed, regenerated on version change.

All three satisfy C1. What is forbidden is a second *authored*, committed copy,
which is what exists today.

## 7. What this removes

- The workspace-scoped `CLAUDE.md` / `.mcp.json` / `.opencode/` copies, whose
  only purpose was to be found in the current directory. The environment overlay
  can ride in via `--append-system-prompt "$(cat .../STUDIO_ENV.md)"` instead,
  which works from any cwd.
- With those gone, the `$HOME` writes stop being a *fallback for cwd-bound
  config* and become what they should have been: the normal place a per-user
  tool keeps its configuration. `upsert_managed_block` stays, and is still the
  right tool for not clobbering a user's own global memory.
- `studio-webapp/knowledge/` shrinks from 35 files to one overlay document.

## 8. The consent prompt

Writing to `$HOME` on OOD is allowed. The rule is to **ask the first time and
remember the answer**. Ask the honest question, though: not "may I install a
plugin", which the environment path makes unnecessary, but:

> May I keep your LLM credentials and session history at `~/.pegasus-ai`, or
> should they be discarded when this session ends?

That is a real tradeoff. Make it one-time, remembered, reversible, and wire it
to the `uninstall()` methods the adapters already implement; those are the right
bones.

Three rules hold regardless of the answer:

1. Never modify a pre-existing dotfile the user owns without saying so.
   `upsert_managed_block` is careful, but not writing there at all is safer and
   now possible.
2. Never put the plugin cache on `$HOME` on OOD, consent or not. It performs git
   clones onto quota-limited NFS.
3. Leave `CLAUDE_CONFIG_DIR` alone even with consent — it holds credentials.

## 9. Migration plan

**Phase 1 — marketplace absorbs the agents** (marketplace repo) — **done, on
branch `feat/absorb-pegasus-agents`**
The three agents moved to `agents/` with explicit `name:` frontmatter. The two
non-portable rules in `workflow-architect` and `data-engineer` mandating
`$HOME/work/` and naming JupyterLab were rewritten as environment-neutral
guidance, since per D5 they belong in the Studio overlay and are stale anyway.
Verified inventory: 8 skills, 3 agents, ~665 tok always-on.

`provd` is held back per D3a. `order:` frontmatter is deferred with D7. Q2
(Docker vs Apptainer) is still open and is content, not structure.

**Phase 1b — `provd` re-enters once validated** (marketplace repo)
Exercise it end to end against a running provd daemon, then add it as
`skills/provd/SKILL.md`. It is removed from studio-webapp in the meantime rather
than left shipping unvalidated (D3a); the text is preserved in this repo's git
history and as a local working copy.

**Phase 2 — publish the wheel** (marketplace repo) — **done, marketplace PR #5**
`pegasus-ai-knowledge`, package data = the tree, exposing `root()` /
`seed_dir()`. Tag and publish a pinned version.

**Phase 3 — studio consumes it** (studio-webapp) — **done**
The Dockerfile installs the package from a pinned git ref (`KNOWLEDGE_REF`)
rather than a version on PyPI, since the package is not published yet; the
install line was verified against the real ref. `studio-api/knowledge/layout.py`
is now the only module that knows where the tree is or what shape it has, and
resolves per call (env → installed package → legacy `/opt`) so tests and the
container can differ. Skill metadata comes from frontmatter. All six duplicated
`KNOWLEDGE_ROOT` constants and `routers/knowledge.py` go through it. The
duplicated tree is deleted and CI fails if it reappears. 240 tests pass, `tsc`
clean.

One behaviour fix fell out: the skills listing previously included
metadata-only directories, which appeared in `/api/knowledge/skills` but 404'd
on fetch. A skill is now defined by having a document.

**Phase 4 — profiles + environment config** (studio-webapp)
Introduce `STUDIO_DEPLOYMENT`, build the seed dir at image build, move config to
env vars in `entrypoint.sh`, and drop the workspace-scoped duplicates. `$HOME`
writes remain: on Studio unconditionally, on OOD behind the first-run consent in
D8. The plugin cache is redirected off `$HOME` on OOD either way.

**Phase 5 — OOD app**
Launcher script per §6. Verify against a real site home directory, ideally one
with a quota and a pre-existing `~/.claude`.

Phases 1-2 and 3-4 are separate PR pairs across two repos; each phase is
independently revertable. Note the ordering constraint: phase 3 cannot land
before phase 2 publishes.

## 10. Measured cost of the switch

Composing the overlay with the real 44 KB guide makes the generated context
**46 KB, roughly 12k tokens**, against ~18.5 KB (~5k tokens) for the old
condensed restatement. That is a real per-session cost, paid by every tool.

It buys correctness: the restatement was the thing that drifted. But the guide
does not have to be resident. The plugin already ships it at
`references/PEGASUS.md` and the skills reference it, so for Claude Code it is
duplicated into context that the skills could load on demand. Worth a follow-up:
compose overlay + appendix only, and have the context point at the guide's path
rather than inlining it. That would put the always-on cost *below* where it
started while keeping one authored copy.

## 11. Open questions

- **Q1 — user-visible rename.** Adopting plugin names turns `/scaffold` into
  `/pegasus-scaffold`. Stripping the prefix in the adapters preserves today's
  names but then Studio-on-Claude-Code and Studio-on-opencode disagree about
  what the same skill is called. Recommendation: take the rename, note it in
  release notes. Settled for `provd` itself, which stays unprefixed following the
  `kiso` precedent already in the plugin.
- **Q2 — Docker vs Apptainer. Resolved: Apptainer.** Studio's Build Image action
  built a project Dockerfile with podman and pushed it to a registry, which
  contradicted the guidance it ships: the skills build a `.sif` from a definition
  file and reference it as `file:///abs/path/X.sif` with `image_site="local"`, no
  registry involved. The builder now runs `apptainer build`, writes the `.sif`
  beside the definition, and reports the catalog snippet. podman and its rootless
  storage config are gone from the image. The `.sif` push path (ORAS) is retained
  but **unverified in this environment** — there is no Apptainer on the dev host,
  so only argv construction is covered by tests.
- **Q3 — `CLAUDE_CODE_PLUGIN_SEED_DIR` is undocumented.** Verified working here,
  but pin a minimum Claude Code version and keep the `--settings` +
  `extraKnownMarketplaces` route as the fallback, since that path is in the
  public settings schema.
- **Q4 — pre-baking the install.** `<seedDir>/cache/<mkt>/<plugin>/<version>/`
  is probed before install, so the extracted payload can ship in the image and
  make `plugin install` a no-op offline. Worth doing for OOD login nodes with no
  egress; needs one more test to confirm it short-circuits cleanly.
- **Q5 — `CODEX_HOME`.** Confirm against the platform binary, not the shim.
- **Q6 — antigravity.** No known config-dir env var. It may be the one tool that
  still requires a consented `$HOME` write, in which case it should degrade to
  "unavailable on OOD" rather than force the prompt on everyone.
