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
from typing import Dict, List, Any, Tuple

from artifact_utils import MAX_LEVEL
from stats_calculator import calculate_build_stats, compute_artifact_delta, combine_artifact_deltas
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

    Only substats/level/unactivatedSubstats actually change here, so we shallow-copy
    the artifact dict and make fresh copies of just the substat dicts we mutate,
    instead of deepcopy()'ing the whole artifact (id, setKey, slotKey, mainStatKey,
    etc. are all immutable and safe to share by reference).
    """
    rarity = artifact.get("rarity", 5)
    max_level = MAX_LEVEL.get(rarity, 20)
    current_level = artifact.get("level", 0)
    remaining_levels = max_level - current_level
    remaining_events = remaining_levels // 4

    new_art = dict(artifact)  # shallow copy - cheap, no recursive walk

    if remaining_events <= 0:
        # Already maxed: substats don't change, but copy the list defensively
        # in case a caller mutates it later.
        new_art["unactivatedSubstats"] = []
        new_art["substats"] = list(artifact.get("substats", []))
        return new_art

    # Fresh dict per substat since we mutate "value" below.
    active_subs = [dict(s) for s in artifact.get("substats", [])]
    hidden_subs = artifact.get("unactivatedSubstats", [])

    # First upgrade reveals the hidden line if present
    if hidden_subs and remaining_events > 0:
        active_subs.append(dict(hidden_subs[0]))
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
                              stat_floors: dict = None,
                              team_context: dict = None) -> float:
    context = {
        "character_config": char_config,
        "artifacts": artifacts,
        "team_context": team_context or {},
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
    raw_damage = calculate_damage_score(stats, damage_model, char_config.get("modifiers"))
    return raw_damage


def _compute_damage_for_stats(stats: Dict, char_config: Dict, damage_model: str,
                              stat_floors: dict = None) -> float:
    """
    Same stat-floor + damage-score logic as _compute_damage_for_build, but
    takes an already-combined CharacterStats block instead of raw artifacts.

    Used by compute_optimal_probabilities, where `stats` comes from
    combine_artifact_deltas(precomputed per-candidate deltas) rather than
    re-parsing 5 artifact dicts from scratch via calculate_build_stats - the
    per-artifact parsing is done once per candidate per sim (see the
    projection loop below), not once per combo.
    """
    if stat_floors:
        for stat_key, floor_value in stat_floors.items():
            current = stats.get(stat_key, 0)
            if current < floor_value:
                return -1.0

    return calculate_damage_score(stats, damage_model, char_config.get("modifiers"))

def compute_marginal_swap_probabilities(
    char_config: Dict,
    in_set_pools: Dict[str, List[Dict]],
    off_set_pools: Dict[str, List[Dict]],
    current_artifacts: Dict[str, Dict],
    roll_values: Dict,
    target_set_keys: set,
    num_sims: int = 1000,
    stat_floors: dict = None,
    damage_model: str = "none",
    team_context: dict = None
) -> Dict[str, Dict[Any, float]]:
    """
    Marginal (single-slot) swap probabilities - the GO-style metric.

    Unlike compute_optimal_probabilities (which jointly re-optimizes all 5
    slots and reports P(artifact is part of the global-best combo)), this
    holds the OTHER four slots fixed at currently-equipped and reports
    P(this candidate is the best choice for THIS slot alone, other slots
    unchanged). Compare against compute_optimal_probabilities output to see
    how much of the gap was joint-vs-marginal framing vs. something else.

    Returns: {slot: {artifact_id: probability}}
    """
    slot_names = ["Flower", "Feather", "Sands", "Goblet", "Circlet"]

    slot_candidates = {}
    for slot in slot_names:
        candidates = []
        for art in in_set_pools.get(slot, []):
            candidates.append((art['id'], art, True))
        for art in off_set_pools.get(slot, []):
            candidates.append((art['id'], art, False))
        current = current_artifacts.get(slot)
        if current is not None and not any(c[0] == current['id'] for c in candidates):
            is_in = current.get('setKey') in target_set_keys
            candidates.append((current['id'], current, is_in))
        seen, unique = set(), []
        for art_id, art, is_in in candidates:
            if art_id not in seen:
                seen.add(art_id)
                unique.append((art_id, art, is_in))
        slot_candidates[slot] = unique

    results = {slot: {art_id: 0 for art_id, _, _ in slot_candidates[slot]} for slot in slot_names}

    for target_slot in slot_names:
        other_slots = [s for s in slot_names if s != target_slot]
        other_current = {}
        for s in other_slots:
            cur = current_artifacts.get(s)
            if cur is None:
                continue
            is_in = cur.get('setKey') in target_set_keys
            other_current[s] = (cur, is_in)

        for _ in range(num_sims):
            # Project the four fixed slots once per sim - their own roll RNG
            # still applies, we're just not letting the *choice* of piece vary.
            fixed_build = {}
            fixed_in_count = 0
            for s, (cur, is_in) in other_current.items():
                fixed_build[s] = _project_artifact(cur, roll_values)
                fixed_in_count += 1 if is_in else 0

            best_damage = -1.0
            best_id = None
            for art_id, art, is_in in slot_candidates[target_slot]:
                proj = _project_artifact(art, roll_values)
                build_artifacts = dict(fixed_build)
                build_artifacts[target_slot] = proj

                in_count = fixed_in_count + (1 if is_in else 0)
                if in_count < 4:
                    continue

                dmg = _compute_damage_for_build(
                    build_artifacts, char_config, roll_values, damage_model, stat_floors, team_context
                )
                if dmg < 0:
                    continue
                if dmg > best_damage:
                    best_damage = dmg
                    best_id = art_id

            if best_id is not None:
                results[target_slot][best_id] += 1

    for slot in slot_names:
        for art_id in results[slot]:
            results[slot][art_id] = results[slot][art_id] / num_sims

    return results


def compute_optimal_probabilities(
    char_config: Dict,
    in_set_pools: Dict[str, List[Dict]],   # slot -> list of artifact dicts (in-set)
    off_set_pools: Dict[str, List[Dict]],  # slot -> list of artifact dicts (off-set)
    current_artifacts: Dict[str, Dict],    # slot -> artifact dict (the equipped ones)
    roll_values: Dict,
    target_set_keys: set,                  # set of setKeys for the target set
    num_sims: int = 1000,
    stat_floors: dict = None,
    damage_model: str = "none",
    team_context: dict = None
) -> Dict[str, Any]:
    """
    Main optimizer.

    Returns a dict:
        {
            "probabilities": {artifact_id: probability of being in the optimal build},
            "infeasible_rate": float,  # fraction of sims where NO combo met the stat floors
        }
    The artifact_id is taken from the artifact dict's 'id' field (must be present).

    `infeasible_rate` is the share of simulations where every candidate combo
    failed the configured stat floors (e.g. ER/EM minimums from stat_targets.yaml).
    When it's non-zero, the per-artifact probabilities won't sum to 100% - the
    missing mass is exactly the builds that couldn't reach the minimum thresholds.
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
    no_valid_sims = 0
    primary_stat = char_config.get("primary_stat", "ATK")

    # Run simulations
    for _ in range(num_sims):
        # Project each candidate to +20, and precompute its stat delta ONCE
        # here (not once per combo). Each candidate appears in many combos
        # within this sim - previously, the full 5-artifact stat aggregation
        # (calculate_build_stats) was re-derived from scratch for every one
        # of those combos, redundantly re-parsing the same 4 shared artifacts
        # over and over. Since stat contributions are purely additive per
        # artifact (see stats_calculator.compute_artifact_delta), we can
        # compute each candidate's contribution once and just sum 5
        # precomputed deltas per combo instead - same math, far less work.
        projected_lists = {}
        for slot, cand_list in slot_candidates.items():
            projected_list = []
            for art_id, art, is_in in cand_list:
                proj = _project_artifact(art, roll_values)
                delta = compute_artifact_delta(proj, primary_stat)
                projected_list.append((art_id, proj, is_in, delta))
            projected_lists[slot] = projected_list

        # Enumerate only combos that can legally satisfy the >=4-in-set rule,
        # instead of enumerating the full cartesian product and discarding
        # ~80% of it after the fact. With 5 slots and a 50/50 in/off-set pool
        # split, ~81% of the full product is invalid - generating and then
        # per-tuple-filtering that invalid 81% (itertools.product + a Python
        # sum() over each tuple, done up to 100k times per sim) was the
        # dominant cost, well above the actual damage-formula evaluations.
        #
        # A legal build is either:
        #   (a) all 5 slots in-set, or
        #   (b) exactly 4 slots in-set + 1 slot off-set (5 choices of which
        #       slot is the off-set one).
        # Both cases are generated directly, so every tuple product() yields
        # here is already guaranteed valid - no filtering needed.
        slot_names = list(projected_lists.keys())
        n_slots = len(slot_names)
        in_lists = [[c for c in projected_lists[slot] if c[2]] for slot in slot_names]
        off_lists = [[c for c in projected_lists[slot] if not c[2]] for slot in slot_names]

        best_damage = -1.0
        best_combo = None

        def _evaluate(combo_tuple):
            nonlocal best_damage, best_combo
            deltas = [combo_tuple[i][3] for i in range(n_slots)]
            stats = combine_artifact_deltas(deltas, char_config, team_context)
            dmg = _compute_damage_for_stats(stats, char_config, damage_model, stat_floors)
            if dmg < 0:
                return
            if dmg > best_damage:
                best_damage = dmg
                best_combo = combo_tuple

        # Case (a): all 5 slots in-set.
        if all(in_lists):
            for combo_tuple in itertools.product(*in_lists):
                _evaluate(combo_tuple)

        # Case (b): exactly one slot uses an off-set candidate.
        for off_idx in range(n_slots):
            if not off_lists[off_idx]:
                continue
            # The other n_slots-1 slots must all be in-set; if any of them
            # has no in-set candidates at all, this case is impossible for
            # that off_idx (itertools.product would yield nothing anyway,
            # but we skip building the generator entirely).
            if any(not in_lists[i] for i in range(n_slots) if i != off_idx):
                continue
            per_slot_lists = [
                off_lists[i] if i == off_idx else in_lists[i]
                for i in range(n_slots)
            ]
            for combo_tuple in itertools.product(*per_slot_lists):
                _evaluate(combo_tuple)

        # Increment win counts for the best combo
        if best_combo is not None:
            for art_id, _, _, _ in best_combo:
                win_counts[art_id] += 1
        else:
            # No combo in this sim met the stat floors - count it so the
            # dashboard can show how much of the build space is unreachable.
            no_valid_sims += 1

    # Compute probabilities
    total = num_sims
    probs = {art_id: win_counts[art_id] / total for art_id in win_counts}
    return {
        "probabilities": probs,
        "infeasible_rate": no_valid_sims / total if total > 0 else 0.0,
    }