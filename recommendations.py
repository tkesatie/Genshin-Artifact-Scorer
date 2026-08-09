"""
Module: recommendations.py

Purpose:
This module is responsible for generating recommendations based on benchmarking results and character equipment data. It helps in identifying potential upgrades for characters by comparing equipped artifacts with available benchmarks.

Responsibilities:
1. **Verdict Determination**: Categorize the practical impact of swapping equipped artifacts with benchmarked candidates.
2. **Recommendation Building**: Aggregate and prioritize recommendations for artifact swaps based on their potential impact and feasibility.
3. **Sorting and Grouping**: Organize recommendations by character and slot, ensuring that the most impactful upgrades are presented first.

Architectural Role:
This module serves as a business logic layer within the application. It is expected to be used by higher-level modules responsible for orchestrating the recommendation process and presenting results to users. The module does not handle user interface or data storage directly but relies on input from other parts of the system.

Intended Dependencies:
- **score.py**: For artifact parsing, scoring calculations, EV (Expected Value) calculations, and recommendation logic.
- **test.py**: For testing purposes, ensuring that recommendations are generated correctly based on various scenarios.

Boundaries:
- This module should not handle user input or output. It focuses solely on processing data and generating recommendations.
- The actual implementation details of artifact parsing and scoring should remain within the `score.py` module.
- Presentation logic, such as formatting recommendations for display, should be handled by separate modules.

Public API:
- **determine_verdict(equipped_rolls, ceiling, good_thresh, exc_thresh)**: Categorizes the practical impact of swapping equipped artifacts based on given thresholds.
- **build_recommendations(bench_results, char_results, top_n_per_slot=3)**: Generates and prioritizes recommendations for artifact swaps based on benchmarking results and character equipment data.
- **build_ceiling_only_candidates(bench_results, char_results, top_n_per_slot=5)**: Surfaces "Dead end"-verdict candidates separately - pieces whose optimistic ceiling beats what's equipped but whose expected value doesn't clear the Good/Excellent threshold. Excluded from build_recommendations' confirmed list on purpose; kept available here for consumers (like the character detail modal) that want to show every real possibility, clearly tagged as higher-risk, without polluting the primary recommendation table.
"""

from collections import defaultdict


def determine_verdict(equipped_rolls, ceiling, good_thresh, exc_thresh):
    """Categorize the practical impact of swapping to this candidate."""
    is_equipped_failing = equipped_rolls < good_thresh

    if ceiling >= exc_thresh:
        if is_equipped_failing:
            return "Major Breakthrough"  # Needs Work -> Could reach Excellent
        return "Luxury Upgrade"          # Good/Exc -> Could reach Excellent
    elif ceiling >= good_thresh:
        if is_equipped_failing:
            return "Patch / Fix"          # Needs Work -> Could reach Good
        return "Minor Polish"            # Good -> Could reach Good

    return "Dead end"


def build_recommendations(bench_results, char_results, top_n_per_slot=3):
    equipped_data = {}
    for r in char_results:
        for slot, s in r["slots"].items():
            equipped_data[(r["name"], slot)] = s

    recommendations = []
    for b in bench_results:
        char_name, slot = b["character"], b["slot"]
        eq = equipped_data.get((char_name, slot))

        if not eq:
            continue

        eq_rolls = eq["roll_count"]

        verdict = determine_verdict(eq_rolls, b["expected_rolls"], b["good"], b["excellent"])

        if verdict == "Dead end" or b["max_rolls"] <= eq_rolls:
            continue

        b_copy = dict(b)
        b_copy["artifact_id"] = b.get("artifact_id")
        b_copy["verdict"] = verdict
        b_copy["equipped_rolls"] = eq_rolls

        # Add flag here to track if the character is upgrading their own equipped piece
        b_copy["is_self_equipped"] = (b.get("equipped_by") == char_name)

        recommendations.append(b_copy)

    grouped = defaultdict(list)
    for b in recommendations:
        grouped[(b["character"], b["slot"])].append(b)

    final_recs = []

    VERDICT_PRIORITY = {
        "Major Breakthrough": 4,
        "Patch / Fix": 3,
        "Luxury Upgrade": 2,
        "Minor Polish": 1
    }

    for (char_name, slot), candidates in grouped.items():
        candidates.sort(
            key=lambda x: (
                -VERDICT_PRIORITY.get(x["verdict"], 0),
                -(x["expected_rolls"] - x["equipped_rolls"]),
                -x["expected_rolls"],
                -x["max_rolls"]
            )
        )
        for b in candidates[:top_n_per_slot]:
            final_recs.append(b)

    final_recs.sort(
        key=lambda x: (
            -VERDICT_PRIORITY.get(x["verdict"], 0),  # Priority 1: upgrade impact
            -x["expected_rolls"],                    # Priority 2: realistic outcome
            -x["max_rolls"],                         # Priority 3: ceiling tie-breaker
            x["equipped_rolls"]                       # Priority 4: weakest equipped piece
        )
    )

    return final_recs


def build_ceiling_only_candidates(bench_results, char_results, top_n_per_slot=5):
    """
    Candidates whose optimistic ceiling (every remaining roll lands on a
    useful stat) beats what's equipped - the same check character_scoring's
    per-slot "upgradeable" flag uses - but whose expected value doesn't
    clear the Good/Excellent threshold. build_recommendations deliberately
    drops these ("Dead end" verdict) from the confirmed swap table, since
    recommending a piece unlikely to actually pay off isn't useful there.

    They're real possibilities, though, just higher-risk ones, so a
    consumer that wants to show everything with any upside (the character
    detail modal, tagged distinctly as "High Risk") can pull them from here
    instead - kept as a separate function rather than folding into
    build_recommendations so the primary recommendation table's stricter,
    EV-based standard doesn't quietly loosen.
    """
    equipped_data = {}
    for r in char_results:
        for slot, s in r["slots"].items():
            equipped_data[(r["name"], slot)] = s

    candidates = []
    for b in bench_results:
        char_name, slot = b["character"], b["slot"]
        eq = equipped_data.get((char_name, slot))

        if not eq:
            continue

        eq_rolls = eq["roll_count"]

        if b["max_rolls"] <= eq_rolls:
            continue  # no ceiling upside at all - not even a long shot

        verdict = determine_verdict(eq_rolls, b["expected_rolls"], b["good"], b["excellent"])
        if verdict != "Dead end":
            continue  # already surfaced as a confirmed recommendation

        b_copy = dict(b)
        b_copy["verdict"] = "High Risk"
        b_copy["equipped_rolls"] = eq_rolls
        b_copy["is_self_equipped"] = (b.get("equipped_by") == char_name)
        candidates.append(b_copy)

    grouped = defaultdict(list)
    for b in candidates:
        grouped[(b["character"], b["slot"])].append(b)

    final = []
    for (char_name, slot), group in grouped.items():
        # Ranked by ceiling gain over equipped, since expected value is by
        # definition not the differentiator for this tier.
        group.sort(key=lambda x: (-(x["max_rolls"] - x["equipped_rolls"]), -x["max_rolls"]))
        final.extend(group[:top_n_per_slot])

    return final

# ---- Leveling Recommendations (Batch 6, optimizer-driven as of Batch 9) ----

from leveling_efficiency import build_combined_leveling_plan


def generate_leveling_recommendations(
    bench_results: list,
    rules: dict,
    roll_values: dict,
    optimizer_candidates_by_char: dict = None,
    char_score_lookup: dict = None,
    char_slot_tier_lookup: dict = None,
    roll_value_by_char: dict = None,
    char_usage_lookup: dict = None,
) -> dict:
    """
    Generate a leveling plan.

    Primary signal is each candidate's build-optimality probability from the
    global optimizer (optimizer_candidates_by_char, keyed by character then
    slot - see score.py) rather than roll-count thresholds: contested slots
    (multiple pieces with comparable, unresolved probability) get cheap
    scouting steps, resolved slots get their winning piece committed to max
    level. Characters the optimizer has no data for fall back to the legacy
    threshold-based planner so nothing goes unrecommended.

    char_score_lookup (character name -> character_scoring.score_character's
    `score`) prioritizes which character's actions claim the budget first.

    char_slot_tier_lookup ((character name, slot) -> the slot's equipped
    status from character_scoring, e.g. "Needs Work"/"Good"/"Excellent"/
    "Missing") drives the tier-upgrade priority rule: an action only counts
    as high priority if its candidate can raise the slot's tier above what's
    currently equipped there (Needs Work -> Good/Excellent, Good ->
    Excellent; Excellent is already top tier so nothing beats it). Non-
    upgrading actions aren't blocked, they just sink to the bottom of the
    priority order. See leveling_efficiency.tier_upgrade_ok for the exact
    rule and rules.yaml's leveling.require_tier_upgrade to toggle it off.

    Args:
        bench_results: Output from bench.find_bench_potential. Used only for
            the legacy fallback path now.
        rules: The full rules.yaml dict (must contain 'budget' and 'leveling' sections).
        roll_values: From roll_values.yaml (kept for API compatibility).
        optimizer_candidates_by_char: score.py's optimizer_candidates_by_char.
            Pass {} or None to use the legacy planner for every character.
        char_score_lookup: {character_name: score}, typically built from
            char_results after score_character has run for the roster.
        char_slot_tier_lookup: {(character_name, slot): equipped tier status},
            typically built from char_results after score_character has run.
        roll_value_by_char: {character_name: {substat_key: estimated damage
            from one average roll}}, from value_per_roll.py, typically built
            alongside char_results. Feeds the optimizer planner's explore-vs-
            exploit terminal check (leveling_efficiency._decide_slot_action)
            with a build-aware sense of what a hidden roll is actually
            worth, instead of treating every substat as equally valuable.
        char_usage_lookup: {character_name: "Active"/"IT Only"}, typically
            built from the roster. Drives the IT Only max-level cap
            (rules.yaml leveling.it_only_max_level) and the Active-only
            toggle (rules.yaml leveling.active_chars_only).

    Returns:
        dict: actions + summary, with additional formatting hints for display.
    """
    budget_config = rules.get("budget", {})
    leveling_config = rules.get("leveling", {})
    optimizer_candidates_by_char = optimizer_candidates_by_char or {}
    char_score_lookup = char_score_lookup or {}
    char_slot_tier_lookup = char_slot_tier_lookup or {}
    roll_value_by_char = roll_value_by_char or {}

    plan = build_combined_leveling_plan(
        bench_results=bench_results,
        optimizer_candidates_by_char=optimizer_candidates_by_char,
        char_score_lookup=char_score_lookup,
        budget_config=budget_config,
        leveling_config=leveling_config,
        char_slot_tier_lookup=char_slot_tier_lookup,
        roll_value_by_char=roll_value_by_char,
        char_usage_lookup=char_usage_lookup,
    )

    # Add a display-friendly version of the actions
    for action in plan["actions"]:
        action["immediate_cost_str"] = (
            f"{action['immediate_cost']['mora']:,} Mora, "
            f"{action['immediate_cost']['exp']:,} EXP"
        )
        action["finish_cost_str"] = (
            f"{action['finish_cost']['mora']:,} Mora, "
            f"{action['finish_cost']['exp']:,} EXP"
        )
        if action.get("action_type") == "Legacy":
            action["probability_str"] = "—"
            action["efficiency_str"] = f"{action.get('efficiency_mora', 0):.2e} Δp/Mora"
        else:
            action["probability_str"] = f"{action.get('probability', 0.0) * 100:.0f}%"
            action["efficiency_str"] = f"{action.get('priority', 0):.2e} priority/Mora"
            # Explore-vs-exploit diagnostics (see leveling_efficiency.
            # _decide_slot_action) - not present on Legacy actions, since
            # the legacy planner doesn't run that decision.
            if action.get("expected_waste_mora") is not None:
                action["expected_waste_str"] = f"{action['expected_waste_mora']:,.0f} Mora"
            if action.get("scout_cost_mora") is not None:
                action["scout_cost_str"] = f"{action['scout_cost_mora']:,.0f} Mora"
            else:
                action["scout_cost_str"] = "—"  # nothing left to scout
            if action.get("expected_damage_gain") is not None:
                action["expected_damage_gain_str"] = f"{action['expected_damage_gain']:,.0f} dmg"

    return plan