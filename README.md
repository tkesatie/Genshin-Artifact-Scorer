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
- Evaluates builds through a composable pipeline engine (`pipeline.py`) — the optimizer and dashboard both route damage scoring through `run_pipeline`. Every roster character declares an explicit `evaluation_pipeline` of composable steps (e.g. `standard_damage`, `personal_damage`, `reaction_damage`, `apply_modifiers`, `em_max`, `hp_max`, `saturate`, `maximize_scaled_value`).
- Builds a budget-aware artifact leveling plan (`leveling_efficiency.py`) driven by the optimizer's build-optimality probabilities — contested slots get cheap scouting steps, resolved slots get their winning piece committed to max level, and characters the optimizer skipped fall back to a legacy threshold-based planner.
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
- `artifact_utils.py`: artifact parsing, stat labels, roll counting, main-stat validation helpers, and leveling-cost helpers.
- `bench.py`: upgrade-potential calculations for under-leveled artifacts.
- `candidate_generation.py`: candidate artifact selection helpers (projected inherent value, top-K per slot).
- `character_scoring.py`: character and domain scoring.
- `config.py`: YAML configuration loader.
- `damage_calculator.py`: re-exports the shared EM/modifier helpers from `pipeline.py` for backward compatibility.
- `flex.py`: off-set flex-slot suggestions for 4pc-locked characters.
- `inventory.py`: classification of unequipped artifacts for inventory cleanup.
- `leveling_efficiency.py`: budget-aware artifact leveling plan builder (optimizer-driven primary path + legacy threshold fallback).
- `models.py`: TypedDict definitions shared by the damage/optimizer pipeline.
- `optimizer.py`: Monte Carlo build optimizer (per-piece "Build Optimality" probabilities).
- `pipeline.py`: composable evaluation-pipeline engine (Strangler Fig migration) — registers pipeline step evaluators and runs them sequentially.
- `recommendations.py`: recommendation ranking and leveling-plan orchestration.
- `render_html.py`: HTML report writer.
- `snapshot.py`: run-to-run progress snapshot save/diff.
- `stat_targets.py`: per-character stat-target loading, current-total computation, and target classification.
- `stats_calculator.py`: aggregates a character's base + artifact stats into a `CharacterStats` block.
- `team_damage.py`: team loading and Relative Damage Index (RDI) computation.
- `thresholds.py`: threshold calculation helpers.
- `validate_config.py`: config pre-flight validation, run before scoring.
- `value_per_roll.py`: per-character per-substat damage value estimation (feeds the leveling planner's explore-vs-exploit math).
- `roster.yaml`: character usage, role, desired set, farming domain, useful stats, and valid main stats.
- `rules.yaml`: base thresholds, stat-pool adjustments, slot adjustments, character overrides, domain scoring weights, optimizer settings, budget/leveling settings, and snapshot config.
- `roll_values.yaml`: average substat roll values for 5-star and 4-star artifacts.
- `character_bases.yaml`: per-character base stats (base ATK/HP/DEF, crit, EM, ER, DMG%, innate % bonuses). Merged into the roster by `config.py`.
- `stat_targets.yaml`: opt-in per-character stat targets, plus optional per-team overrides.
- `teams.yaml`: opt-in team definitions (membership + hand-typed damage assumptions).

Other files present:

- `tests/test_damage_formulas.py`: pytest suite covering the damage-formula pipeline (`stats_calculator` + `pipeline.run_pipeline`).
- `tests/test_leveling_decisions.py`: pytest suite covering the leveling planner's decision logic (`leveling_efficiency`).
- `tests/test_optimizer_infeasible.py`: pytest suite covering the optimizer's infeasible-rate tracking.

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
- `primary_stat`: required. One of `ATK`, `HP`, `DEF`, or `EM`. `config.py` derives the `scaling` list from it when no explicit weighted `scaling` is given, and `stats_calculator.py` uses it as the fallback for `primary_total`.
- `scaling`: optional list of `{stat, weight}` entries defining how the character's primary total is computed (e.g. `[{stat: HP, weight: 1.0}]` for an HP scaler, or multiple entries for hybrid scalers). If omitted, `config.py` builds it from `primary_stat`.
- `evaluation_pipeline`: **required** list of pipeline step dicts, each with a `type` key and optional `config` (e.g. `[{type: standard_damage, config: {talent_multiplier: 1.0}}, {type: personal_damage}]`). This is the single source of truth for how the character's damage is scored — the optimizer and dashboard both route through it. See [pipeline.py](#pipelinepy) below.
- `modifiers`: optional list of kit-specific stat modifiers (e.g. Citlali's EM→flat damage, Nahida's EM→DMG%/CR). Each has `source_stat`, `target`, `coefficient`, and optional `threshold`/`cap`. Consumed by the `apply_modifiers` pipeline step.

Update this file as your roster, builds, or farming targets change.

**YAML gotcha:** PyYAML's `safe_load` treats bare `NO`/`No`/`no` (and `YES`/`ON`/`OFF`, etc.) as booleans, not strings. If a `set` value would read as one of those words - most notably Noblesse Oblige's short label `NO` - it must be quoted (`set: "NO"`) or it silently becomes Python `False` and that character stops matching their own set in bench/inventory/flex logic. Same applies to any other config field that might collide with a YAML boolean/null keyword.

**2pc/2pc split-set builds are not currently resolvable.** The `set` field is designed to hold a single short label (`"NO"`, `"VV"`, etc.) that gets resolved to a real GOOD `setKey` through `bench.SET_ALIASES`. Writing a two-set build directly as text - e.g. `set: "2pc/2pc"`, as `Nilou` currently does in this file - does not work as a real match: no exported artifact's `setKey` will ever equal that string, so `bench.matched_characters_for_set` returns no matches for the character. In practice this means:

- `bench.py` never evaluates any bench artifact for that character, for either set - no upgrade candidates will ever surface for them.
- `flex.py` already special-cases this: `is_four_piece_locked` checks for `"/"` in the `set` field and skips flex consideration entirely for these characters, so they're at least not given bad off-set suggestions.
- `character_scoring.compute_set_status` (see Module Reference below) also detects the `"/"` and reports `"Split (unverified)"` rather than a false result, so the dashboard's Set Bonus badge won't lie to you, it just can't check.
- The optimizer also skips these characters (`"/"` in the set means no target set keys to optimize against).
- The leveling planner's optimizer-driven path also skips them (no optimizer data), but the legacy threshold-based fallback still covers them, so they aren't silently dropped from the leveling plan.

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
- `lunar_base_bonus`: Lunar Reaction Base DMG Bonus (Nod-Krai Lunar Reaction system, e.g. Lunar-Crystallize). Consumed by the `reaction_damage` pipeline step when `reaction: lunar_crystallize`.

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
- `budget`: lifetime Mora/EXP budget for the leveling plan:
  - `max_mora`: total lifetime Mora budget for leveling (default `2000000`).
  - `max_artifact_exp`: total lifetime Artifact EXP budget (default `10000000`).
  - `already_spent_mora` / `already_spent_exp`: amounts already committed to other pieces, subtracted from the lifetime budget before planning.
- `leveling`: settings for the artifact leveling plan (see [leveling_efficiency.py](#leveling_efficiencypy) below):
  - `min_distinct_on_set_slots`: minimum distinct on-set slots with a piece that can reach Good/Excellent before a character is eligible for any leveling action (default `4`).
  - `require_tier_upgrade`: when `true`, an action only counts as high priority if its candidate can raise the slot's tier above what's currently equipped (Needs Work → Good/Excellent, Good → Excellent). Non-upgrading actions aren't blocked, just sink to the bottom of the priority order (default `true`).
  - `min_relevant_probability`: a candidate below this build-optimality probability is ignored entirely by the optimizer-driven planner (default `0.08`).
  - `max_contenders_per_slot`: cap on how many candidates in one slot get planned for at once (default `3`).
  - `scout_step_levels`: level increment for a "Scout" action on a contested slot (default `4`).
  - `max_scout_level`: cap on how high a Scout action can level a piece (default `16`).
  - `soft_stop_floor`: legacy planner's minimum probability for a piece to be worth jumping to a target level (default `0.15`).
  - `cliff_ratio`: legacy planner's efficiency cliff — candidates below this fraction of the best efficiency are cut (default `0.25`).
  - `max_per_character`: cap on how many distinct slots one character can claim budget on per run (default `2`).
  - `it_only_max_level`: cap for IT Only characters — analysis runs as though the piece could go to 20, but the recommended target and budget reservation are capped here (default `16`).
  - `active_chars_only`: when `true`, only Active characters get leveling recommendations; IT Only characters are excluded entirely (default `false`).
  - `tier_gated`: when `true`, the plan only suggests actions from the highest character priority tier that has any viable Scout/Commit action that run (tier 1 = Active + Farming), and defers all lower tiers until that tier is finished. This keeps Mora focused on completing one tier before moving to the next (default `true`).
  - `max_reveal_fraction`: fraction of remaining lifetime budget that can be spent on immediate (reveal/scout) actions (default `0.40`).
  - `max_pieces_per_run`: hard cap on the total number of pieces leveled in one run, across both planners combined (default `10`).

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

The `minimums` block (e.g. `energy_recharge`, `hp`) is also consumed by the optimizer as hard stat floors — a build that misses a floor is rejected during Monte Carlo simulation.

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

Command-line orchestrator. It loads configs, reads the GOOD export, groups equipped artifacts, evaluates bench potential, scores characters/domains, runs stat-target and team-damage analysis, runs the per-character optimizer, builds the leveling plan, builds recommendations, and calls `render_html`.

Functions:

- `main()`: entry point.
- `apply_imaginarium_theater_filter(roster, rules)`: see [Imaginarium Theater filter](#imaginarium-theater-filter). Called once in `main()`, after config validation (which always runs against the full, unfiltered roster) and before the roster is passed to anything else, so a possibly-narrowed roster is what every downstream module actually sees.
- `apply_single_character_filter(roster, character_name)`: narrows the roster to one character when `--char` is passed.
- `best_fit_for_artifact(artifact, roster, slot, roll_values, rules)`: evaluates an unequipped artifact against every roster character whose main-stat config allows the slot.
- `build_inventory_results(good_json, roster, rules, roll_values)`: classifies every unequipped artifact in the export.
- `build_team_context_lookup(teams)`: builds a `{character: team_context}` lookup from `teams.yaml` (first team listed wins for characters on multiple teams).
- `convert_to_cg_artifact(good_artifact_dict)`: converts a GOOD artifact dict to a `candidate_generation.Artifact`.
- `compute_expected_20_roll_value(artifact, roll_values, useful_stats)`: estimates total useful roll value at +20, used to rank optimizer candidates.

`main()` also builds the inputs the leveling planner needs:

- `char_slot_tier_lookup`: `{(character, slot): equipped tier status}` from `char_results` — drives the tier-upgrade priority rule.
- `char_score_lookup`: `{character: urgency score}` from `score_character` — prioritizes which character's actions claim budget first.
- `char_usage_lookup`: `{character: "Active"/"IT Only"}` from the roster — drives the IT Only max-level cap and Active-only toggle.
- `roll_value_by_char`: `{character: {substat_key: damage per roll}}` from `value_per_roll_for_character` — feeds the explore-vs-exploit terminal check.
- `optimizer_candidates_by_char`: per-slot candidate lists with build-optimality probabilities, annotated with `tier_upgrade_ok`/`reachable_tier` per candidate (computed via `reachable_tier_for`/`tier_upgrade_ok` from `leveling_efficiency.py`).

The leveling plan is built via `recommendations.generate_leveling_recommendations` and passed to `render_html` as `leveling_plan`.

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
- `get_leveling_cost(rarity, current_level, target_level)`: returns `{"mora": int, "exp": int}` — the cumulative Mora and Artifact EXP cost to level an artifact from `current_level` to `target_level` (both must be multiples of 4). Used by the leveling planner to price every action and budget reservation.

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

Ranks practical upgrade recommendations from bench results and character scores, and orchestrates the leveling plan.

Functions:

- `determine_verdict(equipped_rolls, ceiling, good_thresh, exc_thresh)`: classifies impact as `Major Breakthrough`, `Patch / Fix`, `Luxury Upgrade`, `Minor Polish`, or `Dead end`.
- `build_recommendations(bench_results, char_results, top_n_per_slot=3)`: filters candidates that can beat the equipped artifact, limits recommendations per character/slot, and sorts by impact.
- `build_ceiling_only_candidates(bench_results, char_results)`: surfaces "High Risk" candidates whose optimistic ceiling beats what's equipped but whose expected value doesn't clear the threshold yet.
- `generate_leveling_recommendations(bench_results, rules, roll_values, optimizer_candidates_by_char=None, char_score_lookup=None, char_slot_tier_lookup=None, roll_value_by_char=None, char_usage_lookup=None)`: the leveling-plan entry point. Reads `budget` and `leveling` sections from `rules.yaml`, calls `leveling_efficiency.build_combined_leveling_plan`, then decorates each action with display-friendly strings (`immediate_cost_str`, `finish_cost_str`, `probability_str`, `efficiency_str`, and the explore-vs-exploit diagnostics `expected_waste_str`/`scout_cost_str`/`expected_damage_gain_str` for optimizer-driven actions).

### `config.py`

Loads YAML configuration from the project root.

Function:

- `load_configs()`: returns `(roster, rules, roll_values)`. Loads `roster.yaml`, `rules.yaml`, `roll_values.yaml`, and `character_bases.yaml` (merging base stats into the roster), then injects defaults for optional fields (`primary_stat` → `"ATK"`) and builds a backward-compatible `scaling` list from `primary_stat` when a character doesn't declare an explicit weighted `scaling`.

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

As part of the Strangler Fig migration, `compute_artifact_delta` now accumulates all three primary-stat families (ATK/HP/DEF) simultaneously instead of a single `primary_stat`, and `combine_artifact_deltas` computes `primary_total` from the character's `scaling` list (falling back to `primary_stat`). It also exposes `raw_atk`/`raw_hp`/`raw_def`/`raw_em` values for pipeline steps that need the unweighted stats (e.g. saturation caps).

Functions:

- `compute_artifact_delta(artifact, primary_stat=None)`: computes one artifact's contribution to the additive stat pool. The `primary_stat` argument is accepted for backward compatibility but no longer used — all stat families are accumulated.
- `combine_artifact_deltas(deltas, character_config, team_context=None)`: sums per-artifact deltas into a `CharacterStats` block, computing `primary_total` from the `scaling` list and adding `raw_*` values.
- `calculate_build_stats(context)`: takes a `BuildContext` (character config, artifacts by slot, team context, roll values) and returns the computed `CharacterStats`.

### `damage_calculator.py`

Retains the shared EM/modifier helper functions for backward compatibility. The actual damage scoring now lives in `pipeline.py`'s registered evaluators; this module simply re-exports the helpers so existing imports keep working.

Functions:

- `get_em_bonus_amplifying(em)`: amplifying-reaction EM bonus.
- `get_em_bonus_transformative(em)`: transformative-reaction EM bonus.
- `get_transformative_base_damage(level)`: base damage for transformative reactions (level-90 approximation).
- `get_modifier_bonus(mod, stats)`: evaluates one kit-specific modifier (e.g. Citlali's EM→flat damage, Nahida's EM→DMG%/CR) against the current stat block.

### `optimizer.py`

Monte Carlo build optimizer. For each character, it builds per-slot candidate pools (in-set and off-set, plus the currently equipped piece), projects every candidate to +20 by randomly distributing remaining upgrades, enumerates all 5-piece combinations (requiring at least 4 in-set), and reports each artifact's probability of being part of the optimal build across `num_sims` simulations.

Functions:

- `compute_optimal_probabilities(char_config, in_set_pools, off_set_pools, current_artifacts, roll_values, target_set_keys, num_sims=1000, stat_floors=None, team_context=None)`: returns `{artifact_id: probability}`.
- `compute_marginal_swap_probabilities(...)`: single-slot swap probabilities (holds the other four slots fixed at equipped) — the GO-style metric, for comparison against the joint optimizer.
- `_project_artifact(artifact, roll_values)`: projects an artifact to +20 by randomly distributing remaining upgrades.

Stat floors (from `stat_targets.yaml`'s `minimums`, e.g. ER/EM floors) are enforced as hard constraints — a build that misses a floor is rejected.

Build evaluation goes through `pipeline.run_pipeline` using the character's `evaluation_pipeline` config, so custom pipeline steps apply to optimized builds too.

### `pipeline.py`

Composable evaluation-pipeline engine — the heart of the Strangler Fig migration. The optimizer no longer calls `damage_calculator` directly; instead it constructs a pipeline of registered evaluator steps and runs them sequentially, carrying an `EvaluationContext` (`stats`, `current_score`, `metadata`) between steps.

Key pieces:

- `EvaluationContext`: state container passed between steps (`stats` — a `CharacterStats` dict now including `raw_*` keys, `current_score`, `metadata` carrying `modifiers` and `character_config`).
- `register_evaluator(name)`: decorator that registers a pipeline step evaluator by name.
- `run_pipeline(pipeline_steps, stats, metadata=None)`: executes each step in order and returns the final `current_score`. Unknown step types raise a `ValueError`.

Built-in registered steps:

- `standard_damage`: base damage from `primary_total * talent_multiplier` (config default `1.0`).
- `personal_damage`: applies crit and DMG% multipliers to the current score.
- `reaction_damage`: applies amplifying (vaporize/melt), transformative, or lunar reaction damage. Config `reaction` selects the formula (e.g. `melt`, `vaporize`, `overloaded`, `lunar_crystallize`). For lunar reactions, an optional `multiplier` config (default `1.6` direct, `0.96` indirect) selects the reaction multiplier, and the lunar EM bonus uses the unique curve `6*EM/(EM+2000)`.
- `apply_modifiers`: applies kit-specific `modifiers` to `ctx.stats` (dmg_bonus, crit_damage, crit_rate, flat_damage_add) so later steps see them.
- `spread_reaction`: adds flat damage from EM (config `coefficient`, e.g. `1.0` for Spread, `0.8` for Aggravate).
- `saturate`: caps a raw stat (config `stat` + `cap`), mutating `ctx.stats` so later steps see the clamped value.
- `maximize_scaled_value`: computes the weighted sum from the character's `scaling` list using the `raw_*` stats (e.g. for Nilou-style HP buffers or Bennett-style base-ATK buffers).
- `em_max`: returns Elemental Mastery as the score (e.g. Sucrose, Citlali).
- `hp_max`: returns `primary_total` as the score (e.g. Sangonomiya Kokomi).

### `candidate_generation.py`

Candidate artifact selection helpers.

Functions:

- `projected_inherent_value(artifact, useful_stats, roll_values)`: projects an artifact to max level, then computes its inherent useful value.
- `inherent_value(artifact, useful_stats, roll_values)`: sums useful substat values divided by their roll values.
- `get_top_k_candidates(character_config, inventory_artifacts, roll_values, current_artifacts, k=5)`: returns the top-K candidates per slot (valid main stat, unequipped, scored by projected value, equipped piece always included).

### `models.py`

TypedDict definitions shared by the damage/optimizer pipeline: `Artifact`, `CharacterStats`, `DamageModifiers`, and `BuildContext`. Also defines `CharacterConfig` (the enhanced character-config dict) documenting the Strangler Fig fields (`scaling`, `evaluation_pipeline`).

### `leveling_efficiency.py`

Budget-aware artifact leveling plan builder. The primary path (`build_leveling_plan_from_optimizer`) is driven by the global optimizer's "build optimality" probabilities rather than a raw roll-count/threshold heuristic:

- **Contested slot**: two or more candidate pieces have comparable, unresolved probability of ending up the optimal piece for that slot (e.g. three pieces each ~33%). Committing real budget to any one of them risks leveling the wrong piece, so each viable contender only gets a small, cheap "scouting" step — one checkpoint's worth of levels (default +4), which reveals its next roll/hidden substat and sharpens the probability estimate for the *next* run.
- **Resolved slot**: one candidate's probability clearly separates from the rest. Its own future-roll variance is already priced into that probability by the optimizer's Monte Carlo simulation, so it's safe to commit it the rest of the way to max level in one action.

Character urgency (`character_scoring.score_character`'s `score`) prioritizes WHICH character's actions get budget first when the immediate/lifetime budgets can't cover everything; the contest/resolve logic decides WHAT to level per character and by how much.

A legacy closed-form/threshold path (`build_leveling_plan`) is kept as a fallback for characters the optimizer didn't run for (split sets, characters with no equipped pieces, etc.) so they aren't silently dropped from the plan. `build_combined_leveling_plan` merges both.

Key functions:

- `reachable_tier_for(max_rolls, good, excellent)`: applies the same tier boundaries `character_scoring.score_character` uses for the equipped piece, but to a candidate's own ceiling (max reachable useful rolls) instead of its current roll count. Returns `"Excellent"`, `"Good"`, or `"Needs Work"`.
- `tier_upgrade_ok(equipped_tier, reachable_tier)`: does a candidate with `reachable_tier` beat the slot's `equipped_tier`? (Needs Work/Missing → any real tier clears it; Good → only Excellent; Excellent → nothing beats it.)
- `any_tier_upgrade_available(optimizer_candidates_by_char, bench_results, skip_chars, char_slot_tier_lookup)`: existence check across the WHOLE roster (both planners, every character), independent of budget/Mora — used to decide whether sidegrades are eligible for selection at all this run. If even one tier-upgrading candidate exists anywhere, no sidegrade should be a selectable candidate for anyone.
- `posterior_reach_probability(artifact, useful_stats, threshold, current_rolls, target_level=None)`: exact closed-form probability that the artifact reaches `threshold` useful rolls by `target_level`, using the fact that hidden-line reveal is deterministic and remaining upgrades follow a Binomial distribution.
- `safe_target_level(artifact, useful_stats, threshold, current_rolls, soft_floor=0.15)`: level to jump to in one action, bounded below by the point at which the piece stops being a low-probability gamble. Two constraints: a hard ceiling (dead-end gate — if even the absolute best case can't reach `threshold` by max level, return the current level) and a soft floor (the exact probability of reaching `threshold` by the returned level must be at least `soft_floor`).
- `build_leveling_plan(bench_results, budget_config, leveling_config, roll_values=None, skip_chars=None, char_slot_tier_lookup=None, max_pieces=None, exclude_non_upgraders=False, char_usage_lookup=None, it_only_max_level=None)`: the legacy closed-form/threshold planner. Generates candidates in one pass, sorts by tier-upgrade status then efficiency, and greedily selects within the immediate/lifetime budget.
- `_select_contenders(candidates, min_relevant_prob, contested_margin, max_contenders)`: filters candidates below `min_relevant_probability` (dead weight, not worth planning for) and caps the list at `max_contenders`.
- `_decide_slot_action(contenders, leveling_config, char_roll_values=None)`: explore-vs-exploit, priced entirely in Mora. `expected_waste` = leader's remaining finish cost × P(leader is wrong); `scout_cost` = Mora cost of the leader's cheapest next scout checkpoint. If `expected_waste > scout_cost`, scouting is the cheaper move (Contested); otherwise Commit. In the terminal case (nothing left to scout), also surfaces a roll-value-informed estimate of what finishing the leader is expected to buy in damage terms (`expected_damage_gain`).
- `explain_slot_decision(contenders, leveling_config, char_roll_values=None)`: human-readable audit trail for a Scout/Commit decision — a verification tool only, not used by the planner itself. Calls `_decide_slot_action` with the exact same arguments the planner would.
- `plan_slot_actions(char_name, slot, candidates, leveling_config, char_roll_values=None, effective_max_level=None)`: builds the per-slot action list (Scout or Commit) for one character/slot from its sorted, floor-filtered candidate list.
- `build_leveling_plan_from_optimizer(optimizer_candidates_by_char, char_score_lookup, budget_config, leveling_config, skip_chars=None, max_pieces=None, exclude_non_upgraders=False, roll_value_by_char=None, char_usage_lookup=None, it_only_max_level=None)`: the primary optimizer-driven planner. Groups actions into atomic per-(character, slot) decisions (a contested slot's contenders are one decision — scout everyone still live for it, or none of them), sorts groups by tier-upgrade status then character urgency then priority, and greedily selects within budget. Lifetime-budget reservation is per-slot (max finish cost in a contested group, not the sum), guaranteeing "if you commit to what's currently being leveled and it pans out, you can still finish it."
- `build_combined_leveling_plan(bench_results, optimizer_candidates_by_char, char_score_lookup, budget_config, leveling_config, char_slot_tier_lookup=None, roll_value_by_char=None, char_usage_lookup=None)`: primary entry point. Runs the optimizer-driven planner for every character the global optimizer produced candidates for, and falls back to the legacy closed-form planner for the rest. Enforces the general coverage gate (`min_distinct_on_set_slots` distinct on-set slots with a piece that can reach Good/Excellent), the global sidegrade hard-exclude (`any_tier_upgrade_available`), and the global piece cap (`max_pieces_per_run`). Merges both action lists into one budget-selected plan with a unified schema.

The unified action schema (shared by both planners):

- `character`, `slot`, `artifact_id`, `artifact`
- `action_type`: `"Scout"` (contested slot, cheap info-gathering step), `"Commit"` (resolved slot, winning piece to max level), or `"Legacy"` (no optimizer data, threshold-based fallback)
- `slot_status`: `"Contested"` or `"Resolved"` (optimizer path only)
- `probability`: build-optimality probability (optimizer path) or `None` (legacy)
- `is_equipped`, `current_level`, `target_level`, `rarity`
- `tier_upgrade_ok`, `reachable_tier`, `group_tier_upgrade_ok`
- `immediate_cost` / `finish_cost`: `{"mora": int, "exp": int}` from `artifact_utils.get_leveling_cost`
- `character_score`: the character's urgency score
- `priority`: `max(probability, 0.01) × urgency / mora_cost` (optimizer path)
- Explore-vs-exploit diagnostics (optimizer path): `expected_waste_mora`, `scout_cost_mora`, `expected_damage_gain`

The summary dict includes `total_immediate_mora`/`exp`, `calculated_immediate_budget_used_fraction`, `total_finish_cost_if_all_completed` (the guaranteed-affordable reservation), `total_finish_cost_worst_case` (FYI only — what it would cost if every scouted contender were also fully maxed), `remaining_lifetime_mora`/`exp`, `lifetime_warning`, `recommendation_text`, `scout_count`, `commit_count`, `legacy_count`, `deferred_tier_count`, `piece_count`, and `max_pieces_per_run`.

### `value_per_roll.py`

Estimates, per character, how much end-damage one average roll of each substat is actually worth right now — not a flat community "Crit Value" table, but derived from this character's own current build via the same damage pipeline `character_scoring.py` already runs. A CD roll and an ATK% roll are NOT interchangeable (a crit-capped character gets ~nothing from another CR roll no matter what a static table says), so this exists to give the leveling planner's explore-vs-exploit math a real, build-aware number instead of treating every hidden roll as equally valuable.

Method — "perturb and re-run":

1. Compute the character's CURRENT build's damage once (`base_damage`), from their actually-equipped artifacts.
2. For each substat this character cares about (`cfg["useful_stats"]`), build a tiny synthetic "artifact" containing nothing but one average roll of that substat, run it through `stats_calculator.compute_artifact_delta` (the exact same parsing real artifacts get), add it to the current build's deltas, and re-run the pipeline.
3. `value_per_roll[stat] = perturbed_damage - base_damage`.

Known approximations (acceptable for a first version, revisitable later):

- Uses the AVERAGE of a stat's 4 roll tiers from `roll_values.yaml`, not the true roll-tier distribution.
- Prices each stat independently (additive), not accounting for interaction effects between stacked stats.
- Priced off the character's CURRENTLY EQUIPPED build, not the specific candidate artifact under consideration — so a stat this character is already saturated on will correctly show low value, but a candidate meant to REPLACE a crit-heavy piece would (for now) still be evaluated against the "already capped" baseline.

Function:

- `value_per_roll_for_character(char_name, cfg, artifacts_by_slot, team_context, roll_values, useful_stats=None, rarity=5)`: returns `{substat_key: estimated damage gained from one average roll of that substat}` for this character's current equipped build. Only prices stats in `useful_stats` (defaults to `cfg["useful_stats"]`).

### `render_html.py`

Writes the sortable HTML dashboard.

Functions:

- `format_substats_html(substats, rarity=5, level=0)`: formats substats and adds a hidden-line marker for low-level 5-star artifacts with three visible substats. This helper is currently not used by `render_html`.
- `render_html(char_results, domain_results, recommendations, out_path, flex_results=None, inventory_results=None, progress_changes=None, ceiling_only_results=None, stat_target_results=None, team_damage_results=None, multi_piece_results=None, prob_lookup=None, equipped_artifacts_by_char=None, roster=None, optimizer_candidates_by_char=None, infeasible_rate_by_char=None, leveling_plan=None)`: writes the report and prints `Wrote <out_path>`.

The dashboard includes:

- **Progress Since Last Snapshot** — driven by `progress_changes` (see `snapshot.py` below).
- **Characters** table — per-slot statuses, Set Bonus column (from `set_status`), a ⚠ next to a character's status badge when `set_bonus_mismatch` is true, and a farming-priority score. Every row is clickable and opens a detail modal (pure vanilla JS, no backend calls).
- **Leveling Recommendations** — driven by `leveling_plan` (see `leveling_efficiency.py` below). Shows each action's character, slot, artifact, action type (Commit/Scout/Legacy), character score, optimality probability, level range, immediate cost, and finish cost, plus a summary box with the recommendation text, immediate spend, guaranteed-affordable reservation, and remaining lifetime budget.
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
- Filtered-out characters are excluded from character scoring, upgrade recommendations, flex recommendations, inventory matching, domain priority calculations, the leveling plan, and the run-to-run progress snapshot, for that run only. Nothing is deleted from `roster.yaml`; the next run without the filter (or with different elements) sees the full roster again.

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

### 5. Calibrated damage-per-Mora gate for the leveling planner's terminal Commit — under consideration

`leveling_efficiency._decide_slot_action` already computes `expected_damage_gain` (P(leader) × remaining hidden rolls × average priced roll value) in the terminal case (nothing left to scout), but it's informational only — not yet used to block a Commit outright because no calibrated damage-per-Mora bar exists yet to compare it against. Once `value_per_roll.py`'s estimates are trusted enough (or a per-candidate version exists), this could gate whether finishing a resolved piece is actually worth the Mora.

## Scoring Notes

- Only equipped artifacts are used for character scores.
- Set completion (2pc/4pc) is checked independently from substat roll quality - a character's per-slot statuses (Needs Work/Good/Excellent) say nothing about whether their equipped pieces share a set, so always check the Set Bonus column/warning alongside the slot statuses, not instead of them.
- Bench/recommendation logic considers under-leveled artifacts and ignores artifacts already at their rarity's max level.
- Main-stat validation is enforced for bench candidates through each character's `main_stats` config.
- Expected rolls are probability-weighted from the currently active useful substats. Optimistic ceilings assume all remaining upgrades land on useful stats after any known hidden line is revealed.
- Domain scores increase for incomplete characters and for non-Luxury characters below their luxury excellent-piece target.
- Stat targets, team damage, and the optimizer are all opt-in, independent lenses on the same equipped-artifact data. A character can be "Farming" in roll-count terms and still have a stat already over its target, or read as low RDI while doing their job as a pure support.
- The optimizer's "Build Optimality" is a probability that a piece is part of the best build found by Monte Carlo simulation for that character's current objective — intended for comparing artifact quality, not predicting in-game damage. Characters with no optimizer data (missing pieces, no bench candidates, or a split-set build) show `—`.
- The leveling plan is budget-aware: immediate spend (reveal/scout actions) is capped to a fraction of remaining lifetime currency, and each selected group's finish cost is reserved against the lifetime budget so committing to what's currently being leveled and following it through to completion is always affordable. Characters with fewer than `min_distinct_on_set_slots` distinct on-set slots of potential Good/Excellent coverage are skipped entirely, and sidegrades (pieces that can't raise their slot's tier) are only funded once nothing better exists roster-wide.