"""
Module: optimizer

Purpose:
Global artifact set optimizer that finds the optimal 5-piece build (with at least
4 pieces from the target set) for a character, given a pool of candidate artifacts
per slot (both in-set and off-set). Uses Monte Carlo simulation to account for
random upgrade rolls.

Returns:
    A dict mapping artifact_id -> probability of being in the optimal build.
"""

import itertools
import random
from copy import deepcopy
from typing import Dict, List, Any, Tuple

from artifact_utils import MAX_LEVEL
from stats_calculator import calculate_build_stats
from damage_calculator import calculate_damage_score


def _random_roll_value(substat_key: str, rarity: int, roll_values: Dict) -> float:
    """Pick a random roll value for a given substat key and rarity."""
    table = roll_values["five_star"] if rarity >= 5 else roll_values["four_star"]
    choices = table.get(substat_key, [0.0])
    return random.choice(choices)


def _project_artifact(artifact: Dict[str, Any], roll_values: Dict) -> Dict[str, Any]:
    """
    Project the given artifact to +20 by randomly distributing remaining upgrades.
    Returns a new artifact dict with updated level, substats, and cleared unactivated.
    """
    rarity = artifact.get("rarity", 5)
    max_level = MAX_LEVEL.get(rarity, 20)
    current_level = artifact.get("level", 0)
    remaining_levels = max_level - current_level
    remaining_events = remaining_levels // 4

    if remaining_events <= 0:
        # Already maxed: just return a clean copy
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

    new_art["unactivatedSubstats"] = []

    # Distribute remaining upgrades randomly among active substats
    for _ in range(remaining_events):
        if not active_subs:
            break
        chosen = random.choice(active_subs)
        key = chosen.get("key")
        if not key:
            continue
        roll_val = _random_roll_value(key, rarity, roll_values)
        chosen["value"] = chosen.get("value", 0.0) + roll_val

    new_art["substats"] = active_subs
    new_art["level"] = max_level
    return new_art


def _compute_damage_for_build(artifacts: Dict[str, Dict], char_config: Dict,
                              roll_values: Dict, damage_model: str,
                              stat_floors: dict = None) -> float:
    context = {
        "character_config": char_config,
        "artifacts": artifacts,
        "team_context": {},
        "roll_values": roll_values,
        "damage_model": damage_model,
    }
    stats = calculate_build_stats(context)

    # ---- Generic stat floor enforcement ----
    if stat_floors:
        for stat_key, floor_value in stat_floors.items():
            current = stats.get(stat_key, 0)
            if current < floor_value:
                # Option A: hard reject (return -1.0)
                return -1.0
                # Option B: use partial penalty for ER only (see below)

    # ---- Damage calculation (unchanged) ----
    raw_damage = calculate_damage_score(stats, damage_model)
    return raw_damage


def compute_optimal_probabilities(
    char_config: Dict,
    in_set_pools: Dict[str, List[Dict]],   # slot -> list of artifact dicts (in-set)
    off_set_pools: Dict[str, List[Dict]],  # slot -> list of artifact dicts (off-set)
    current_artifacts: Dict[str, Dict],    # slot -> artifact dict (the equipped ones)
    roll_values: Dict,
    target_set_keys: set,                  # set of setKeys for the target set
    num_sims: int = 1000,
    stat_floors: dict = None,
    damage_model: str = "none"
) -> Dict[Any, float]:
    """
    Main optimizer.

    Returns a dict mapping artifact_id -> probability of being in the optimal build.
    The artifact_id is taken from the artifact dict's 'id' field (must be present).
    """
    # Build combined candidate lists per slot: include both in-set and off-set,
    # but we need to know which are in-set for the set rule.
    # We'll store (artifact_id, artifact_dict, is_in_set) tuples.
    slot_candidates = {}
    for slot in ["Flower", "Feather", "Sands", "Goblet", "Circlet"]:
        candidates = []
        # In-set
        for art in in_set_pools.get(slot, []):
            candidates.append((art['id'], art, True))
        # Off-set
        for art in off_set_pools.get(slot, []):
            candidates.append((art['id'], art, False))
        # Also include the current equipped piece if it's not already in the list
        current = current_artifacts.get(slot)
        if current is not None:
            if not any(c[0] == current['id'] for c in candidates):
                is_in = current.get('setKey') in target_set_keys
                candidates.append((current['id'], current, is_in))
        # Remove duplicates (in case of same artifact appearing in both pools)
        seen = set()
        unique = []
        for art_id, art, is_in in candidates:
            if art_id not in seen:
                seen.add(art_id)
                unique.append((art_id, art, is_in))
        slot_candidates[slot] = unique

    # Initialize win counters per artifact_id
    win_counts = {art_id: 0 for slot in slot_candidates for art_id, _, _ in slot_candidates[slot]}

    # Run simulations
    for _ in range(num_sims):
        # Project each candidate to +20
        projected_lists = {}
        for slot, cand_list in slot_candidates.items():
            projected_list = []
            for art_id, art, is_in in cand_list:
                proj = _project_artifact(art, roll_values)
                projected_list.append((art_id, proj, is_in))
            projected_lists[slot] = projected_list

        # Enumerate all combinations and find the best valid one
        best_damage = -1.0
        best_combo = None

        # Generate all combinations using product
        slot_names = list(projected_lists.keys())
        lists = [projected_lists[slot] for slot in slot_names]
        for combo_tuple in itertools.product(*lists):
            # combo_tuple is a tuple of (art_id, proj_art, is_in) for each slot
            # Check legality: at least 4 in-set
            in_count = sum(1 for _, _, is_in in combo_tuple if is_in)
            if in_count < 4:
                continue

            # Build full artifact set
            build_artifacts = {}
            for i, slot in enumerate(slot_names):
                art_id, proj_art, _ = combo_tuple[i]
                build_artifacts[slot] = proj_art

            # Compute damage (with ER check)
            dmg = _compute_damage_for_build(
                build_artifacts, char_config, roll_values, damage_model, stat_floors
            )
            if dmg < 0:
                continue
            if dmg > best_damage:
                best_damage = dmg
                best_combo = combo_tuple

        # Increment win counts for the best combo
        if best_combo is not None:
            for art_id, _, _ in best_combo:
                win_counts[art_id] += 1

    # Compute probabilities
    total = num_sims
    probs = {art_id: win_counts[art_id] / total for art_id in win_counts}
    return probs