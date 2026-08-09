"""
Module: value_per_roll

Purpose:
Estimate, per character, how much end-damage one average roll of each
substat is actually worth right now - not a flat community "Crit Value"
table, but derived from this character's own current build via the same
damage pipeline character_scoring.py already runs. A CD roll and an ATK%
roll are NOT interchangeable (a crit-capped character gets ~nothing from
another CR roll no matter what a static table says), so this exists to
give the leveling planner's explore-vs-exploit math a real, build-aware
number instead of treating every hidden roll as equally valuable.

Method - "perturb and re-run":
  1. Compute the character's CURRENT build's damage once (base_damage),
     from their actually-equipped artifacts. This is the same math
     character_scoring.score_character already does for `current_damage`
     - no new cost here.
  2. For each substat this character cares about (cfg["useful_stats"]),
     build a tiny synthetic "artifact" containing nothing but one average
     roll of that substat, run it through stats_calculator's own
     compute_artifact_delta (the exact same parsing real artifacts get -
     no separate/duplicated stat-mapping logic to drift out of sync), add
     it to the current build's deltas, and re-run the pipeline.
  3. value_per_roll[stat] = perturbed_damage - base_damage.

This is a CHEAP heuristic on purpose (see leveling_efficiency.py's
explore-vs-exploit rationale): a handful of extra pipeline calls per
character (one per useful substat), not a full re-simulation. Known
approximations, acceptable for a first version and revisitable later:
  - Uses the AVERAGE of a stat's 4 roll tiers from roll_values.yaml,
    not the true roll-tier distribution.
  - Prices each stat independently (additive), not accounting for
    interaction effects between stacked stats (e.g. two crit-adjacent
    rolls compounding differently together than the sum of them alone).
  - Priced off the character's CURRENTLY EQUIPPED build, not the specific
    candidate artifact under consideration - so a stat this character is
    already saturated on (e.g. crit-capped) will correctly show low
    value, but a candidate that's meant to REPLACE a crit-heavy piece
    would (for now) still be evaluated against the "already capped"
    baseline. Documented and deferred rather than solved now - see
    leveling_efficiency.py's explore-vs-exploit notes for the more
    accurate (and more expensive) per-candidate version.

Public API:
- value_per_roll_for_character(...): returns {substat_key: damage_delta}
  for one character's current build.
"""

from typing import Dict, Any, Optional

from stats_calculator import compute_artifact_delta, combine_artifact_deltas
from pipeline import run_pipeline

# The GOOD substat keys roll_values.yaml carries tables for. Flat HP/ATK/DEF
# are included for completeness (roll_values.yaml has them "for future-
# proofing") but are excluded from the default set below since nothing in
# the scoring pipeline currently treats them as useful substats.
DEFAULT_SUBSTAT_KEYS = (
    "hp_", "atk_", "def_", "enerRech_", "eleMas", "critRate_", "critDMG_",
)


def _average_roll_value(roll_values: Dict[str, Any], stat_key: str, rarity: int) -> float:
    """Average of a stat's roll-tier values from roll_values.yaml (the same
    table roll_count_for_artifact uses), for the given rarity."""
    tier_key = "five_star" if rarity == 5 else "four_star"
    tiers = roll_values.get(tier_key, {}).get(stat_key)
    if not tiers:
        return 0.0
    return sum(tiers) / len(tiers)


def value_per_roll_for_character(
    char_name: str,
    cfg: Dict[str, Any],
    artifacts_by_slot: Dict[str, Any],
    team_context: Dict[str, Any],
    roll_values: Dict[str, Any],
    useful_stats: Optional[list] = None,
    rarity: int = 5,
) -> Dict[str, float]:
    """
    Returns {substat_key: estimated damage gained from one average roll of
    that substat}, for this character's CURRENT equipped build. Only prices
    stats in `useful_stats` (defaults to cfg["useful_stats"], falling back
    to DEFAULT_SUBSTAT_KEYS if that's empty/missing) - no point pricing a
    roll on a stat this character's scoring already treats as dead weight.
    """
    useful_stats = useful_stats or [str(s) for s in cfg.get("useful_stats", [])] or list(DEFAULT_SUBSTAT_KEYS)
    team_context = team_context or {}

    pipeline_steps = cfg.get("evaluation_pipeline", [])
    if not pipeline_steps:
        # Mirrors character_scoring.score_character's own hard requirement -
        # every roster character must declare one post-migration. Return an
        # all-zero vector rather than raising, so a single misconfigured
        # character doesn't take down the whole roll-value pass for
        # everyone else.
        return {stat: 0.0 for stat in useful_stats}
    pipeline_metadata = {
        "modifiers": cfg.get("modifiers", []),
        "character_config": cfg,
    }

    base_deltas = [
        compute_artifact_delta(a) for a in artifacts_by_slot.values() if a is not None
    ]
    base_stats = combine_artifact_deltas(base_deltas, cfg, team_context)
    base_damage = run_pipeline(pipeline_steps, base_stats, pipeline_metadata)

    values = {}
    for stat_key in useful_stats:
        avg_val = _average_roll_value(roll_values, stat_key, rarity)
        if avg_val == 0.0:
            values[stat_key] = 0.0
            continue

        synthetic_artifact = {
            "mainStatKey": None,
            "mainStatValue": 0.0,
            "substats": [{"key": stat_key, "value": avg_val}],
            "rarity": rarity,
        }
        perturb_delta = compute_artifact_delta(synthetic_artifact)
        perturbed_stats = combine_artifact_deltas(base_deltas + [perturb_delta], cfg, team_context)
        perturbed_damage = run_pipeline(pipeline_steps, perturbed_stats, pipeline_metadata)

        values[stat_key] = perturbed_damage - base_damage

    return values