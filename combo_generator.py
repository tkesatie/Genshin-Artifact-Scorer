# combo_generator.py
from itertools import combinations, product
from damage_calculator import (
    calculate_build_stats, calculate_damage_score,
    apply_set_effects, apply_er_gate
)
from models import BuildContext

def generate_combos(slot_pools: dict, current_artifacts: dict):
    """
    Yields new artifact sets by swapping 1 to 5 slots.
    slot_pools: dict {slot: list of artifact candidates for that slot}
    current_artifacts: dict {slot: currently equipped artifact}
    """
    slots = list(slot_pools.keys())
    for swap_size in range(1, 6):
        for slot_subset in combinations(slots, swap_size):
            candidate_lists = [slot_pools[s] for s in slot_subset]
            for chosen in product(*candidate_lists):
                # Skip if all chosen are the same as current
                all_current = all(chosen[i] == current_artifacts[slot_subset[i]]
                                  for i in range(len(slot_subset)))
                if all_current:
                    continue
                new_set = current_artifacts.copy()
                for idx, slot in enumerate(slot_subset):
                    new_set[slot] = chosen[idx]
                yield new_set

def evaluate_combo(new_artifacts: dict, character_config: dict, team_context: dict,
                   roll_values: dict, damage_model: str, current_raw: float,
                   er_floor: float) -> dict:
    """
    Evaluates a single combo and returns stats, raw score, gain, and synergy.
    """
    # Build context
    ctx = BuildContext(
        character_config=character_config,
        artifacts=new_artifacts,
        team_context=team_context,
        roll_values=roll_values,
        damage_model=damage_model
    )
    stats = calculate_build_stats(ctx)
    raw = calculate_damage_score(stats, damage_model)
    mods = apply_set_effects(new_artifacts, stats)
    # Apply set multipliers (simplified: we multiply raw by the relevant multiplier)
    # In a full implementation, we'd apply per attack type. For now, we use the highest.
    multiplier = max(mods.burst_multiplier, mods.normal_multiplier,
                     mods.skill_multiplier, mods.plunge_multiplier)
    raw *= multiplier

    # ER gate
    er_penalty = apply_er_gate(stats, er_floor)
    if er_penalty is None:
        return None  # Hard reject

    final_score = raw * er_penalty
    gain = (final_score / current_raw) - 1  # relative gain

    return {
        "stats": stats,
        "final_score": final_score,
        "gain": gain,
        "er_penalty": er_penalty,
        "artifact_set": new_artifacts,
    }

def compute_synergy(combo_result: dict, isolated_gains: dict) -> float:
    """
    isolated_gains: dict {slot: gain from swapping only that slot}
    Returns synergy bonus = combo_gain - sum(isolated_gains).
    """
    combo_gain = combo_result["gain"]
    sum_isolated = sum(isolated_gains.values())
    return combo_gain - sum_isolated