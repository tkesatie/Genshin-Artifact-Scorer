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

