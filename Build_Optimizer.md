Genshin Artifact Scorer v2.0 – Final Implementation Specification
1. Overview & Design Philosophy
Objective: Replace the static "roll count + subjective weight" system with a full-build damage simulation engine.

Core Shifts:

Stats over rolls: We sum raw stat totals (ATK/HP/DEF, CR, CD, DMG%, EM) from Main Stats + Substats.

Damage over weight: Artifacts are ranked by their exact contribution to the character's Damage Score.

ER as a constraint: Energy Recharge is a functional gatekeeper (burst uptime), not a competitive damage stat.

Synergy over isolation: We brute-force multi-slot swaps to catch cascading upgrades (e.g., a high-CR Flower enables a Circlet swap from CR% to CDMG%).

Modern GOOD support: Explicitly consumes unactivatedSubstats (hidden pre-rolled 4th lines) during candidate pre-filtering.

2. Data Models (The Single Source of Truth)
All functions must consume these exact structures.

A. Artifact (Parsed from GOOD export)
python
Artifact = {
    "slotKey": "sands",                # flower, feather, sands, goblet, circlet
    "setKey": "EmblemOfSeveredFate",   # Raw GOOD set key
    "level": 20,                       # 0 to 20
    "rarity": 5,                       # 4 or 5
    "mainStatKey": "atk_",             # GOOD stat key for main stat
    "location": "Raiden",              # Equipped character name, or None/"" if unequipped
    "substats": [                      # ACTIVE, visible substats
        {"key": "critRate_", "value": 14.8},
        {"key": "atk_", "value": 9.9}
    ],
    "unactivatedSubstats": [           # Pre-rolled hidden 4th line (unlocked at +4)
        {"key": "critDMG_", "value": 7.8}
    ],
    # The following fields are parsed but ignored by the optimizer:
    "lock": False,
    "totalRolls": 3,                   # Handled by existing bench.py logic
}
B. CharacterStats (Result of build calculation)
python
CharacterStats = {
    # Primary Stat Breakdown (for HP/ATK/DEF scaling)
    "primary_base": float,      # Character base + Weapon base
    "primary_percent": float,   # Sum of all % bonuses (e.g., 0.80)
    "primary_flat": float,      # Sum of all flat bonuses
    "primary_total": float,     # Calculated: base * (1 + %) + flat

    # Offensive Stats
    "crit_rate": float,         # Decimal (0.65)
    "crit_damage": float,       # Decimal (1.80)
    "dmg_bonus": float,         # Decimal (0.466)
    "elemental_mastery": float, # Raw EM

    # Functional Stats
    "energy_recharge": float,   # Decimal (1.80)
}
C. DamageModifiers (For Set Bonuses / Future-proofing)
python
DamageModifiers = {
    "burst_multiplier": 1.0,
    "normal_multiplier": 1.0,
    "plunge_multiplier": 1.0,
    "skill_multiplier": 1.0,
}
D. BuildContext (Input envelope for the stat calculator)
python
BuildContext = {
    "character_config": dict,   # Raw roster.yaml entry
    "artifacts": dict,          # Map of slotKey -> Artifact (5 pieces)
    "team_context": dict,       # From rules.yaml
    "roll_values": dict,        # From roll_values.yaml
    "damage_model": str,        # "amplifying" | "transformative" | "none"
}
3. Core Functions (The Engine)
A. calculate_build_stats(context: BuildContext) -> CharacterStats
Location: New module (e.g., stats_calculator.py) or integrated into artifact_utils.py.

Logic:

Read primary_stat from character_config (ATK, HP, DEF, or EM).
Sum artifact main/subs for that stat, separating into base, %, and flat components.
Add external_flat_stat, external_dmg_bonus, and external_em from team_context.
Sum CR, CD, DMG%, EM, and ER from all artifact main/subs.
Populate and return CharacterStats.
B. calculate_damage_score(stats: CharacterStats, damage_model: str) -> float
Location: New pure function.

Logic:

python
cr = min(stats["crit_rate"], 1.0)
cd = stats["crit_damage"]
dmg = stats["dmg_bonus"]
em = stats["elemental_mastery"]

if damage_model == "amplifying":
    em_mult = 1 + (2.78 * em) / (em + 1400)
elif damage_model == "transformative":
    em_mult = 1 + (16 * em) / (em + 2000)
else:  # 'none'
    em_mult = 1.0

return stats["primary_total"] * (1 + cr * cd) * (1 + dmg) * em_mult
C. apply_set_effects(artifacts: dict) -> DamageModifiers
Location: New function.

Logic:

Examine the 5 equipped artifacts' setKeys.

If 4pc bonus active:

Emblem: burst_multiplier = 1 + 0.25 * stats.er (requires stats input).

Crimson Witch: skill_multiplier = 1.15, burst_multiplier = 1.15.

Gladiator: normal_multiplier = 1.35.

Default: return DamageModifiers() (all 1.0).

(Note: Stat-based set bonuses like 18% ATK are applied inside calculate_build_stats).

D. apply_er_gate(stats: CharacterStats, floor: float) -> float | None
Location: New function.

Logic:

If stats.er >= floor: return 1.0 (saturated, no penalty).

If stats.er < floor * 0.8: return None (Hard Reject).

Else: return 1.0 - ((floor - stats.er) / floor) (linear penalty).

4. Candidate Pre-Filter (Top-K with Diversity)
Important: The existing bench.py uses initialValue and totalRolls for projection. We respect that logic. For pre-filtering, we calculate Inherent Substat Value using raw current values.

A. inherent_value(artifact: Artifact, useful_stats: list, roll_values: dict) -> float
Logic:

Sum values from both substats and unactivatedSubstats that match useful_stats.

Divide each by its average roll value (roll_values).

Main stat is ignored. Level is ignored. Rarity is ignored.

python
val = 0.0
for sub in artifact["substats"] + artifact["unactivatedSubstats"]:
    if sub["key"] in useful_stats:
        val += sub["value"] / roll_values[sub["key"]]
return val
B. Diversity Filter (Per Slot)
Group unequipped artifacts by mainStatKey.

Find the highest inherent_value in each group.

Sort groups by this value, descending.

Force-include:

Top 2 from the highest group.

Top 1 from the 2nd highest group.

Top 1 from the 3rd highest group.

(Backfill with next-best from top group to reach exactly k=5).

Always append the currently equipped artifact to the pool.

5. Multi-Piece Combo Generator
Exact Algorithm (with duplicate skipping):

python
from itertools import combinations, product

def generate_combos(slot_pools, current_artifacts):
    slots = list(slot_pools.keys())
    for swap_size in range(1, 6):  # Swap 1 to 5 slots
        for slot_subset in combinations(slots, swap_size):
            candidate_lists = [slot_pools[s] for s in slot_subset]
            for chosen in product(*candidate_lists):
                # Skip if every chosen artifact is the current one
                all_current = all(chosen[i] == current_artifacts[slot_subset[i]] 
                                 for i in range(len(slot_subset)))
                if all_current:
                    continue
                new_set = current_artifacts.copy()
                for idx, slot in enumerate(slot_subset):
                    new_set[slot] = chosen[idx]
                yield new_set
Evaluation Loop per Combo:

Project unequipped artifacts to +20 using existing bench.expected_useful_rolls() to get their projected final stats. (Equipped artifacts remain at current level).

Build BuildContext with the projected set.

Call calculate_build_stats() → get new_stats.

Call calculate_damage_score() → get new_raw.

Call apply_set_effects() → get mods; apply to new_raw.

Call apply_er_gate() → if None, skip combo.

Calculate final_gain = ((new_raw * mods * er_penalty) / current_raw) - 1.

Synergy Detection:

Store the isolated gain for each swapped piece.

synergy_bonus = final_gain - sum(isolated_gains).

If synergy_bonus > 0.005, display as "Synergy: +X%" in the dashboard.

6. Configuration Updates
roster.yaml (Character Level)
yaml
Hu_Tao:
  usage: Active
  role: DPS
  damage_model: amplifying      # Renamed from reaction_type
  primary_stat: HP              # ATK, HP, DEF, EM
  er_minimum: 120               # Optional override
  useful_stats: [CR, CD, HP%, EM]
  main_stats: ...
rules.yaml (Global / Team Context)
yaml
er_minimum_by_role:
  DPS: 130
  Sub-DPS: 180
  Support: 200

team_context:
  Hu_Tao:
    external_flat_stat: 1500    # Bennett flat ATK
    external_dmg_bonus: 0.40    # Kazuha
    external_em: 200            # Sucrose

multi_piece_top_k: 5
er_hard_reject_threshold: 0.20
7. Implementation Roadmap
Define Data Models: Create CharacterStats, BuildContext, DamageModifiers in models.py.

Implement calculate_build_stats(): Handles dynamic ATK/HP/DEF/EM scaling.

Implement calculate_damage_score(): Pure math function.

Implement apply_set_effects() and apply_er_gate().

Rewrite bench.py evaluation loop:

Replace raw roll comparisons with the BuildContext → DamageScore flow.

Use existing expected_useful_rolls() to project unequipped artifacts to +20 before passing them to calculate_build_stats.

Implement Top-K Filter: (Inherent Value + Diversity) in a new candidate_generation.py module.

Implement Combo Generator: (itertools logic).

Update recommendations.py: Rank combos by final_gain and compute synergy.

Update render_html.py: Add "Multi-Piece Pathways" table with synergy badges.

8. Binding Rules
Hidden Substats (unactivatedSubstats): Included in the inherent_value pre-filter; handled as deterministic stats in the bench projection.

totalRolls & initialValue: Do not re-implement. The existing bench.py already handles these correctly for projection.

ER above threshold: Grants zero damage credit.

ER below hard threshold (80% of floor): Hard Reject (combo is discarded).

Candidate Generation: Completely isolated from damage scoring (only checks valid_main_stat and useful_stats).

Set Bonuses: Stat-based ones go into calculate_build_stats; effect-based ones go into DamageModifiers.

Step 6: Modify bench.py (Existing File)
Goal:
Modify bench.py so that its find_bench_potential (or the main evaluation loop) returns projected +20 Artifact objects instead of raw roll counts. This will allow the multi‑piece combo generator to use fully projected artifacts directly.

Files to read (for context):

bench.py – Understand the current flow: how it loops through unequipped artifacts, calls expected_useful_rolls or max_possible_useful_rolls, and what it returns currently.

models.py – For the Artifact structure.

artifact_utils.py – For STAT_LABEL and existing helper functions.

Files allowed to modify:

bench.py – Modify this existing file only. You may add new helper functions, but you must not change the function signatures of exported functions that are called by score.py (unless you also update score.py later, but that is Step 9). For now, keep find_bench_potential signature the same.

Implementation details:

Locate the function that currently computes roll counts (likely find_bench_potential or bench_expected_lookup).

Inside the loop where an artifact is evaluated for a character/slot:

Call the existing expected_useful_rolls(artifact, useful_stats, roll_values) to get the projected final values (this function already handles unactivatedSubstats and roll distribution).

Construct a new dict that is a copy of the original artifact, but:

Replace "substats" with the projected substats (a list of {"key": ..., "value": ...}).

Clear "unactivatedSubstats" (set to empty list).

Keep all other fields (slotKey, setKey, level set to 20, rarity, mainStatKey, location as None).

Return this projected Artifact (or store it in a lookup) instead of a roll count.

Output format: Maintain the same top‑level return structure (e.g., a dict mapping (character, slot) to something), but now the value should be the projected Artifact object.

If there is a helper function (e.g., bench_expected_lookup) that other files call, keep its return format consistent (just swap the roll count for the projected artifact).

Success criteria:

python -c "from bench import find_bench_potential; print('OK')" runs without SyntaxError.

The modified function still accepts the same inputs as before.

No new imports are required beyond models (if used).

Stop instruction:
Stop after modifying bench.py. Do not modify recommendations.py or score.py yet.

Step 7: Modify recommendations.py (Existing File)
Goal:
Add a new function find_multi_piece_upgrades to recommendations.py. This function consumes the Top‑K pools (from Step 5), the current build stats, and the ER floor, and returns a ranked list of multi‑slot upgrade pathways. Do not delete the old recommendation logic yet (it will be phased out in a later step).

Files to read (for context):

recommendations.py – Understand the existing structure and how it currently ranks single‑slot upgrades.

models.py – For CharacterStats, BuildContext, Artifact.

stats_calculator.py, damage_calculator.py, set_bonus.py – To use the new functions.

candidate_generation.py – Though the candidate pools will be passed in from score.py.

Files allowed to modify:

recommendations.py – Modify this file. You may add new imports and new functions. Do not remove or alter the existing exported functions (like build_recommendations) unless you are adding a new parameter.

Implementation details:
Write the following new function at the bottom of the file.

generate_combos(slot_pools: dict[str, list[Artifact]], current_artifacts: dict[str, Artifact]) -> Iterator[dict[str, Artifact]]:

Use itertools.combinations and itertools.product.

Iterate over swap_size from 1 to 5.

For each combination of slots, iterate over the Cartesian product of their candidate lists.

Skip the combination if every chosen artifact is the same as current_artifacts for that slot (i.e., all selected slots retain their equipped piece).

Yield the new artifact set (a dict mapping slot -> chosen artifact).

find_multi_piece_upgrades( char_name: str, char_config: dict, current_artifacts: dict[str, Artifact], top_k_pools: dict[str, list[Artifact]], current_stats: CharacterStats, current_damage: float, roll_values: dict, er_floor: float, damage_model: str, team_context: dict ) -> list[dict]:

Initialize results = [].

For each new_set yielded by generate_combos:

Build BuildContext with the new_set, char_config, team_context, roll_values, and damage_model.

Call stats_calculator.calculate_build_stats(context) → new_stats.

Call damage_calculator.calculate_damage_score(new_stats, damage_model) → new_raw.

Call set_bonus.apply_set_effects(new_set, new_stats) → mods.

Call damage_calculator.apply_er_gate(new_stats, er_floor) → er_penalty. If None, skip this combo.

Calculate final_gain = ((new_raw * mods["burst_multiplier"] * er_penalty) / current_damage) - 1.

Store a record: {"new_set": new_set, "gain": final_gain, "stats": new_stats, "mods": mods}.

Sort results by gain descending.

Return the Top N (e.g., Top 10) combos.

Success criteria:

python -c "from recommendations import find_multi_piece_upgrades; print('OK')" runs without errors.

The new function imports correctly and does not break existing code.

No SyntaxError.

Stop instruction:
Stop after modifying recommendations.py. Do not wire this into score.py yet.

Step 8: Modify character_scoring.py (Existing File)
Goal:
Modify score_character (or the main scoring loop) so that it computes and stores current_stats and current_damage for each character using the new stats_calculator and damage_calculator. This provides the baseline needed for multi‑piece comparisons.

Files to read (for context):

character_scoring.py – Understand how score_character currently works, what it returns, and how it accesses equipped artifacts.

models.py, stats_calculator.py, damage_calculator.py – For the new functions.

Files allowed to modify:

character_scoring.py – Modify this file. You may add new imports and replace the old roll‑counting logic with the new stat‑summing logic.

Implementation details:

Locate score_character or the function that iterates over equipped artifacts.

Inside it, after constructing the artifacts_by_slot dict (equipped artifacts), do the following:

Build a BuildContext with:

character_config (the character's config dict)

artifacts (the equipped artifacts)

team_context (empty or passed in from rules – for now use {})

roll_values (from config.load_configs())

damage_model (from character_config, default "none").

Call calculate_build_stats(context) → current_stats.

Call calculate_damage_score(current_stats, damage_model) → current_damage.

Add "current_stats" and "current_damage" to the result dict that score_character returns.

Keep the existing "slot_statuses" and "status" fields (Needs Work/Good/Excellent) for backward compatibility. Do not remove them yet.

Ensure that if any of the new imports fail, the function still raises a clear error.

Success criteria:

python -c "from character_scoring import score_character; print('OK')" runs without errors.

The returned character result dict now contains current_stats and current_damage.

Stop instruction:
Stop after modifying character_scoring.py. Do not modify score.py yet.

Step 9: Modify score.py (Main Orchestrator)
Goal:
Modify the main() function in score.py to integrate the new multi‑piece upgrade pathway generation. After scoring characters (Step 8), call candidate_generation.get_top_k_candidates and recommendations.find_multi_piece_upgrades for each character, then pass the results to render_html.

Files to read (for context):

score.py – Understand the current flow: loading configs, parsing GOOD export, calling character_scoring, calling render_html.

candidate_generation.py, recommendations.py, stats_calculator.py – To know the function signatures.

Files allowed to modify:

score.py – Modify this file. You may add imports and new loops. Do not delete existing logic (like the old recommendations) – we will keep both for now.

Implementation details:

After calling character_scoring.score_character for all characters (and receiving the char_results dict), loop through char_results.

For each character:

Extract the character's config from roster.

Extract the current_artifacts (equipped set) – you may need to reconstruct this from the GOOD export's equipped grouping.

Extract the current_stats and current_damage from the char_result (added in Step 8).

Get the ER floor: from character_config.get("er_minimum") or fallback to rules["er_minimum_by_role"].get(role, 180).

Get the Top‑K pools using candidate_generation.get_top_k_candidates.

Call recommendations.find_multi_piece_upgrades(...) and store the result in a dict keyed by character name (e.g., multi_piece_results[char_name] = upgrades).

Pass multi_piece_results as a new argument to render_html.render_html (you will need to modify the function signature in Step 10 – for now, just store it and pass it as an extra keyword or modify the call to include it).

Success criteria:

python score.py sample_export.json --out dashboard.html runs without crashing (it may still use old logic, but no new exceptions).

The multi‑piece results are computed and stored.

Stop instruction:
Stop after modifying score.py. Do not modify render_html.py yet (that is Step 10).

Step 10: Modify render_html.py (Existing File)
Goal:
Add a new table titled "Multi‑Piece Upgrade Pathways" inside the character detail modal. This table displays the top multi‑slot combos returned from Step 9, including the expected gain, slots swapped, and synergy bonus.

Files to read (for context):

render_html.py – Understand how the current HTML is generated, especially the character modal (JavaScript popup) and how data is passed from Python to the template.

models.py – For Artifact structures (to display set names and main stats).

Files allowed to modify:

render_html.py – Modify this file. You may add a new table rendering function. Do not delete existing tables.

Implementation details:

In render_html, accept the new multi_piece_results argument (default to {}).

In the character modal generation (the section that creates the popup HTML), after the existing "Recommended Upgrades" section, add a new section:

<h4>Multi‑Piece Upgrade Pathways</h4>

<table> with columns: Rank, Slots to Swap, Expected Gain, Set Bonus Status, ER Status, Synergy.

Iterate over multi_piece_results.get(char_name, []) and render each combo.

For synergy, if synergy > 0.005, display it as "+X.X% Synergy!" with a green badge; otherwise leave it blank.

Ensure the JavaScript for the modal (pure vanilla) can display the new table without breaking.

Success criteria:

python -c "from render_html import render_html; print('OK')" runs without errors.

When you generate a dashboard, the character modal shows the new table (even if empty).

Stop instruction:
Stop after modifying render_html.py. Do not update config.py or validate_config.py yet.

Step 11: Modify config.py & validate_config.py (Existing Files)
Goal:
Extend the configuration loading and validation to support the new YAML fields: damage_model, primary_stat, team_context, er_minimum, and er_minimum_by_role. Ensure that missing or invalid values fall back to safe defaults and emit warnings.

Files to read (for context):

config.py – Understand how load_configs() loads the three YAML files and returns them.

validate_config.py – Understand how validate_config() runs lint checks and prints issues.

roster.yaml and rules.yaml (sample) – To know the expected structure.

Files allowed to modify:

config.py – Modify this file. (Add defaults or post‑processing if needed).

validate_config.py – Modify this file. (Add new validation functions).

Implementation details:

In config.py (optional): If you want to enforce defaults immediately after loading, you can loop through roster and set damage_model = "none" if missing, primary_stat = "ATK" if missing, etc. However, this is not strictly required because stats_calculator.py already defaults to "ATK" and "none". You may leave it as‑is for now.

In validate_config.py: Add the following new checks (each as a separate function called inside validate_config):

check_damage_model(roster): For each character, if damage_model is set and not in ["amplifying", "transformative", "none"], add a WARNING. If not set, do nothing (default is "none").

check_primary_stat(roster): For each character, if primary_stat is set and not in ["ATK", "HP", "DEF", "EM"], add a WARNING. If not set, do nothing (default is "ATK").

check_er_minimum(roster, rules): If a character has er_minimum set and it is not a positive float, add an ERROR. Also, if rules.yaml does not contain er_minimum_by_role, add a WARNING (but do not fail – the system will fallback to 180).

check_team_context(rules): If team_context is present, warn if any field uses unrecognized keys (only external_flat_stat, external_dmg_bonus, external_em are valid). This is optional but helpful.

Add these new checks to the main validate_config function so they are called during score.py --validate-only.

Success criteria:

python score.py --validate-only runs and prints the new warnings/errors (or silence if all configs are valid).

No SyntaxError or ImportError.

Existing validation checks (e.g., for NO boolean coercion) still work.

Stop instruction:
Stop after modifying config.py and validate_config.py. The v2.0 engine is now fully integrated. You may now run the full pipeline and test.