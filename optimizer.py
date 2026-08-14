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
from pipeline import run_pipeline


def _random_roll_value(substat_key: str, rarity: int, roll_values: Dict) -> float:
    """Pick a random roll value for a given substat key and rarity."""
    table = roll_values["five_star"] if rarity >= 5 else roll_values["four_star"]
    choices = table.get(substat_key, [0.0])
    return random.choice(choices)


def _random_roll_value(substat_key: str, rarity: int, roll_values: Dict) -> float:
    """Pick a random roll value for a given substat key and rarity."""
    table = roll_values["five_star"] if rarity >= 5 else roll_values["four_star"]
    choices = table.get(substat_key, [0.0])
    return random.choice(choices)


def _project_artifact_to_level(
    artifact: Dict[str, Any],
    roll_values: Dict,
    target_level: int = None
) -> Dict[str, Any]:
    """
    Project the given artifact to a specified level (must be a multiple of 4)
    by randomly distributing upgrades. If target_level is None, project to max level.

    Returns a new artifact dict with updated level, substats, and cleared unactivated.
    """
    rarity = artifact.get("rarity", 5)
    max_level = MAX_LEVEL.get(rarity, 20)
    current_level = artifact.get("level", 0)

    # If no target specified, go to max
    if target_level is None:
        target_level = max_level

    # Validation
    if target_level < current_level:
        raise ValueError(
            f"target_level ({target_level}) < current_level ({current_level})"
        )
    if target_level % 4 != 0:
        raise ValueError(
            f"target_level ({target_level}) must be a multiple of 4"
        )
    if target_level > max_level:
        raise ValueError(
            f"target_level ({target_level}) exceeds max level ({max_level})"
        )

    remaining_levels = target_level - current_level
    remaining_events = (remaining_levels + 3) // 4 if remaining_levels > 0 else 0

    # Shallow copy – safe because we'll replace substats and unactivated
    new_art = dict(artifact)

    # Already at or above target: just copy substats defensively and return
    if remaining_events <= 0:
        new_art["unactivatedSubstats"] = []
        new_art["substats"] = list(artifact.get("substats", []))
        new_art["level"] = target_level
        return new_art

    # Fresh dicts for substats we will mutate
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
    new_art["level"] = target_level
    return new_art


# ---- Legacy wrapper and public API ----

def _project_artifact(artifact: Dict[str, Any], roll_values: Dict) -> Dict[str, Any]:
    """
    Legacy wrapper – projects to max level.
    Kept for backward compatibility with existing optimizer code.
    """
    return _project_artifact_to_level(artifact, roll_values, target_level=None)


def project_artifact_to_level(
    artifact: Dict[str, Any],
    roll_values: Dict,
    target_level: int
) -> Dict[str, Any]:
    """
    Public API: project an artifact to a specific target level (multiple of 4).
    Used by the leveling efficiency planner.
    """
    return _project_artifact_to_level(artifact, roll_values, target_level)


def _compute_damage_for_build(artifacts: Dict[str, Dict], char_config: Dict,
                              roll_values: Dict,
                              stat_floors: dict = None,
                              team_context: dict = None) -> float:
    context = {
        "character_config": char_config,
        "artifacts": artifacts,
        "team_context": team_context or {},
        "roll_values": roll_values,
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

    # ---- Damage calculation via pipeline ----
    pipeline_steps = char_config.get("evaluation_pipeline", [])
    assert pipeline_steps, f"{char_config.get('name', '?')}: evaluation_pipeline is empty - every character must declare one post-migration"
    metadata = {
        "modifiers": char_config.get("modifiers", []),
        "character_config": char_config,
    }
    raw_damage = run_pipeline(pipeline_steps, stats, metadata)
    return raw_damage


def _compute_damage_for_stats(stats: Dict, char_config: Dict,
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

    # Get the pipeline from the character config
    pipeline_steps = char_config.get("evaluation_pipeline", [])
    assert pipeline_steps, f"{char_config.get('name', '?')}: evaluation_pipeline is empty - every character must declare one post-migration"

    # Build metadata for the pipeline steps
    metadata = {
        "modifiers": char_config.get("modifiers", []),
        "character_config": char_config,
    }

    # Run the pipeline and return the final score
    return run_pipeline(pipeline_steps, stats, metadata)

def compute_marginal_swap_probabilities(
    char_config: Dict,
    in_set_pools: Dict[str, List[Dict]],
    off_set_pools: Dict[str, List[Dict]],
    current_artifacts: Dict[str, Dict],
    roll_values: Dict,
    target_set_keys: set,
    num_sims: int = 1000,
    stat_floors: dict = None,
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
                    build_artifacts, char_config, roll_values, stat_floors, team_context
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


def _prune_slot_candidates(
    slot_candidates: Dict[str, List[Tuple]],
    win_counts: Dict[str, int],
    warmup_sims: int,
    threshold: float,
    min_keep: int,
    current_artifacts: Dict[str, Dict],
) -> Dict[str, List[Tuple]]:
    """
    Drop candidates whose warm-up win rate is strictly below `threshold`.

    The win rate is P(artifact is part of the optimal build) over the warm-up
    sims - i.e. `win_counts[id] / warmup_sims`. A candidate whose probability
    of being optimal is under the cutoff (e.g. 5%) is effectively never a
    contender, so pruning it from the remaining simulations barely changes the
    reported probabilities while drastically shrinking the combo search space.

    Guards applied per slot so pruning never breaks build legality or the
    semantics of the output:
      - always keep at least `min_keep` candidates (by win rate),
      - always keep at least one in-set candidate (>=4-in-set rule),
      - always keep the currently-equipped piece.

    Returns a NEW slot_candidates dict containing only the survivors.
    """
    pruned = {}
    for slot, cand_list in slot_candidates.items():
        survivors = []
        ranked = []  # (win_rate, art_id, artifact, is_in) for min_keep fallback
        for art_id, art, is_in in cand_list:
            rate = win_counts.get(art_id, 0) / warmup_sims if warmup_sims > 0 else 1.0
            ranked.append((rate, art_id, art, is_in))
            if rate >= threshold:
                survivors.append((art_id, art, is_in))

        # Guard: keep at least one in-set candidate so the >=4-in-set rule can
        # still be satisfied for this slot.
        if not any(c[2] for c in survivors):
            best_in = max(
                (c for c in cand_list if c[2]),
                key=lambda c: win_counts.get(c[0], 0),
                default=None,
            )
            if best_in is not None and not any(c[0] == best_in[0] for c in survivors):
                survivors.append(best_in)

        # Guard: always keep the currently-equipped piece.
        cur = current_artifacts.get(slot)
        if cur is not None:
            cur_id = cur.get('id')
            if not any(c[0] == cur_id for c in survivors):
                match = next((c for c in cand_list if c[0] == cur_id), None)
                if match is not None:
                    survivors.append(match)

        # Guard: enforce min_keep by best warm-up win rate.
        if len(survivors) < min_keep:
            ranked.sort(key=lambda x: x[0], reverse=True)
            for rate, art_id, art, is_in in ranked:
                if len(survivors) >= min_keep:
                    break
                if not any(c[0] == art_id for c in survivors):
                    survivors.append((art_id, art, is_in))

        pruned[slot] = survivors
    return pruned


def compute_optimal_probabilities(
    char_config: Dict,
    in_set_pools: Dict[str, List[Dict]],   # slot -> list of artifact dicts (in-set)
    off_set_pools: Dict[str, List[Dict]],  # slot -> list of artifact dicts (off-set)
    current_artifacts: Dict[str, Dict],    # slot -> artifact dict (the equipped ones)
    roll_values: Dict,
    target_set_keys: set,                  # set of setKeys for the target set
    num_sims: int = 1000,
    stat_floors: dict = None,
    team_context: dict = None,
    prune_after: int = 0,
    prune_threshold: float = 0.05,
    prune_min_keep: int = 1,
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

    ---- Early pruning (optional) ----
    `prune_after` (default 0 = disabled) runs that many "warm-up" simulations
    with the full candidate pool to estimate each candidate's probability of
    being in the optimal build. After that many sims, any candidate whose
    warm-up probability is strictly below `prune_threshold` (default 0.05) is
    dropped from the candidate pool for the remaining `num_sims - prune_after`
    simulations. This shrinks the per-slot candidate lists (and therefore the
    combo enumeration) dramatically while only discarding artifacts that are
    effectively never optimal. Dropped candidates keep the win counts they
    accumulated during the warm-up, so the final probabilities (computed over
    the full `num_sims` run) still include them at their warm-up estimates.
    `prune_min_keep` (default 1) forces at least that many survivors per slot.
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

    # Run simulations
    pruned = False
    for sim_idx in range(num_sims):
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
                delta = compute_artifact_delta(proj)
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
            dmg = _compute_damage_for_stats(stats, char_config, stat_floors)
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

        # Early pruning: after the warm-up phase, drop candidates whose
        # probability of being in the optimal build is below `prune_threshold`.
        # Only happens once; subsequent sims enumerate only the survivors.
        if prune_after > 0 and not pruned and sim_idx == prune_after - 1:
            slot_candidates = _prune_slot_candidates(
                slot_candidates,
                win_counts,
                sim_idx + 1,               # number of warm-up sims completed
                prune_threshold,
                prune_min_keep,
                current_artifacts,
            )
            pruned = True

    # Compute probabilities
    total = num_sims
    probs = {art_id: win_counts[art_id] / total for art_id in win_counts}
    return {
        "probabilities": probs,
        "infeasible_rate": no_valid_sims / total if total > 0 else 0.0,
    }