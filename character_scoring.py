"""
Module: character_scoring

Purpose:
This module is responsible for scoring characters based on their equipped artifacts. It provides a comprehensive evaluation of each character's artifact setup, including roll counts, thresholds, and upgrade potential. The primary goal is to assist in optimizing character builds by quantifying the effectiveness of artifact configurations.

Responsibilities:
1. **Artifact Scoring**: Evaluate individual artifacts to determine if they meet "good" or "excellent" thresholds based on character usage and role.
2. **Character Status Determination**: Assign a status (e.g., "Farming", "Usable", "Finished", "Luxury") to each character based on the quality of their equipped artifacts.
3. **Set Completion Tracking**: Determine whether the character's equipped artifacts actually satisfy their configured set's 2pc/4pc bonus, independent of substat roll quality.
4. **Domain Scoring**: Aggregate scores for characters within specific domains, applying weights based on character usage type ("Active" or "IT Only").

Architectural Role:
This module serves as a business logic layer within the Artifact Scorer project. It is expected to be used by higher-level modules responsible for orchestrating the scoring process and presenting results to users. The module relies on utility functions from `artifact_utils`, `bench` (for set alias resolution), and configuration data from `thresholds`.

Intended Dependencies:
- **artifact_utils**: Provides utility functions for calculating effective useful pools and roll counts.
- **bench**: Provides `SET_ALIASES` for resolving a roster character's short set label to real GOOD setKeys.
- **thresholds**: Contains configuration rules for determining artifact thresholds and domain scoring weights.

Boundaries:
- This module should not handle user input or output. It focuses solely on the logic of scoring characters and artifacts.
- The presentation layer should be responsible for displaying results to users, while this module provides the underlying data.
- Domain-specific logic that is not related to artifact scoring should be handled in separate modules.

Public API:
- `compute_set_status(cfg, artifacts_by_slot)`: Determines whether the character's equipped pieces satisfy their target set's 2pc/4pc bonus.
- `score_character(char_name, cfg, artifacts_by_slot, rules, roll_values, bench_lookup, bench_candidates)`: Evaluates a character's artifact setup and returns a detailed score report, including set completion status.
- `score_domains(char_results, rules)`: Aggregates scores for characters within domains and applies domain-specific scoring rules.

"""

from collections import defaultdict

from artifact_utils import effective_useful_pool, roll_count_for_artifact
from bench import SET_ALIASES
from thresholds import compute_thresholds


def compute_set_status(cfg, artifacts_by_slot):
    """
    Determine whether the character's currently equipped artifacts actually
    satisfy their configured set's 2pc/4pc bonus. Roll quality is scored
    per-slot independently elsewhere, so nothing else in the pipeline checks
    whether those "Excellent" pieces even come from the same set - a
    character can look fully built and still have zero active set bonus.

    Split builds (set field containing "/", e.g. "2pc/2pc") aren't resolved
    against real setKeys yet - same limitation flex.py already carves out
    via is_four_piece_locked. Reported but not validated.

    Returns:
        {
            "target": the configured set label (or None),
            "matching": count of equipped pieces from the target set,
            "active_bonus": "4pc" | "2pc" | "None" | "Split (unverified)" | "N/A",
            "complete": True if the 4pc bonus is active, False if not,
                        None if not applicable/not checkable,
        }
    """
    target = cfg.get("set")

    if not target:
        return {"target": None, "matching": 0, "active_bonus": "N/A", "complete": None}

    if "/" in str(target):
        return {
            "target": target,
            "matching": None,
            "active_bonus": "Split (unverified)",
            "complete": None,
        }

    target_keys = set(SET_ALIASES.get(target, [target]))

    equipped_set_keys = [
        art.get("setKey")
        for art in artifacts_by_slot.values()
        if art is not None
    ]

    matching = sum(1 for k in equipped_set_keys if k in target_keys)

    if matching >= 4:
        active_bonus = "4pc"
    elif matching >= 2:
        active_bonus = "2pc"
    else:
        active_bonus = "None"

    return {
        "target": target,
        "matching": matching,
        "active_bonus": active_bonus,
        "complete": matching >= 4,
    }


def score_character(char_name, cfg, artifacts_by_slot, rules, roll_values, bench_lookup, bench_candidates):
    usage, role = cfg["usage"], cfg["role"]
    useful_stats = [str(s) for s in cfg["useful_stats"]]
    set_status = compute_set_status(cfg, artifacts_by_slot)
    slots_result = {}
    for slot in ["Flower", "Feather", "Sands", "Goblet", "Circlet"]:
        art = artifacts_by_slot.get(slot)
        bench_info = bench_lookup.get((char_name, slot), {"expected": 0, "max": 0})
        bench_expected, bench_ceiling = bench_info["expected"], bench_info["max"]
        if art is None:
            slots_result[slot] = {
                "status": "Missing", "roll_status": "Fail",
                "roll_count": 0, "good": None, "excellent": None,
                "bench_expected": bench_expected, "bench_ceiling": bench_ceiling,
                "upgradeable": bench_ceiling > 0,
            }
            continue
        rarity = art.get("rarity", 5)
        eff_pool = effective_useful_pool(art.get("mainStatKey"), useful_stats)
        good, excellent = compute_thresholds(rules, usage, role, slot, eff_pool, char_name)
        rc = roll_count_for_artifact(art, useful_stats, roll_values)
        roll_status = "Pass" if rc >= good else "Fail"
        if rc < good:
            status = "Needs Work"
        elif rc < excellent:
            status = "Good"
        else:
            status = "Excellent"
        slots_result[slot] = {
            "status": status, "roll_status": roll_status,
            "roll_count": round(rc, 2), "good": good, "excellent": excellent,
            "bench_expected": bench_expected, "bench_ceiling": bench_ceiling,
            "upgradeable": bench_ceiling > rc,
        }

    completion = sum(1 for s in slots_result.values() if s["status"] not in ("Needs Work", "Missing"))
    excellent_pieces = sum(1 for s in slots_result.values() if s["status"] == "Excellent")
    needs_work = [slot for slot, s in slots_result.items() if s["status"] in ("Needs Work", "Missing")]
    upgrades_good = 0
    upgrades_excellent = 0

    for slot, s in slots_result.items():
        equipped_rolls = s["roll_count"]
        candidates = bench_candidates.get((char_name, slot), [])
        for candidate in candidates:
            if candidate["max_rolls"] > equipped_rolls:
                if candidate["verdict"] == "Could reach Excellent":
                    upgrades_excellent += 1
                elif candidate["verdict"] == "Could reach Good":
                    upgrades_good += 1

    base = rules["base_thresholds"][f"{usage}|{role}"]
    if needs_work:
        char_status = "Farming"
    elif excellent_pieces >= base["luxury_excellent"]:
        char_status = "Luxury"
    elif excellent_pieces >= base["finished_excellent"]:
        char_status = "Finished"
    else:
        char_status = "Usable"

    if usage == "Active" and char_status == "Farming":
        tier = 1
    elif usage == "IT Only" and char_status == "Farming":
        tier = 2
    elif usage == "Active" and char_status == "Finished":
        tier = 3
    elif usage == "IT Only" and char_status == "Finished":
        tier = 4
    else:
        tier = 5
    score = 1000 - (tier * 100 + completion * 10 + excellent_pieces)

    # A character can score Finished/Luxury purely on per-slot roll quality
    # while wearing artifacts from mismatched sets, since roll scoring never
    # checks setKey. Flag that mismatch explicitly rather than let a
    # "Luxury" badge imply a build that isn't actually functioning.
    set_bonus_mismatch = (
        char_status in ("Finished", "Luxury")
        and set_status["complete"] is False
    )

    return {
        "name": char_name, "usage": usage, "role": role, "domain": cfg.get("domain"),
        "status": char_status, "completion": completion, "excellent_pieces": excellent_pieces,
        "needs_work": needs_work, "score": score, "slots": slots_result,
        "luxury_target": base["luxury_excellent"],
        "upgrades_good": upgrades_good,
        "upgrades_excellent": upgrades_excellent,
        "set_status": set_status,
        "set_bonus_mismatch": set_bonus_mismatch,
    }


def score_domains(char_results, rules):
    weights = rules["domain_scoring"]
    domains = defaultdict(lambda: {"characters": [], "score": 0.0, "active": 0, "it_only": 0})
    for r in char_results:
        d = r["domain"]
        if d is None or d == "None":
            d = "None"
        entry = domains[d]
        entry["characters"].append(r["name"])
        w = weights["active_weight"] if r["usage"] == "Active" else weights["it_only_weight"]
        entry["score"] += (5 - r["completion"]) * w
        if r["status"] != "Luxury":
            entry["score"] += max(0, r["luxury_target"] - r["excellent_pieces"]) * weights["finished_polish_weight"] * w
        if r["usage"] == "Active":
            entry["active"] += 1
        else:
            entry["it_only"] += 1
    return dict(domains)