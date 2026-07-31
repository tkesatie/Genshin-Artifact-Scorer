# Genshin Artifact Scorer

Genshin Artifact Scorer analyzes an Irminsul/GOOD artifact export against the local roster and scoring configuration, then writes an HTML dashboard with character status, domain farming priorities, and artifact upgrade recommendations.

## What It Does

- Parses equipped artifacts from a GOOD-format JSON export.
- Scores each roster character's equipped artifacts by useful substat roll count.
- Computes per-slot "Good" and "Excellent" thresholds from usage, role, slot, useful-stat pool, and character overrides.
- Evaluates under-leveled artifacts as upgrade candidates for matching roster characters.
- Builds prioritized recommendations for artifact upgrades and swaps.
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
- `main_stats`: allowed main-stat labels per slot.

Update this file as your roster, builds, or farming targets change.

### `rules.yaml`

Controls scoring thresholds and farming priority:

- `base_thresholds`: Good/Excellent roll thresholds and Finished/Luxury piece counts by `usage|role`.
- `stat_pool_adjustment`: approximate-match adjustments based on effective useful-stat pool size.
- `slot_adjustment`: per-slot threshold adjustments.
- `character_overrides`: optional per-character threshold overrides.
- `domain_scoring`: weights used by `score_domains`.

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

- `score_character(char_name, cfg, artifacts_by_slot, rules, roll_values, bench_lookup, bench_candidates)`: returns a character score report with per-slot statuses, completion count, upgrade counts, and farming-priority score.
- `score_domains(char_results, rules)`: groups character needs by domain and applies `domain_scoring` weights.

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
- `render_html(char_results, domain_results, recommendations, out_path)`: writes the report and prints `Wrote <out_path>`.

## Scoring Notes

- Only equipped artifacts are used for character scores.
- Bench/recommendation logic considers under-leveled artifacts and ignores artifacts already at their rarity's max level.
- Main-stat validation is enforced for bench candidates through each character's `main_stats` config.
- Expected rolls are probability-weighted from the currently active useful substats. Optimistic ceilings assume all remaining upgrades land on useful stats after any known hidden line is revealed.
- Domain scores increase for incomplete characters and for non-Luxury characters below their luxury excellent-piece target.
