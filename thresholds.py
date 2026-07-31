"""
Module: thresholds

Purpose:
The `thresholds` module is responsible for calculating various threshold values based on given rules, usage, role, slot, effective pool, and character name. It provides the logic to adjust these thresholds according to predefined rules and overrides.

Responsibilities:
1. **Stat Pool Adjustment**: Determine the appropriate adjustments to the stat pool based on the number of stats available.
2. **Threshold Calculation**: Compute both "good" and "excellent" thresholds by applying various adjustments including base thresholds, pool adjustments, slot-specific adjustments, and character-specific overrides.

Architectural Role:
The `thresholds` module serves as a utility layer within the application. It is expected to be used by higher-level modules that require threshold calculations for decision-making processes. This module does not handle user input or output directly but focuses on providing reusable logic for threshold computations.

Intended Dependencies:
- **rules**: A dictionary containing configuration rules for various thresholds and adjustments.
- **usage, role, slot, effective_pool, char_name**: Parameters used to compute the thresholds, which are expected to be provided by other modules in the application.

Boundaries:
- This module should not handle user interface logic or data storage operations. Its primary responsibility is to perform calculations based on the input parameters and predefined rules.
- Logic related to artifact parsing, scoring calculations, EV calculations, and recommendation logic should reside in other modules such as `score.py`.

Public API:
- **stat_pool_adjustment(rules, effective_pool)**: Adjusts the stat pool based on the number of stats available.
- **compute_thresholds(rules, usage, role, slot, effective_pool, char_name)**: Computes the "good" and "excellent" thresholds by applying various adjustments.

"""

def stat_pool_adjustment(rules, effective_pool):
    """Approximate-match against stat_pool_adjustment, like the sheet's VLOOKUP(...,TRUE)."""
    buckets = sorted(rules["stat_pool_adjustment"], key=lambda b: b["stat_count"])
    chosen = buckets[0]
    for b in buckets:
        if b["stat_count"] <= effective_pool:
            chosen = b
        else:
            break
    return chosen["good_adj"], chosen["excellent_adj"]


def compute_thresholds(rules, usage, role, slot, effective_pool, char_name):
    key = f"{usage}|{role}"
    base = rules["base_thresholds"][key]
    pool_good_adj, pool_exc_adj = stat_pool_adjustment(rules, effective_pool)
    slot_adj = rules["slot_adjustment"][slot]
    override = rules["character_overrides"].get(char_name, {})

    good = (base["good"] + pool_good_adj + slot_adj["good_adj"]
            + override.get("good_adj", 0))
    excellent = (base["excellent"] + pool_exc_adj + slot_adj["excellent_adj"]
                 + override.get("excellent_adj", 0))
    return good, excellent
