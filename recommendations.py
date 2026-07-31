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
