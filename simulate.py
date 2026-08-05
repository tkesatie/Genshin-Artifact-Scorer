import random
from copy import deepcopy
from collections import defaultdict

from artifact_utils import MAX_LEVEL
from stats_calculator import calculate_build_stats
from damage_calculator import calculate_damage_score


def _random_roll_value(key, rarity, roll_values):
    """Pick a random roll tier for a given substat key and rarity."""
    table = roll_values["five_star"] if rarity >= 5 else roll_values["four_star"]
    choices = table.get(key, [0])
    return random.choice(choices)


def project_artifact_random(artifact, roll_values):
    """
    Return a copy of the artifact with remaining upgrades randomly distributed.
    Assumes we start from the artifact's current level and level it to max.
    """
    rarity = artifact.get("rarity", 5)
    max_level = MAX_LEVEL.get(rarity, 20)
    current_level = artifact.get("level", 0)
    remaining_levels = max_level - current_level
    remaining_events = remaining_levels // 4

    if remaining_events <= 0:
        # Already maxed — just return a clean copy
        new_art = deepcopy(artifact)
        new_art["unactivatedSubstats"] = []
        return new_art

    new_art = deepcopy(artifact)
    active_subs = new_art.get("substats", [])
    hidden_subs = new_art.get("unactivatedSubstats", [])

    # First upgrade reveals the hidden line if present
    if hidden_subs and remaining_events > 0:
        revealed = hidden_subs[0]
        active_subs.append(revealed)
        remaining_events -= 1
    new_art["unactivatedSubstats"] = []  # all revealed or none

    # Distribute remaining upgrades randomly
    for _ in range(remaining_events):
        if not active_subs:
            break
        chosen = random.choice(active_subs)
        key = chosen.get("key")
        if not key:
            continue
        roll_val = _random_roll_value(key, rarity, roll_values)
        chosen["value"] = chosen.get("value", 0) + roll_val

    new_art["substats"] = active_subs
    new_art["level"] = max_level
    return new_art


def evaluate_artifact_swap(char_config, current_artifacts, candidate_artifact, slot, roll_values,
                           current_stats, current_damage, num_sims=1000, damage_model="none"):
    """
    Run Monte Carlo simulation for swapping one artifact slot.
    Returns: { "win_rate": float, "avg_gain_pct": float }
    """
    if current_damage <= 0:
        return {"win_rate": 0.0, "avg_gain_pct": 0.0}

    wins = 0
    total_gain_pct = 0.0

    # Build a fresh context base; we'll replace the candidate slot each time
    base_context = {
        "character_config": char_config,
        "artifacts": dict(current_artifacts),  # copy
        "team_context": {},
        "roll_values": roll_values,
        "damage_model": damage_model,
    }

    for _ in range(num_sims):
        projected = project_artifact_random(candidate_artifact, roll_values)
        # Replace the specific slot
        test_artifacts = dict(base_context["artifacts"])
        test_artifacts[slot] = projected
        test_context = dict(base_context)
        test_context["artifacts"] = test_artifacts

        test_stats = calculate_build_stats(test_context)
        test_damage = calculate_damage_score(test_stats, damage_model)

        if test_damage >= current_damage:
            wins += 1
            gain_pct = (test_damage / current_damage - 1) * 100
            total_gain_pct += gain_pct

    win_rate = wins / num_sims
    avg_gain_pct = total_gain_pct / num_sims if num_sims > 0 else 0.0

    return {
        "win_rate": round(win_rate * 100, 1),  # as percentage (e.g., 72.5)
        "avg_gain_pct": round(avg_gain_pct, 1),
    }