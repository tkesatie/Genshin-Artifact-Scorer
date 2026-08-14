"""
Module: strongbox.py

Purpose:
Provides strongbox crafting recommendations using:
  - Substat appearance probabilities (datamined scaled chances).
  - Upgrade roll distribution (4 tiers, uniform).
  - Bench coverage by probability (not just ceiling).
  - Monte Carlo simulation per set to estimate marginal value.
  - Greedy budget allocation across sets.
  - Two-stage allocation when Imaginarium Theater filter is enabled:
    first allocate a fraction of budget to IT-priority sets, then allocate
    the rest to the full roster.

Debug mode:
  When `debug=True`, prints detailed logs to stdout and writes `strongbox_debug.json`
  with all internal data (demand, probabilities, marginal curves, allocation steps).
"""

import os
import json
import random
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple

from artifact_utils import MAX_LEVEL, STAT_LABEL, roll_count_for_artifact
from flex import is_four_piece_locked
from thresholds import compute_thresholds
from optimizer import _project_artifact_to_level

# ---------------------------------------------------------------------
# Substat appearance weights (scaled chances from datamined data)
# These are relative weights; we'll normalize them.
# Values are from the wiki's "Scaled Chance" tables.
# ---------------------------------------------------------------------
SUBSAT_WEIGHTS = {
    "HP": 57.8,
    "ATK": 57.8,
    "DEF": 57.8,
    "HP%": 43.2,
    "ATK%": 43.2,
    "DEF%": 43.2,
    "ER": 43.2,
    "EM": 43.2,
    "CR": 34.2,
    "CD": 34.2,
}

# Roll value multipliers (tiers)
ROLL_TIERS = [0.7, 0.8, 0.9, 1.0]  # uniform distribution assumed

# 5‑star artifact: 34% chance to start with 4 substats, 66% with 3.
PROB_4_LINE = 0.34

# Main stat distributions (for Sands/Goblet/Circlet)
MAIN_STAT_DIST = {
    "Sands": {
        "HP%": 0.27,
        "ATK%": 0.27,
        "DEF%": 0.27,
        "EM": 0.095,
        "ER": 0.095,
    },
    "Goblet": {
        "HP%": 0.19,
        "ATK%": 0.19,
        "DEF%": 0.19,
        "PhysicalDMG%": 0.05,
        "AnemoDMG%": 0.05,
        "GeoDMG%": 0.05,
        "ElectroDMG%": 0.05,
        "HydroDMG%": 0.05,
        "PyroDMG%": 0.05,
        "CryoDMG%": 0.05,
        "DendroDMG%": 0.05,
        "EM": 0.025,
    },
    "Circlet": {
        "HP%": 0.22,
        "ATK%": 0.22,
        "DEF%": 0.22,
        "CR": 0.10,
        "CD": 0.10,
        "Heal%": 0.10,
        "EM": 0.04,
    },
}
# Flower and Feather are fixed.
FIXED_MAIN = {"Flower": "HP", "Feather": "ATK"}

# ---------------------------------------------------------------------
def _draw_main_stat(slot: str) -> str:
    """Draw a random main stat for the given slot."""
    if slot in FIXED_MAIN:
        return FIXED_MAIN[slot]
    dist = MAIN_STAT_DIST.get(slot, {})
    if not dist:
        return "ATK%"  # fallback
    keys, weights = zip(*dist.items())
    return random.choices(keys, weights=weights, k=1)[0]

def _substat_probability_accurate(
    char_name: str,
    slot: str,
    main_stat_key: str,
    useful_stats: List[str],
    good_threshold: int,
    roll_values: Dict,
    num_sims: int = 500
) -> float:
    """
    Compute the probability that a random +0 artifact with the given main stat
    and random substats reaches the 'good' threshold for this character/slot.
    """
    main_label = STAT_LABEL.get(main_stat_key, main_stat_key)
    # Build the substat pool excluding the main stat.
    all_labels = list(SUBSAT_WEIGHTS.keys())
    if main_label in all_labels:
        all_labels.remove(main_label)
    if not all_labels:
        return 0.0

    weights = [SUBSAT_WEIGHTS[label] for label in all_labels]

    def simulate_one():
        # Determine number of initial lines
        if random.random() < PROB_4_LINE:
            initial_count = 4
        else:
            initial_count = 3

        # Select substats without replacement (weighted)
        selected_indices = random.choices(
            population=range(len(all_labels)),
            weights=weights,
            k=initial_count
        )
        selected_labels = [all_labels[i] for i in selected_indices]
        # Build substat list for the artifact
        substats = []
        for label in selected_labels:
            key = None
            for k, v in STAT_LABEL.items():
                if v == label:
                    key = k
                    break
            if key is not None:
                substats.append({"key": key, "value": 0.0, "initialValue": 0.0})

        # If 3‑line, we need a hidden 4th line.
        unactivated = []
        if initial_count == 3:
            remaining_indices = [i for i in range(len(all_labels)) if i not in selected_indices]
            if remaining_indices:
                remaining_weights = [weights[i] for i in remaining_indices]
                hidden_idx = random.choices(remaining_indices, weights=remaining_weights, k=1)[0]
                hidden_label = all_labels[hidden_idx]
                hidden_key = None
                for k, v in STAT_LABEL.items():
                    if v == hidden_label:
                        hidden_key = k
                        break
                if hidden_key is not None:
                    unactivated = [{"key": hidden_key, "value": 0.0, "initialValue": 0.0}]

        artifact = {
            "rarity": 5,
            "level": 0,
            "substats": substats,
            "unactivatedSubstats": unactivated,
            "mainStatKey": main_stat_key,
        }
        projected = _project_artifact_to_level(artifact, roll_values, target_level=20)
        rc = roll_count_for_artifact(projected, useful_stats, roll_values)
        return rc >= good_threshold

    successes = 0
    for _ in range(num_sims):
        if simulate_one():
            successes += 1
    return successes / num_sims

# ---------------------------------------------------------------------
# Demand building with bench coverage probability
# ---------------------------------------------------------------------
def build_demand(
    rules: Dict,
    roster: Dict[str, Dict],
    char_results: List[Dict[str, Any]],
    bench_results: List[Dict[str, Any]],
    roll_values: Dict,
    coverage_prob_threshold: float = 0.50,
    debug: bool = False
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict]:
    """
    Build demand units per set. Returns (demand_by_set, debug_info).
    """
    eligible_sets = set(rules.get("strongbox", {}).get("eligible_sets", []))
    char_result_map = {r["name"]: r for r in char_results}
    bench_by_char_slot = defaultdict(list)
    for b in bench_results:
        char = b.get("character")
        slot = b.get("slot")
        if char and slot:
            bench_by_char_slot[(char, slot)].append(b)

    demand_by_set = defaultdict(list)
    debug_info = {
        "coverage_checks": [],
        "covered_slots": [],
        "uncovered_slots": [],
    }

    for char_name, cfg in roster.items():
        if not is_four_piece_locked(cfg):
            continue
        set_short = cfg.get("set")
        if not set_short or set_short not in eligible_sets:
            continue

        char_result = char_result_map.get(char_name)
        if not char_result:
            continue

        score = char_result.get("score", 0)
        slots_info = char_result.get("slots", {})
        main_stats_cfg = cfg.get("main_stats", {})

        for slot in ["Flower", "Feather", "Sands", "Goblet", "Circlet"]:
            slot_info = slots_info.get(slot)
            if not slot_info:
                continue
            status = slot_info.get("status")
            if status not in ("Missing", "Needs Work"):
                continue
            if status in ("Good", "Excellent"):
                continue  # already covered

            good = slot_info.get("good")
            if good is None:
                continue
            useful_stats = [str(s) for s in cfg.get("useful_stats", [])]

            # Check bench coverage
            covered = False
            best_bench_prob = 0.0
            for b in bench_by_char_slot.get((char_name, slot), []):
                art = b.get("original_artifact")
                if not art:
                    continue
                # Estimate probability this bench piece can reach good
                # Use small Monte Carlo (30 sims for speed)
                sims = 30
                success = 0
                for _ in range(sims):
                    proj = _project_artifact_to_level(art, roll_values, target_level=20)
                    rc = roll_count_for_artifact(proj, useful_stats, roll_values)
                    if rc >= good:
                        success += 1
                prob = success / sims
                if prob > best_bench_prob:
                    best_bench_prob = prob
                if prob >= coverage_prob_threshold:
                    covered = True
                    break

            debug_info["coverage_checks"].append({
                "character": char_name,
                "slot": slot,
                "best_bench_prob": best_bench_prob,
                "covered": covered,
                "threshold": coverage_prob_threshold
            })
            if covered:
                debug_info["covered_slots"].append((char_name, slot))
            else:
                debug_info["uncovered_slots"].append((char_name, slot))

            if covered:
                continue

            # Not covered → create demand
            allowed = main_stats_cfg.get(slot, [])
            if slot == "Flower":
                allowed = ["HP"]
            elif slot == "Feather":
                allowed = ["ATK"]
            elif "ANY" in allowed:
                allowed = None
            demand_by_set[set_short].append({
                "character": char_name,
                "slot": slot,
                "allowed_main_stats": allowed,
                "weight": score,
                "good": good,
                "excellent": slot_info.get("excellent"),
                "useful_stats": useful_stats,
            })

    return dict(demand_by_set), debug_info

# ---------------------------------------------------------------------
# Precompute substat probabilities for each demand's main stats
# ---------------------------------------------------------------------
def precompute_substat_probabilities(
    demand_by_set: Dict[str, List[Dict[str, Any]]],
    roll_values: Dict,
    num_sims_per_stat: int = 500,
    debug: bool = False
) -> Tuple[Dict[Tuple[str, str, str], float], Dict]:
    """
    Returns (prob_cache, debug_info).
    """
    cache = {}
    debug_info = {"cached_entries": []}
    for demands in demand_by_set.values():
        for d in demands:
            char = d["character"]
            slot = d["slot"]
            good = d["good"]
            useful = d["useful_stats"]
            allowed = d["allowed_main_stats"]

            main_stats = allowed if allowed is not None else list(MAIN_STAT_DIST.get(slot, {}).keys())
            for main_label in main_stats:
                # Find mainStatKey
                main_key = None
                for k, v in STAT_LABEL.items():
                    if v == main_label:
                        main_key = k
                        break
                if main_key is None:
                    continue
                key = (char, slot, main_label)
                if key in cache:
                    continue
                prob = _substat_probability_accurate(
                    char, slot, main_key, useful, good,
                    roll_values, num_sims=num_sims_per_stat
                )
                cache[key] = prob
                debug_info["cached_entries"].append({
                    "character": char,
                    "slot": slot,
                    "main_stat": main_label,
                    "probability": prob,
                    "good_threshold": good
                })
    return cache, debug_info

# ---------------------------------------------------------------------
# Simulation per set (marginal value curve)
# ---------------------------------------------------------------------
def simulate_set(
    set_label: str,
    demand_units: List[Dict[str, Any]],
    prob_cache: Dict[Tuple[str, str, str], float],
    num_sims: int = 1000,
    max_crafts: int = 20
) -> List[float]:
    """
    Returns marginal values for craft 1..max_crafts.
    """
    if not demand_units:
        return [0.0] * max_crafts

    # Precompute for each demand its possible hits (main_stat -> prob)
    demand_hits = []
    for d in demand_units:
        slot = d["slot"]
        allowed = d["allowed_main_stats"]
        main_stats = allowed if allowed is not None else list(MAIN_STAT_DIST.get(slot, {}).keys())
        hits = []
        for main_label in main_stats:
            prob = prob_cache.get((d["character"], slot, main_label), 0.0)
            if prob > 0.0:
                hits.append((main_label, prob))
        if hits:
            demand_hits.append({
                "demand": d,
                "hits": hits,
                "weight": d["weight"],
            })

    if not demand_hits:
        return [0.0] * max_crafts

    cumulative = [0.0] * (max_crafts + 1)

    for _ in range(num_sims):
        remaining = demand_hits.copy()
        satisfied = [0.0] * max_crafts

        for craft_idx in range(max_crafts):
            slot = random.choice(["Flower", "Feather", "Sands", "Goblet", "Circlet"])
            main_stat = _draw_main_stat(slot)

            best_val = -1.0
            best_idx = -1
            for i, entry in enumerate(remaining):
                if entry["demand"]["slot"] != slot:
                    continue
                for hit_main, prob in entry["hits"]:
                    if hit_main == main_stat:
                        val = entry["weight"] * prob
                        if val > best_val:
                            best_val = val
                            best_idx = i
                        break

            if best_idx >= 0:
                satisfied[craft_idx] = best_val
                remaining.pop(best_idx)
            else:
                satisfied[craft_idx] = 0.0

        running = 0.0
        for i in range(max_crafts):
            running += satisfied[i]
            cumulative[i+1] += running

    avg_cum = [c / num_sims for c in cumulative]
    marginals = [avg_cum[i] - avg_cum[i-1] for i in range(1, max_crafts+1)]
    return marginals

# ---------------------------------------------------------------------
# Budget allocation (full roster, single stage)
# ---------------------------------------------------------------------
def allocate_budget(
    demand_by_set: Dict[str, List[Dict[str, Any]]],
    prob_cache: Dict[Tuple[str, str, str], float],
    total_crafts: int,
    num_sims: int = 1000,
    max_crafts_per_set: int = 20
) -> Dict[str, Any]:
    """
    Original single-stage greedy allocation (full roster).
    Returns recommendations, beneficiaries, curves, allocation_steps.
    """
    if total_crafts <= 0 or not demand_by_set:
        return {
            "recommendations": {},
            "beneficiaries": {},
            "beneficiary_slots": {},
            "curves": {},
            "allocation_steps": [],
        }

    curves = {}
    for set_label, demands in demand_by_set.items():
        curves[set_label] = simulate_set(
            set_label, demands, prob_cache,
            num_sims=num_sims,
            max_crafts=max_crafts_per_set
        )

    allocated = {s: 0 for s in curves}
    remaining = total_crafts
    allocation_steps = []

    while remaining > 0:
        best = None
        best_marginal = -1.0
        for label, curve in curves.items():
            pos = allocated[label]
            if pos < len(curve) and curve[pos] > best_marginal:
                best_marginal = curve[pos]
                best = label
        if best is None or best_marginal <= 0:
            break
        allocated[best] += 1
        remaining -= 1
        allocation_steps.append({
            "step": total_crafts - remaining,
            "set": best,
            "marginal": best_marginal
        })

    beneficiaries = {}
    beneficiary_slots = {}
    for set_label, crafts in allocated.items():
        if crafts == 0:
            continue
        demands = demand_by_set.get(set_label, [])
        chars = set(d["character"] for d in demands)
        beneficiaries[set_label] = list(chars)
        beneficiary_slots[set_label] = [(d["character"], d["slot"]) for d in demands]

    return {
        "recommendations": allocated,
        "beneficiaries": beneficiaries,
        "beneficiary_slots": beneficiary_slots,
        "curves": curves,
        "allocation_steps": allocation_steps,
    }

# ---------------------------------------------------------------------
# Two-stage allocation (IT focus + full roster)
# ---------------------------------------------------------------------
def allocate_budget_two_stage(
    full_demand_by_set: Dict[str, List[Dict[str, Any]]],
    it_demand_by_set: Dict[str, List[Dict[str, Any]]],
    prob_cache_full: Dict[Tuple[str, str, str], float],
    prob_cache_it: Dict[Tuple[str, str, str], float],
    total_crafts: int,
    it_budget: int,
    num_sims: int = 1000,
    max_crafts_per_set: int = 20,
    debug: bool = False
) -> Dict[str, Any]:
    """
    Two-stage allocation:
    1. Allocate up to `it_budget` crafts using the IT-only marginal curves.
    2. Allocate the remaining crafts using full-roster curves, with IT sets
       advanced by the number already taken in stage 1.
    Returns the same structure as allocate_budget plus extra debug fields.
    """
    if total_crafts <= 0:
        return {"recommendations": {}, "beneficiaries": {}, "beneficiary_slots": {}, "curves": {}, "allocation_steps": []}

    # --- Stage 1: IT-only allocation ---
    # Compute IT marginal curves
    it_curves = {}
    for set_label, demands in it_demand_by_set.items():
        it_curves[set_label] = simulate_set(
            set_label, demands, prob_cache_it,
            num_sims=num_sims, max_crafts=max_crafts_per_set
        )

    # Greedy allocation for IT only, capped at it_budget
    it_alloc = {s: 0 for s in it_curves}
    remaining_it = it_budget
    it_allocation_steps = []
    while remaining_it > 0:
        best_set = None
        best_marginal = -1.0
        for label, curve in it_curves.items():
            pos = it_alloc[label]
            if pos < len(curve) and curve[pos] > best_marginal:
                best_marginal = curve[pos]
                best_set = label
        if best_set is None or best_marginal <= 0:
            break
        it_alloc[best_set] += 1
        remaining_it -= 1
        it_allocation_steps.append({
            "step": it_budget - remaining_it,
            "set": best_set,
            "marginal": best_marginal
        })

    if debug:
        print(f"Stage 1 IT allocation: {it_alloc} (budget {it_budget})")

    # --- Stage 2: Full-roster allocation, with IT sets advanced ---
    # Compute full marginal curves
    full_curves = {}
    for set_label, demands in full_demand_by_set.items():
        full_curves[set_label] = simulate_set(
            set_label, demands, prob_cache_full,
            num_sims=num_sims, max_crafts=max_crafts_per_set
        )

    # Start with the already allocated IT crafts
    final_alloc = it_alloc.copy()
    # For sets not in IT, initialize to 0
    for label in full_curves:
        if label not in final_alloc:
            final_alloc[label] = 0

    # Remaining budget
    remaining_full = total_crafts - it_budget
    full_allocation_steps = []

    # Greedy allocation on full curves, but for IT sets we skip the already allocated positions
    def next_marginal(set_label, position):
        curve = full_curves.get(set_label, [])
        if position < len(curve):
            return curve[position]
        return -1.0

    while remaining_full > 0:
        best_set = None
        best_marginal = -1.0
        for label in full_curves:
            pos = final_alloc[label]
            marginal = next_marginal(label, pos)
            if marginal > best_marginal:
                best_marginal = marginal
                best_set = label
        if best_set is None or best_marginal <= 0:
            break
        final_alloc[best_set] += 1
        remaining_full -= 1
        full_allocation_steps.append({
            "step": total_crafts - remaining_full,
            "set": best_set,
            "marginal": best_marginal
        })

    if debug:
        print(f"Stage 2 final allocation: {final_alloc}")

    # Combine allocation steps (for debug)
    combined_steps = it_allocation_steps + full_allocation_steps

    # Build beneficiaries info (using full demand)
    beneficiaries = {}
    beneficiary_slots = {}
    for set_label, crafts in final_alloc.items():
        if crafts == 0:
            continue
        demands = full_demand_by_set.get(set_label, [])
        chars = set(d["character"] for d in demands)
        beneficiaries[set_label] = list(chars)
        beneficiary_slots[set_label] = [(d["character"], d["slot"]) for d in demands]

    return {
        "recommendations": final_alloc,
        "beneficiaries": beneficiaries,
        "beneficiary_slots": beneficiary_slots,
        "curves": full_curves,  # full curves for debug
        "allocation_steps": combined_steps,
        "it_allocation": it_alloc,   # debug
        "it_allocation_steps": it_allocation_steps,
    }

# ---------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------
def get_strongbox_recommendations(
    rules: Dict,
    roster: Dict[str, Dict],
    char_results: List[Dict[str, Any]],
    bench_results: List[Dict[str, Any]],
    roll_values: Dict,
    strongbox_count: int,
    debug: bool = False,
    it_enabled: bool = False,
    it_focus_fraction: float = 0.7
) -> Dict[str, Any]:
    total_crafts = strongbox_count // 3
    if total_crafts <= 0:
        return {...}

    coverage_threshold = rules.get("strongbox", {}).get("coverage_prob_threshold", 0.50)
    substat_sims = rules.get("strongbox", {}).get("substat_sims", 500)
    num_sims = rules.get("strongbox", {}).get("num_sims", 1000)
    max_crafts = rules.get("strongbox", {}).get("max_crafts_per_set", 20)

    # Build demand for the relevant roster(s)
    if it_enabled and it_focus_fraction < 1.0:
        # Build IT-only demand
        theater_cfg = rules.get("imaginarium_theater", {})
        elements = set(theater_cfg.get("elements", []))
        it_roster = {
            name: cfg for name, cfg in roster.items()
            if cfg.get("usage") == "Active" or cfg.get("element") in elements
        }
        if not it_roster:
            # Fallback to full roster if no IT characters
            it_enabled = False

    if it_enabled and it_focus_fraction < 1.0 and it_roster:
        # IT-only allocation up to the cap
        it_demand, _ = build_demand(rules, it_roster, char_results, bench_results,
                                    roll_values, coverage_threshold, debug)
        if it_demand:
            prob_cache, _ = precompute_substat_probabilities(it_demand, roll_values,
                                                             substat_sims, debug)
            it_budget = min(total_crafts, int(total_crafts * it_focus_fraction))
            allocation = allocate_budget(it_demand, prob_cache, it_budget,
                                         num_sims, max_crafts)
            # allocation already returns the greedy result for the IT-only set
            # We'll use that as the final recommendations
            result = {
                "recommendations": allocation["recommendations"],
                "beneficiaries": allocation["beneficiaries"],
                "beneficiary_slots": allocation["beneficiary_slots"],
                "total_crafts": total_crafts,
                "strongbox_count": strongbox_count,
                "prob_cache_size": len(prob_cache),
                "it_budget": it_budget,
                "saved_crafts": total_crafts - it_budget,
                "message": f"Allocated {it_budget} crafts to IT-priority sets, saved {total_crafts - it_budget} for future months."
            }
            if debug:
                print(f"IT-only allocation up to {it_budget} crafts (saving {total_crafts - it_budget})")
                # ... print debug info from allocation
            return result

    # Fallback: full-roster allocation (original behavior)
    demand_by_set, coverage_debug = build_demand(
        rules, roster, char_results, bench_results, roll_values,
        coverage_prob_threshold=coverage_threshold,
        debug=debug
    )
    if not demand_by_set:
        return {...}
    prob_cache, _ = precompute_substat_probabilities(demand_by_set, roll_values,
                                                     substat_sims, debug)
    allocation = allocate_budget(demand_by_set, prob_cache, total_crafts,
                                 num_sims, max_crafts)
    result = {
        "recommendations": allocation["recommendations"],
        "beneficiaries": allocation["beneficiaries"],
        "beneficiary_slots": allocation["beneficiary_slots"],
        "total_crafts": total_crafts,
        "strongbox_count": strongbox_count,
        "prob_cache_size": len(prob_cache),
        "saved_crafts": 0,
    }
    return result