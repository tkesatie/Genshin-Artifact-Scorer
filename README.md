# Genshin Artifact Scorer

Genshin Artifact Scorer analyzes an Irminsul/GOOD artifact export against the local roster and scoring configuration, then writes an HTML dashboard with character status, domain farming priorities, and artifact upgrade recommendations.

## What It Does

- Parses equipped artifacts from a GOOD-format JSON export.
- Scores each roster character's equipped artifacts by useful substat roll count.
- Computes per-slot "Good" and "Excellent" thresholds from usage, role, slot, useful-stat pool, and character overrides.
- Flags whether a character's equipped artifacts actually satisfy their target set's 2pc/4pc bonus, independent of substat roll quality, and warns when a high roll-quality status (Finished/Luxury) is misleading because the set bonus isn't active.
- Evaluates under-leveled artifacts as upgrade candidates for matching roster characters.
- Builds prioritized recommendations for artifact upgrades and swaps.
- Flags off-set flex candidates for 4pc-locked characters when a piece beats their weakest equipped slot by enough expected value.
- Classifies unequipped artifacts for inventory cleanup (review / strongbox / elixir fodder).
- Aggregates character needs into domain farming scores.
- Renders the final report to a sortable standalone HTML dashboard.

## Requirements

- Python 3.8 or newer.
- `PyYAML`, used by `config.py` to read `roster.yaml`, `rules.yaml`, and `roll_values.yaml`.

Install the only external package:

```bash
python -m pip install PyYAML
```

There is no `requirements.txt` in this repository.

## Required Files

These files must be in the project root:

- `score.py`: command-line entry point.
- `artifact_utils.py`: artifact parsing, stat labels, roll counting, and main-stat validation helpers.
- `bench.py`: upgrade-potential calculations for under-leveled artifacts.
- `character_scoring.py`: character and domain scoring.
- `config.py`: YAML configuration loader.
- `flex.py`: off-set flex-slot suggestions for 4pc-locked characters.
- `inventory.py`: classification of unequipped artifacts for inventory cleanup.
- `recommendations.py`: recommendation ranking.
- `render_html.py`: HTML report writer.
- `thresholds.py`: threshold calculation helpers.
- `roster.yaml`: character usage, role, desired set, farming domain, useful stats, and valid main stats.
- `rules.yaml`: base thresholds, stat-pool adjustments, slot adjustments, character overrides, and domain scoring weights.
- `roll_values.yaml`: average substat roll values for 5-star and 4-star artifacts.

Other files currently present:

- `sample_export.json`: sample GOOD export for local testing.
- `genshin_data.json`: local data file; it is not read by the current scoring pipeline.
- `dashboard.html`: generated report output.
- `test.py`: placeholder script that currently prints `Hello World`.

## Usage

Run the scorer from the project root:

```bash
python score.py <good_export.json> [--out dashboard.html]
```

Arguments:

- `<good_export.json>`: path to an Irminsul/GOOD JSON export.
- `--out`: optional output path. Defaults to `dashboard.html`.

Example:

```bash
python score.py sample_export.json --out dashboard.html
```

Open the generated HTML file in a browser after the command completes.

## Configuration

### `roster.yaml`

Defines each character included in scoring. The current code expects fields such as:

- `usage`: usually `Active` or `IT Only`.
- `role`: one of the role keys used in `rules.yaml`, such as `DPS`, `Sub-DPS`, or `Support`.
- `set`: short artifact-set label used for bench matching through `bench.SET_ALIASES`.
- `domain`: farming domain shown in the dashboard and domain priority table.
- `useful_stats`: display labels such as `CR`, `CD`, `ATK%`, `ER`, or `EM`.
- `main_stats`: allowed main-stat labels per slot, matched against `artifact_utils.STAT_LABEL` values (e.g. `PyroDMG%`, not `Pyro%`). Use the literal string `ANY` for a slot to accept any main stat (e.g. Gorou/Faruzan's Goblet, where the set doesn't require a specific one).

Update this file as your roster, builds, or farming targets change.

**YAML gotcha:** PyYAML's `safe_load` treats bare `NO`/`No`/`no` (and `YES`/`ON`/`OFF`, etc.) as booleans, not strings. If a `set` value would read as one of those words - most notably Noblesse Oblige's short label `NO` - it must be quoted (`set: "NO"`) or it silently becomes Python `False` and that character stops matching their own set in bench/inventory/flex logic. Same applies to any other config field that might collide with a YAML boolean/null keyword.

**2pc/2pc split-set builds are not currently resolvable.** The `set` field is designed to hold a single short label (`"NO"`, `"VV"`, etc.) that gets resolved to a real GOOD `setKey` through `bench.SET_ALIASES`. Writing a two-set build directly as text - e.g. `set: "2pc/2pc"`, as `Nilou` currently does in this file - does not work as a real match: no exported artifact's `setKey` will ever equal that string, so `bench.matched_characters_for_set` returns no matches for the character. In practice this means:

- `bench.py` never evaluates any bench artifact for that character, for either set - no upgrade candidates will ever surface for them.
- `flex.py` already special-cases this: `is_four_piece_locked` checks for `"/"` in the `set` field and skips flex consideration entirely for these characters, so they're at least not given bad off-set suggestions.
- `character_scoring.compute_set_status` (see Module Reference below) also detects the `"/"` and reports `"Split (unverified)"` rather than a false result, so the dashboard's Set Bonus badge won't lie to you, it just can't check.

Until real split-set support is added, a `set` field with `"/"` is honestly reported as unverified everywhere, but gets zero bench-upgrade evaluation. If you want *some* bench coverage in the meantime, the practical workaround is to set `set` to whichever of the two half-sets you're most actively trying to complete or upgrade (e.g. `set: "Ocean-Hued Clam"` instead of a literal split label) - you'll lose bench matching for the other half, but at least get real candidates for the one you listed. Revert it once genuine 2pc/2pc support exists.

### `rules.yaml`

Controls scoring thresholds and farming priority:

- `base_thresholds`: Good/Excellent roll thresholds and Finished/Luxury piece counts by `usage|role`.
- `stat_pool_adjustment`: approximate-match adjustments based on effective useful-stat pool size.
- `slot_adjustment`: per-slot threshold adjustments.
- `character_overrides`: optional per-character threshold overrides.
- `domain_scoring`: weights used by `score_domains`.
- `flex_rules.min_ev_gain`: minimum EV gain required for `flex.find_flex_candidates` to surface an off-set flex suggestion.

### `roll_values.yaml`

Maps GOOD stat keys to average roll values. `roll_count_for_artifact` divides each useful substat value by the matching average roll value and rounds each substat to the nearest whole roll before summing.

## Module Reference

### `score.py`

Command-line orchestrator. It loads configs, reads the GOOD export, groups equipped artifacts, evaluates bench potential, scores characters/domains, builds recommendations, and calls `render_html`.

Public entry point:

- `main()`

### `artifact_utils.py`

Shared artifact helpers.

Important constants and functions:

- `SLOT_MAP`: maps GOOD slot keys to display slot names.
- `STAT_LABEL`: maps GOOD stat keys to labels used by config files.
- `MAX_LEVEL`: maximum artifact level by rarity.
- `all_substats(artifact)`: returns activated and unactivated substats together.
- `roll_count_for_artifact(artifact, useful_stats, roll_values, rarity)`: estimates useful roll count from activated substats only.
- `effective_useful_pool(main_stat_key, useful_stats)`: subtracts the main stat from the useful-stat pool when applicable.
- `valid_main_stat(artifact, cfg, slot)`: checks a slot's main stat against the character config.
- `parse_good_export(good_json, roster)`: groups equipped artifacts by character and slot. Unequipped artifacts are not returned here.

### `thresholds.py`

Threshold calculation helpers used by both character scoring and bench evaluation.

Functions:

- `stat_pool_adjustment(rules, effective_pool)`: chooses the highest configured stat-count bucket less than or equal to the effective pool.
- `compute_thresholds(rules, usage, role, slot, effective_pool, char_name)`: returns `(good, excellent)` after applying base thresholds, pool adjustment, slot adjustment, and character override.

### `bench.py`

Evaluates under-leveled artifacts against roster characters that want the artifact set. Unequipped artifacts can be evaluated for any matching character. Equipped artifacts are evaluated only for the character currently holding them.

Important constants and functions:

- `SET_ALIASES`: maps roster set labels to GOOD `setKey` values. Add or correct aliases here if your exporter uses different set keys.
- `matched_characters_for_set(set_key, roster)`: finds roster characters whose configured set matches a GOOD set key.
- `max_possible_useful_rolls(artifact, useful_stats, roll_values)`: returns current useful rolls and optimistic ceiling.
- `expected_useful_rolls(artifact, useful_stats, roll_values)`: returns current useful rolls and expected useful rolls after remaining upgrades.
- `find_bench_potential(good_json, roster, rules, roll_values)`: produces candidate upgrade records.
- `bench_expected_lookup(bench_results)`: returns best expected bench value by `(character, slot)`.
- `bench_candidates_lookup(bench_results)`: groups non-dead-end candidates by `(character, slot)`.

### `character_scoring.py`

Scores equipped artifacts for each roster character and aggregates domain priorities.

Functions:

- `compute_set_status(cfg, artifacts_by_slot)`: Checks the character's equipped artifacts against their configured `set`, resolved through `bench.SET_ALIASES`, and returns the active bonus (`"4pc"`, `"2pc"`, `"None"`, `"Split (unverified)"` for `/`-delimited sets, or `"N/A"` if no set is configured). This is independent of substat roll quality - a character can score "Excellent" on every slot's rolls and still show no active set bonus if the pieces are from mismatched sets.
- `score_character(char_name, cfg, artifacts_by_slot, rules, roll_values, bench_lookup, bench_candidates)`: Returns a character score report with per-slot statuses, completion count, upgrade counts, set completion status (`set_status`), a `set_bonus_mismatch` flag (true when status is Finished/Luxury but the 4pc bonus isn't actually active), and farming-priority score.
- `score_domains(char_results, rules)`: Groups character needs by domain and applies `domain_scoring` weights.

### `recommendations.py`

Ranks practical upgrade recommendations from bench results and character scores.

Functions:

- `determine_verdict(equipped_rolls, ceiling, good_thresh, exc_thresh)`: classifies impact as `Major Breakthrough`, `Patch / Fix`, `Luxury Upgrade`, `Minor Polish`, or `Dead end`.
- `build_recommendations(bench_results, char_results, top_n_per_slot=3)`: filters candidates that can beat the equipped artifact, limits recommendations per character/slot, and sorts by impact.

### `config.py`

Loads YAML configuration from the project root.

Function:

- `load_configs()`: returns `(roster, rules, roll_values)`.

### `render_html.py`

Writes the sortable HTML dashboard.

Functions:

- `format_substats_html(substats, rarity=5, level=0)`: formats substats and adds a hidden-line marker for low-level 5-star artifacts with three visible substats. This helper is currently not used by `render_html`.
- `render_html(char_results, domain_results, recommendations, out_path)`: writes the report and prints `Wrote <out_path>`. The character table includes a Set Bonus column (from `set_status`) showing the active 2pc/4pc bonus, and adds a ⚠ next to a character's status badge when `set_bonus_mismatch` is true.

## Planned / Under Consideration

Ideas discussed but not yet built, kept here so they don't get lost between sessions.

### 1. Run-to-run progress snapshot — approved

Save each run's `char_results` (status, excellent count, set bonus, score) to a small JSON file alongside the project. On the next run, diff against the saved snapshot and surface what changed since last time (e.g. "Bennett: Needs Work → Luxury, +2 excellent pieces") instead of just showing current state.

**Needs a minimum time interval between snapshots.** Without one, running the scorer repeatedly during testing/tweaking would make it look like meaningful progress happened every few minutes. Snapshot should only be overwritten/compared if enough real time has passed since the last saved snapshot (e.g. a configurable minimum, start at one day, not a fixed run-count).

### 2. Config pre-flight validation — approved

A lint pass over `roster.yaml`/`rules.yaml` run at the start of `score.py`, before scoring, that catches config issues that currently fail silently:

- Unquoted YAML booleans (the `NO` gotcha already documented above).
- `usage|role` combos with no matching entry in `base_thresholds`.
- `set` values with no coverage in `bench.SET_ALIASES`.
- Slot names referenced in `main_stats` that aren't real slot names (`Flower`/`Feather`/`Sands`/`Goblet`/`Circlet`).

Goal is to catch this class of typo/config-drift bug at startup with a clear warning, rather than discovering it later as an artifact/character that mysteriously never gets bench matches.

### 3. Lock-field-aware inventory cleanup — undecided

GOOD exports carry a `lock` field per artifact (whether it's locked in-game) that `inventory.py` currently ignores entirely - locked pieces get the same SAFE_STRONGBOX/SANCTIFY_ELIXIR treatment as everything else. Value is unclear right now since lock is barely used in practice (currently only used to protect Instructor pieces from accidental 4-star fodder use) - may not be worth building unless lock usage becomes more deliberate/widespread. Revisit if that changes.

### 4. Resin cost-aware recommendation ranking — needs rethinking

Originally proposed as a simple tiebreaker in `build_recommendations` favoring candidates with fewer `levels_needed` to reach their ceiling. Rejected as too simplistic: leveling from 0→4 (or from a level where the hidden 4th substat line unlocks) can reveal more useful information per resin spent than leveling from 16→20, since early levels have outsized chances of revealing whether a piece is worth continuing at all, while late levels are refining an already-known outcome. What's actually needed is a cost-per-information (or marginal-value) analysis, not a flat cost tiebreaker - e.g. weighting early upgrade events (especially ones that could reveal a hidden useful line) more heavily than late ones when ranking "worth leveling now" vs "worth leveling later." Needs more design thought before implementation - not a quick add.

### 5. Dashboard search/filter box — approved

Client-side text input above the character table (and possibly others) that filters rows by name/domain/status as-you-type. Small, self-contained, vanilla JS only, no backend/data changes.

## Scoring Notes

- Only equipped artifacts are used for character scores.
- Set completion (2pc/4pc) is checked independently from substat roll quality - a character's per-slot statuses (Needs Work/Good/Excellent) say nothing about whether their equipped pieces share a set, so always check the Set Bonus column/warning alongside the slot statuses, not instead of them.
- Bench/recommendation logic considers under-leveled artifacts and ignores artifacts already at their rarity's max level.
- Main-stat validation is enforced for bench candidates through each character's `main_stats` config.
- Expected rolls are probability-weighted from the currently active useful substats. Optimistic ceilings assume all remaining upgrades land on useful stats after any known hidden line is revealed.
- Domain scores increase for incomplete characters and for non-Luxury characters below their luxury excellent-piece target.