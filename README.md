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
- Optionally narrows all of the above to characters relevant to the currently active Imaginarium Theater elements.
- Tracks per-character stat targets (ER, CR, CD, EM, ATK%, HP%, DEF%) against current equipped totals, with optional per-team overrides, so you can see which stats are already over-invested and which are still worth chasing.
- Computes a Relative Damage Index (RDI) per character/team from hand-typed team assumptions — a same-character comparison tool, not a real damage number.
- Runs a Monte Carlo build optimizer per character that projects candidate artifacts to +20, simulates random upgrade rolls, and reports each piece's probability of being part of the optimal 5-piece build ("Build Optimality").
- Renders the final report to a sortable standalone HTML dashboard.

## Requirements

- Python 3.8 or newer.
- `PyYAML`, used by `config.py` to read `roster.yaml`, `rules.yaml`, `roll_values.yaml`, and `character_bases.yaml`.

Install the only required external package:

```bash
python -m pip install PyYAML
```

There is no `requirements.txt` in this repository.

`tqdm` is optional: if installed, it shows a progress bar during the per-character optimizer loop; otherwise the scorer falls back to a plain console message.

## Required Files

These files must be in the project root:

- `score.py`: command-line entry point.
- `artifact_utils.py`: artifact parsing, stat labels, roll counting, and main-stat validation helpers.
- `bench.py`: upgrade-potential calculations for under-leveled artifacts.
- `candidate_generation.py`: candidate artifact selection helpers (projected inherent value, top-K per slot).
- `character_scoring.py`: character and domain scoring.
- `config.py`: YAML configuration loader.
- `damage_calculator.py`: damage-model formulas (amplifying/transformative EM bonuses, kit-specific modifiers).
- `flex.py`: off-set flex-slot suggestions for 4pc-locked characters.
- `inventory.py`: classification of unequipped artifacts for inventory cleanup.
- `models.py`: TypedDict definitions shared by the damage/optimizer pipeline.
- `optimizer.py`: Monte Carlo build optimizer (per-piece "Build Optimality" probabilities).
- `recommendations.py`: recommendation ranking.
- `render_html.py`: HTML report writer.
- `set_bonus.py`: set-effect damage modifiers (used by the damage pipeline).
- `simulate.py`: single-slot swap Monte Carlo simulation (win rate / average gain).
- `snapshot.py`: run-to-run progress snapshot save/diff.
- `stat_targets.py`: per-character stat-target loading, current-total computation, and target classification.
- `stats_calculator.py`: aggregates a character's base + artifact stats into a `CharacterStats` block.
- `team_damage.py`: team loading and Relative Damage Index (RDI) computation.
- `thresholds.py`: threshold calculation helpers.
- `validate_config.py`: config pre-flight validation, run before scoring.
- `roster.yaml`: character usage, role, desired set, farming domain, useful stats, and valid main stats.
- `rules.yaml`: base thresholds, stat-pool adjustments, slot adjustments, character overrides, domain scoring weights, optimizer settings, and snapshot config.
- `roll_values.yaml`: average substat roll values for 5-star and 4-star artifacts.
- `character_bases.yaml`: per-character base stats (base ATK/HP/DEF, crit, EM, ER, DMG%, innate % bonuses). Merged into the roster by `config.py`.
- `stat_targets.yaml`: opt-in per-character stat targets, plus optional per-team overrides.
- `teams.yaml`: opt-in team definitions (membership + hand-typed damage assumptions).

Other files present:

- `tests/test_damage_formulas.py`: pytest suite covering the damage-formula pipeline (`stats_calculator` + `damage_calculator`).

## Usage

Run the scorer from the project root:

```bash
python score.py <good_export.json> [--out dashboard.html]
```

Arguments:

- `<good_export.json>`: path to an Irminsul/GOOD JSON export.
- `--out`: optional output path. Defaults to `dashboard.html`.
- `--char`: restrict scoring to a single roster character by name. If the name isn't in the roster, a warning is printed and all characters are scored.
- `--snapshot-path`: override the snapshot file location. Defaults to `rules.yaml`'s `snapshot.path` (`snapshot.json`).
- `--snapshot-interval-hours`: override the minimum hours required between snapshot saves. Defaults to `rules.yaml`'s `snapshot.min_interval_hours` (`24`).
- `--no-snapshot`: skip the run-to-run progress snapshot entirely for this run.
- `--skip-validation`: skip the config pre-flight validation pass entirely.
- `--validate-only`: run config pre-flight validation and exit without scoring. `<good_export.json>` is not required in this mode. Exits non-zero if any ERROR-level issue is found, so it's usable as a pre-commit/CI check on `roster.yaml`/`rules.yaml`.

Example:

```bash
python score.py sample_export.json --out dashboard.html
```

Open the generated HTML file in a browser after the command completes.

Each run first validates `roster.yaml`/`rules.yaml` — see [Config pre-flight validation](#config-pre-flight-validation) below — then saves/diffs a small `snapshot.json` progress file — see [Run-to-run progress snapshot](#run-to-run-progress-snapshot) below.

## Configuration

### `roster.yaml`

Defines each character included in scoring. The current code expects fields such as:

- `usage`: usually `Active` or `IT Only`.
- `role`: one of the role keys used in `rules.yaml`, such as `DPS`, `Sub-DPS`, or `Support`.
- `set`: short artifact-set label used for bench matching through `bench.SET_ALIASES`.
- `domain`: farming domain shown in the dashboard and domain priority table.
- `useful_stats`: display labels such as `CR`, `CD`, `ATK%`, `ER`, or `EM`.
- `main_stats`: allowed main-stat labels per slot, matched against `artifact_utils.STAT_LABEL` values (e.g. `PyroDMG%`, not `Pyro%`). Use the literal string `ANY` for a slot to accept any main stat (e.g. Gorou/Faruzan's Goblet, where the set doesn't require a specific one).
- `damage_model`: optional; defaults to `none`. One of `none`, `vaporize`, `melt`, `overloaded`, `electro_charged`, `superconduct`, `swirl`, `shatter`, or `em_max`. Drives which formula `damage_calculator.py` uses.
- `primary_stat`: optional; defaults to `ATK`. One of `ATK`, `HP`, `DEF`, or `EM`. Determines which stat `stats_calculator.py` treats as the character's primary scaling stat.
- `modifiers`: optional list of kit-specific stat modifiers (e.g. Citlali's EM→flat damage, Nahida's EM→DMG%/CR). Each has `source_stat`, `target`, `coefficient`, and optional `threshold`/`cap`. Applied by `damage_calculator.py`.

Update this file as your roster, builds, or farming targets change.

**YAML gotcha:** PyYAML's `safe_load` treats bare `NO`/`No`/`no` (and `YES`/`ON`/`OFF`, etc.) as booleans, not strings. If a `set` value would read as one of those words - most notably Noblesse Oblige's short label `NO` - it must be quoted (`set: "NO"`) or it silently becomes Python `False` and that character stops matching their own set in bench/inventory/flex logic. Same applies to any other config field that might collide with a YAML boolean/null keyword.

**2pc/2pc split-set builds are not currently resolvable.** The `set` field is designed to hold a single short label (`"NO"`, `"VV"`, etc.) that gets resolved to a real GOOD `setKey` through `bench.SET_ALIASES`. Writing a two-set build directly as text - e.g. `set: "2pc/2pc"`, as `Nilou` currently does in this file - does not work as a real match: no exported artifact's `setKey` will ever equal that string, so `bench.matched_characters_for_set` returns no matches for the character. In practice this means:

- `bench.py` never evaluates any bench artifact for that character, for either set - no upgrade candidates will ever surface for them.
- `flex.py` already special-cases this: `is_four_piece_locked` checks for `"/"` in the `set` field and skips flex consideration entirely for these characters, so they're at least not given bad off-set suggestions.
- `character_scoring.compute_set_status` (see Module Reference below) also detects the `"/"` and reports `"Split (unverified)"` rather than a false result, so the dashboard's Set Bonus badge won't lie to you, it just can't check.
- The optimizer also skips these characters (`"/"` in the set means no target set keys to optimize against).

Until real split-set support is added, a `set` field with `"/"` is honestly reported as unverified everywhere, but gets zero bench-upgrade evaluation. If you want *some* bench coverage in the meantime, the practical workaround is to set `set` to whichever of the two half-sets you're most actively trying to complete or upgrade (e.g. `set: "Ocean-Hued Clam"` instead of a literal split label) - you'll lose bench matching for the other half, but at least get real candidates for the one you listed. Revert it once genuine 2pc/2pc support exists.

### `character_bases.yaml`

Per-character base stats that `config.py` merges into the roster (roster entries win on conflicts; characters only in this file are added to the roster). Recognized keys include:

- `base_atk` / `base_hp` / `base_def`: base primary-stat value (weapon included where relevant).
- `base_atk_percent` / `base_hp_percent` / `base_def_percent`: innate % bonus on the primary stat (e.g. Zibai's 20% DEF, Furina's 20% HP).
- `base_crit_rate` / `base_crit_damage`: base crit stats (defaults 0.05 / 0.50).
- `base_em`: base Elemental Mastery.
- `base_er`: base Energy Recharge (defaults 1.0).
- `base_dmg_bonus`: base DMG bonus.
- `reaction_dmg_bonus`: extra reaction damage bonus (e.g. Mualani's Vaporize).

These feed `stats_calculator.py` and the optimizer's damage model.

### `rules.yaml`

Controls scoring thresholds and farming priority:

- `base_thresholds`: Good/Excellent roll thresholds and Finished/Luxury piece counts by `usage|role`.
- `stat_pool_adjustment`: approximate-match adjustments based on effective useful-stat pool size.
- `slot_adjustment`: per-slot threshold adjustments.
- `character_overrides`: optional per-character threshold overrides.
- `domain_scoring`: weights used by `score_domains`.
- `flex_rules.min_ev_gain`: minimum EV gain required for `flex.find_flex_candidates` to surface an off-set flex suggestion.
- `imaginarium_theater`: optional filter that narrows analysis to characters relevant to the currently active Imaginarium Theater elements. See [Imaginarium Theater filter](#imaginarium-theater-filter) below.
- `snapshot`: `path` and `min_interval_hours` for the run-to-run progress snapshot. See [Run-to-run progress snapshot](#run-to-run-progress-snapshot) below.
- `optimizer`: settings for the Monte Carlo build optimizer:
  - `num_sims`: number of Monte Carlo simulations per character (default `1000`).
  - `in_set_pool_size`: max in-set candidates considered per slot (default `5`).
  - `off_set_pool_size`: max off-set candidates considered per slot (default `5`).
  - `apply_ceiling_filter`: if `true`, skip candidates whose ceiling can't beat the currently equipped piece (default `true`).

### `roll_values.yaml`

Maps GOOD stat keys to average roll values. `roll_count_for_artifact` divides each useful substat value by the matching average roll value and rounds each substat to the nearest whole roll before summing. The optimizer's `_random_roll_value` also samples from the per-rarity roll tables here when projecting artifacts to +20.

### `stat_targets.yaml`

Opt-in, manually-configured per-character stat targets. Only characters listed here (in their own block or under a team) appear in the dashboard's Stat Targets section. Supported stats: `ER`, `CR`, `CD`, `EM`, `ATK%`, `HP%`, `DEF%` (percentages for the ratio stats, flat for EM). Absolute stats like flat HP aren't supported yet.

```yaml
Skirk:
  ER: 0
Furina:
  minimums:
    energy_recharge: 2
    hp: 40000
```

- A character's top-level block is its `Default` target set.
- `teams:` at the bottom holds per-team overrides. A team only needs to list the stats it changes; everything else falls back to the character's Default. The dashboard shows every applicable context (Default + each team that overrides) side by side, since the same character can need different things in different teams (e.g. less ER when someone else holds the field).

Current totals are main stat + activated substats, assuming every equipped piece is at max level (20 for 5-star, 16 for 4-star) — treat them as "current at full investment," not exact. Each configured stat is classified `Under Target` / `Near Target` / `Exceeds Target` (within a 3-point tolerance), and stats that are over target are flagged to deprioritize.

### `teams.yaml`

Opt-in team definitions used by the Team Damage Context section. Each team lists `members` and hand-typed `assumptions`:

- `rotation_length`: reserved for a future ER-estimation phase; not read yet.
- `dmg_bonus_pct`: aggregate teammate DMG%/ATK%-equivalent buffs stacked on the on-field character.
- `resistance_shred_pct`: aggregate RES shred estimate.
- `reaction_multiplier`: flat multiplier for reaction/amplification benefit not captured above (1.0 = no adjustment).
- `team_em`: team-provided Elemental Mastery bonus (e.g. Sucrose's EM share) applied to the on-field character's reaction damage.

These are hand-typed constants, not derived from any mechanics formula — source them from a KQM/Akasha sheet, a build guide, or your own testing, and revisit them if the comp or rotation changes.

## Module Reference

### `score.py`

Command-line orchestrator. It loads configs, reads the GOOD export, groups equipped artifacts, evaluates bench potential, scores characters/domains, runs stat-target and team-damage analysis, runs the per-character optimizer, builds recommendations, and calls `render_html`.

Functions:

- `main()`: entry point.
- `apply_imaginarium_theater_filter(roster, rules)`: see [Imaginarium Theater filter](#imaginarium-theater-filter). Called once in `main()`, after config validation (which always runs against the full, unfiltered roster) and before the roster is passed to anything else, so a possibly-narrowed roster is what every downstream module actually sees.
- `apply_single_character_filter(roster, character_name)`: narrows the roster to one character when `--char` is passed.
- `best_fit_for_artifact(artifact, roster, slot, roll_values, rules)`: evaluates an unequipped artifact against every roster character whose main-stat config allows the slot.
- `build_inventory_results(good_json, roster, rules, roll_values)`: classifies every unequipped artifact in the export.
- `build_team_context_lookup(teams)`: builds a `{character: team_context}` lookup from `teams.yaml` (first team listed wins for characters on multiple teams).
- `convert_to_cg_artifact(good_artifact_dict)`: converts a GOOD artifact dict to a `candidate_generation.Artifact`.
- `compute_expected_20_roll_value(artifact, roll_values, useful_stats)`: estimates total useful roll value at +20, used to rank optimizer candidates.

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
- `bench_potential_lookup(bench_results)`: returns best expected bench value by `(character, slot)`.
- `bench_candidates_lookup(bench_results)`: groups non-dead-end candidates by `(character, slot)`.

### `character_scoring.py`

Scores equipped artifacts for each roster character and aggregates domain priorities.

Functions:

- `compute_set_status(cfg, artifacts_by_slot)`: Checks the character's equipped artifacts against their configured `set`, resolved through `bench.SET_ALIASES`, and returns the active bonus (`"4pc"`, `"2pc"`, `"None"`, `"Split (unverified)"` for `/`-delimited sets, or `"N/A"` if no set is configured). This is independent of substat roll quality - a character can score "Excellent" on every slot's rolls and still show no active set bonus if the pieces are from mismatched sets.
- `score_character(char_name, cfg, artifacts_by_slot, rules, roll_values, bench_lookup, bench_candidates, team_context=None)`: Returns a character score report with per-slot statuses, completion count, upgrade counts, set completion status (`set_status`), a `set_bonus_mismatch` flag (true when status is Finished/Luxury but the 4pc bonus isn't actually active), and farming-priority score. `team_context` (from `teams.yaml`) is threaded through for the optimizer's build evaluation.
- `score_domains(char_results, rules)`: Groups character needs by domain and applies `domain_scoring` weights.

### `recommendations.py`

Ranks practical upgrade recommendations from bench results and character scores.

Functions:

- `determine_verdict(equipped_rolls, ceiling, good_thresh, exc_thresh)`: classifies impact as `Major Breakthrough`, `Patch / Fix`, `Luxury Upgrade`, `Minor Polish`, or `Dead end`.
- `build_recommendations(bench_results, char_results, top_n_per_slot=3)`: filters candidates that can beat the equipped artifact, limits recommendations per character/slot, and sorts by impact.
- `build_ceiling_only_candidates(bench_results, char_results)`: surfaces "High Risk" candidates whose optimistic ceiling beats what's equipped but whose expected value doesn't clear the threshold yet.

### `config.py`

Loads YAML configuration from the project root.

Function:

- `load_configs()`: returns `(roster, rules, roll_values)`. Loads `roster.yaml`, `rules.yaml`, `roll_values.yaml`, and `character_bases.yaml` (merging base stats into the roster), then injects defaults for optional fields (`damage_model` → `"none"`, `primary_stat` → `"ATK"`).

### `stat_targets.py`

Per-character stat-target analysis (Phases 1-2 of the damage-calculator roadmap).

Functions:

- `load_stat_targets()`: loads `stat_targets.yaml`, returns `{}` if missing.
- `resolve_target_contexts(char_name, all_targets)`: returns `{context_name: target_dict}` for a character — `"Default"` plus one entry per team that overrides it.
- `compute_current_stat_totals(cfg, artifacts_by_slot)`: current totals for the supported stats (ER, CR, CD, EM, ATK%, HP%, DEF%) from a character's equipped pieces.
- `score_stat_targets_for_context(char_name, cfg, artifacts_by_slot, context_name, context_targets)`: stat-target report for one character in one context.
- `score_all_stat_targets(by_char, roster, targets)`: one report per (character, context) for every roster character with configured targets.

### `team_damage.py`

Team loading and Relative Damage Index (RDI) computation.

Functions:

- `load_teams()`: loads `teams.yaml`, returns `{}` if missing.
- `primary_scaling_stat(cfg)`: `"ATK%" | "HP%" | "DEF%" | None` via a useful-stats heuristic.
- `relative_damage_index(cfg, current_totals, assumptions)`: the RDI float for one character/team combination.
- `relative_damage_change(cfg, totals_before, totals_after, assumptions)`: % RDI change between two stat totals for the same character.
- `score_all_team_damage(by_char, roster, teams)`: one RDI report per (character, team) for every team membership in `teams.yaml`.

RDI is **not** a real damage number — it's a same-character comparison tool. Crit and scaling-stat multipliers come from the character's own equipped stats; DMG/RES/reaction multipliers are hand-typed team constants. EM is intentionally excluded (its real contribution depends on which reaction formula applies). Pure supports whose value is a buff they give teammates will read as low RDI even when doing their job.

### `stats_calculator.py`

Aggregates a character's base stats (from `character_bases.yaml` via the roster) plus equipped artifact main stats and substats into a `CharacterStats` block (`primary_total`, `crit_rate`, `crit_damage`, `dmg_bonus`, `elemental_mastery`, `energy_recharge`, `reaction_dmg_bonus`, `team_em`).

Function:

- `calculate_build_stats(context)`: takes a `BuildContext` (character config, artifacts by slot, team context, roll values, damage model) and returns the computed `CharacterStats`.

### `damage_calculator.py`

Damage-model formulas used by the optimizer and simulation.

Functions:

- `get_em_bonus_amplifying(em)`: amplifying-reaction EM bonus.
- `get_em_bonus_transformative(em)`: transformative-reaction EM bonus.
- `get_transformative_base_damage(level)`: base damage for transformative reactions (level-90 approximation).
- `get_modifier_bonus(mod, stats)`: evaluates one kit-specific modifier (e.g. Citlali's EM→flat damage, Nahida's EM→DMG%/CR) against the current stat block.
- `calculate_damage_score(stats, damage_model, modifiers=None)`: computes a damage score for the given model (`none`, `vaporize`, `melt`, transformative reactions, or `em_max`), applying kit modifiers first.

### `optimizer.py`

Monte Carlo build optimizer. For each character, it builds per-slot candidate pools (in-set and off-set, plus the currently equipped piece), projects every candidate to +20 by randomly distributing remaining upgrades, enumerates all 5-piece combinations (requiring at least 4 in-set), and reports each artifact's probability of being part of the optimal build across `num_sims` simulations.

Functions:

- `compute_optimal_probabilities(char_config, in_set_pools, off_set_pools, current_artifacts, roll_values, target_set_keys, num_sims=1000, stat_floors=None, damage_model="none", team_context=None)`: returns `{artifact_id: probability}`.
- `compute_marginal_swap_probabilities(...)`: single-slot swap probabilities (holds the other four slots fixed at equipped) — the GO-style metric, for comparison against the joint optimizer.
- `_project_artifact(artifact, roll_values)`: projects an artifact to +20 by randomly distributing remaining upgrades.

Stat floors (from `stat_targets.yaml`'s `minimums`, e.g. ER/EM floors) are enforced as hard constraints — a build that misses a floor is rejected.

### `simulate.py`

Single-slot swap Monte Carlo simulation.

Functions:

- `project_artifact_random(artifact, roll_values)`: projects an artifact to max level with random upgrade distribution.
- `evaluate_artifact_swap(char_config, current_artifacts, candidate_artifact, slot, roll_values, current_stats, current_damage, num_sims=1000, damage_model="none", team_context=None)`: returns `{win_rate, avg_gain_pct}` for swapping one slot.

### `set_bonus.py`

Set-effect damage modifiers for the damage pipeline. `apply_set_effects(artifacts, stats)` counts set pieces and returns `DamageModifiers` (burst/normal/plunge/skill multipliers) for known sets (Emblem of Severed Fate, Crimson Witch of Flames, Gladiator's Finale).

### `candidate_generation.py`

Candidate artifact selection helpers.

Functions:

- `projected_inherent_value(artifact, useful_stats, roll_values)`: projects an artifact to max level, then computes its inherent useful value.
- `inherent_value(artifact, useful_stats, roll_values)`: sums useful substat values divided by their roll values.
- `get_top_k_candidates(character_config, inventory_artifacts, roll_values, current_artifacts, k=5)`: returns the top-K candidates per slot (valid main stat, unequipped, scored by projected value, equipped piece always included).

### `models.py`

TypedDict definitions shared by the damage/optimizer pipeline: `Artifact`, `CharacterStats`, `DamageModifiers`, and `BuildContext`.

### `render_html.py`

Writes the sortable HTML dashboard.

Functions:

- `format_substats_html(substats, rarity=5, level=0)`: formats substats and adds a hidden-line marker for low-level 5-star artifacts with three visible substats. This helper is currently not used by `render_html`.
- `render_html(char_results, domain_results, recommendations, out_path, flex_results=None, inventory_results=None, progress_changes=None, ceiling_only_results=None, stat_target_results=None, team_damage_results=None, multi_piece_results=None, prob_lookup=None, equipped_artifacts_by_char=None, roster=None, optimizer_candidates_by_char=None)`: writes the report and prints `Wrote <out_path>`.

The dashboard includes:

- **Progress Since Last Snapshot** — driven by `progress_changes` (see `snapshot.py` below).
- **Characters** table — per-slot statuses, Set Bonus column (from `set_status`), a ⚠ next to a character's status badge when `set_bonus_mismatch` is true, and a farming-priority score. Every row is clickable and opens a detail modal (pure vanilla JS, no backend calls).
- **Stat Targets** table — per-character/context stat targets with current/target/delta/status, flagging over-target stats to deprioritize.
- **Team Damage Context** table — per-character/team RDI with the multiplier breakdown.
- **Domains** table — sorted by farming priority.
- **Recommended Swaps** table — bench pieces that beat what's equipped, with a **Build Optimality** column (the optimizer's probability that the piece is part of the optimal build).
- **Flex Slot Suggestions** table — off-set candidates for 4pc-locked characters.
- **Inventory Cleanup** table — classified unequipped artifacts.

The character detail modal shows a five-column grid (one column per artifact slot) of the currently equipped piece plus the top candidates per slot, sorted by Build Optimality, with the equipped piece marked. When optimizer data exists for a character this is a unified single grid; otherwise it falls back to separate "Current Equipped" and "Upgrade Options" sections. Each of the tables also gets a client-side text filter box above it (see `_filter_input_html`) that hides non-matching rows as you type.

- `_filter_input_html(table_id, placeholder)`: builds the filter input + "no matching rows" message markup for one table. Actual filtering runs client-side in vanilla JS, wired up once per `.table-filter` input in the script block at the bottom of the page.

### `snapshot.py`

Saves and diffs run-to-run character progress so `score.py` can surface what's changed since the last real (non-testing) run.

Functions:

- `load_snapshot(path)`: loads a previously saved snapshot JSON file, or `None` if missing/unparseable.
- `extract_snapshot_data(char_results)`: builds the compact per-character record that gets persisted (`status`, `excellent_pieces`, `set_bonus`, `score`).
- `compute_progress(old_snapshot, char_results)`: diffs current results against a loaded snapshot into human-readable change strings, e.g. `"Bennett: Needs Work → Luxury, +2 excellent pieces, set bonus 2pc → 4pc"`. Characters not present in the old snapshot (new roster additions) are skipped rather than reported.
- `maybe_update_snapshot(path, char_results, min_interval_hours=24, now=None)`: the main entry point. Gates both the diff *and* the save on a minimum real-time interval since the last saved snapshot's timestamp — if less time than `min_interval_hours` has passed, returns `None` and leaves the on-disk file untouched, so repeated testing/tweaking runs never overwrite a "real" snapshot or manufacture fake progress. Otherwise computes the diff against the old snapshot, writes a fresh snapshot with the current timestamp, and returns the (possibly empty) list of change strings.

Configured via `rules.yaml`'s `snapshot.path` and `snapshot.min_interval_hours`, overridable per-run with `score.py`'s `--snapshot-path`, `--snapshot-interval-hours`, and `--no-snapshot` flags.

### `validate_config.py`

Pre-flight lint pass over the loaded `roster`/`rules` config, run by `score.py` before scoring.

Functions:

- `check_boolean_coercion(roster)`: flags roster string fields (`usage`, `role`, `set`, `domain`, `main_stats` keys/values) that PyYAML coerced into a `bool`/`None` — the unquoted `NO` gotcha and its relatives (`YES`, `ON`, `OFF`, etc.).
- `check_usage_role_thresholds(roster, rules)`: flags `usage|role` combos with no matching entry in `rules.base_thresholds`.
- `check_set_aliases(roster)`: flags `set` values (excluding `/`-delimited split-set labels) with no entry in `bench.SET_ALIASES`.
- `check_slot_names(roster)`: flags `main_stats` keys that aren't real slot names, checked against `artifact_utils.SLOT_MAP`'s display values.
- `validate_config(roster, rules)`: runs all four checks, returns the combined list of `ValidationIssue` records.
- `has_errors(issues)`: `True` if any issue is `ERROR`-level (as opposed to `WARNING`).

`ValidationIssue` carries `severity` (`"ERROR"` or `"WARNING"`), `character` (or `None` for config-wide issues), and `message`.

## Run-to-run progress snapshot

After each run (subject to the minimum interval below), `score.py` writes a small `snapshot.json` file recording every roster character's `status`, `excellent_pieces` count, active set bonus, and score. On the next run past the interval, it diffs the new results against that saved snapshot and prints/renders what changed, e.g.:

```
Progress since last snapshot:
  Bennett: Needs Work → Luxury, +2 excellent pieces, set bonus 2pc → 4pc
```

This also appears as a "Progress Since Last Snapshot" section at the top of the dashboard.

**Minimum time interval.** To avoid making it look like meaningful progress happened every few minutes while testing/tweaking, the snapshot is only compared *and* overwritten if at least `snapshot.min_interval_hours` (default 24, in `rules.yaml`) has passed since the last saved snapshot's timestamp. If you run the scorer again before that interval elapses, the existing `snapshot.json` is left untouched and no diff is shown or computed that run — console output notes the snapshot was skipped, and the dashboard shows a muted "not due yet" note instead of a diff.

New characters added to the roster since the last snapshot won't produce a change line the first time they appear (there's nothing to diff against yet); they'll show normal diffs on runs after that.

Use `--no-snapshot` to skip this entirely for a given run (e.g. one-off exports you don't want counted), or `--snapshot-path` / `--snapshot-interval-hours` to override the config file's defaults for that run only.

## Config pre-flight validation

Before scoring, `score.py` runs `validate_config.validate_config(roster, rules)` and prints every issue found, e.g.:

```
Config validation: 2 issue(s) found.
  [ERROR] Bennett: `set` was read as False instead of a string - likely an unquoted YAML boolean/null keyword (e.g. NO, Yes, Null) in the source file. Quote the value in roster.yaml, e.g. set: "NO".
  [WARNING] Skirk: set "Nihility (unreleased)" has no entry in bench.SET_ALIASES - bench.py will never surface upgrade candidates for this character's set.
```

Two severities:

- **ERROR** — would break scoring outright or silently corrupt a threshold lookup (unquoted-boolean coercion, a missing `usage|role` entry in `base_thresholds`). If any ERROR-level issue is found, `score.py` prints the list and exits (code 1) **before** touching the GOOD export or scoring anything, so you fix the config rather than scoring against a broken one. Override with `--skip-validation` if you need to force a run anyway.
- **WARNING** — won't crash the run, but silently reduces coverage in a way that's easy to miss (a `set` with no `bench.SET_ALIASES` entry, a typo'd slot name in `main_stats`). These are printed but don't block scoring.

Run `python score.py --validate-only` to check config without scoring — no GOOD export path required. Exits non-zero if any ERROR-level issue is present, so it doubles as a pre-commit/CI check on `roster.yaml`/`rules.yaml`.

## Imaginarium Theater filter

Optionally limits artifact analysis for `IT Only` characters based on the currently active Imaginarium Theater elements, so a run only scores/recommends for characters actually relevant to the current rotation.

Configured under `imaginarium_theater` in `rules.yaml`:

- `enabled`: turns the filter on or off. Defaults to `false`. When disabled (or the block is absent from `rules.yaml`), all roster characters are analyzed normally.
- `elements`: a list of the currently active Theater elements, e.g. `["Pyro", "Hydro", "Cryo"]`. Ignored while `enabled` is `false`.

```yaml
imaginarium_theater:
  enabled: true
  elements: [Pyro, Hydro, Cryo]
```

When enabled:

- Characters with `usage: Active` are always included, regardless of element - they're farmed independent of Theater.
- Characters with `usage: IT Only` are included only if their `element` matches one of the configured `elements`.
- Filtered-out characters are excluded from character scoring, upgrade recommendations, flex recommendations, inventory matching, domain priority calculations, and the run-to-run progress snapshot, for that run only. Nothing is deleted from `roster.yaml`; the next run without the filter (or with different elements) sees the full roster again.

Config validation (see above) always runs against the full, unfiltered roster, so an issue in an `IT Only` character's config is still caught even on a run where the Theater filter would have excluded that character from scoring.

`score.py` prints a line to the console when the filter is active, e.g.:

```
Imaginarium Theater filter active (elements=['Pyro', 'Hydro', 'Cryo']): 24/41 roster characters included.
```

## Planned / Under Consideration

Ideas discussed but not yet built, kept here so they don't get lost between sessions.

### 1. Lock-field-aware inventory cleanup — undecided

GOOD exports carry a `lock` field per artifact (whether it's locked in-game) that `inventory.py` currently ignores entirely - locked pieces get the same SAFE_STRONGBOX/SANCTIFY_ELIXIR treatment as everything else. Value is unclear right now since lock is barely used in practice (currently only used to protect Instructor pieces from accidental 4-star fodder use) - may not be worth building unless lock usage becomes more deliberate/widespread. Revisit if that changes.

### 2. Resin cost-aware recommendation ranking — needs rethinking

Originally proposed as a simple tiebreaker in `build_recommendations` favoring candidates with fewer `levels_needed` to reach their ceiling. Rejected as too simplistic: leveling from 0→4 (or from a level where the hidden 4th substat line unlocks) can reveal more useful information per resin spent than leveling from 16→20, since early levels have outsized chances of revealing whether a piece is worth continuing at all, while late levels are refining an already-known outcome. What's actually needed is a cost-per-information (or marginal-value) analysis, not a flat cost tiebreaker - e.g. weighting early upgrade events (especially ones that could reveal a hidden useful line) more heavily than late ones when ranking "worth leveling now" vs "worth leveling later." Needs more design thought before implementation - not a quick add.

### 3. Wire `relative_damage_change` into recommendations — under consideration

`team_damage.relative_damage_change` is the primitive for "how much does this artifact swap matter" but isn't yet wired into bench/recommendation candidates. Once candidate-artifact data is available to `team_damage.py`, this could surface an estimated % RDI change per recommended swap.

### 4. Validate `teams.yaml` and `stat_targets.yaml` in the pre-flight pass — under consideration

`validate_config.py` currently checks `roster.yaml`/`rules.yaml` only. Team memberships that reference characters not in the roster are silently skipped, and unsupported stat targets are surfaced in the dashboard rather than at validation time. A future pass could lint these files too.

## Scoring Notes

- Only equipped artifacts are used for character scores.
- Set completion (2pc/4pc) is checked independently from substat roll quality - a character's per-slot statuses (Needs Work/Good/Excellent) say nothing about whether their equipped pieces share a set, so always check the Set Bonus column/warning alongside the slot statuses, not instead of them.
- Bench/recommendation logic considers under-leveled artifacts and ignores artifacts already at their rarity's max level.
- Main-stat validation is enforced for bench candidates through each character's `main_stats` config.
- Expected rolls are probability-weighted from the currently active useful substats. Optimistic ceilings assume all remaining upgrades land on useful stats after any known hidden line is revealed.
- Domain scores increase for incomplete characters and for non-Luxury characters below their luxury excellent-piece target.
- Stat targets, team damage, and the optimizer are all opt-in, independent lenses on the same equipped-artifact data. A character can be "Farming" in roll-count terms and still have a stat already over its target, or read as low RDI while doing their job as a pure support.
- The optimizer's "Build Optimality" is a probability that a piece is part of the best build found by Monte Carlo simulation for that character's current objective — intended for comparing artifact quality, not predicting in-game damage. Characters with no optimizer data (missing pieces, no bench candidates, or a split-set build) show `—`.