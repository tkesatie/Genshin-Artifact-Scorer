"""
Module: artifact_utils

Purpose:
This module provides utility functions for parsing and analyzing artifacts within the Artifact Scorer project.
It serves as a central location for handling artifact-related logic, ensuring consistency and maintainability across the application.

Responsibilities:
- Manage the mapping between artifact slot keys and their corresponding labels.
- Calculate and estimate various metrics related to artifact substats and rolls.
- Validate main stats of artifacts based on character slots and configuration settings.
- Parse Good Export JSON data to organize equipped artifacts by character and slot.

Architectural Role:
This module acts as a utility library, providing foundational functions that are used by other modules in the project. It is designed to be independent of specific application logic, focusing solely on artifact-related tasks.

Intended Dependencies:
- `collections.defaultdict` for efficient data grouping.
- Configuration settings (`cfg`) for main stat validation, which should be provided by a higher-level module or configuration management system.

What Logic Does Not Belong Here:
- Application-specific business logic that involves multiple artifacts or characters.
- User interface components or presentation logic.
- Database interactions or data storage operations.
- External API calls or network-related tasks.

This module is intended to remain lightweight and focused on artifact parsing and analysis, ensuring that it can be easily maintained and reused across different parts of the project.
"""

from collections import defaultdict
from itertools import product

SLOT_MAP = {
    "flower": "Flower", "plume": "Feather", "sands": "Sands",
    "goblet": "Goblet", "circlet": "Circlet",
}

STAT_LABEL = {
    "critRate_": "CR", "critDMG_": "CD", "atk_": "ATK%", "hp_": "HP%",
    "def_": "DEF%", "enerRech_": "ER", "eleMas": "EM",
    "hp": "HP", "atk": "ATK", "def": "DEF", "heal_": "Heal%",
    "cryo_dmg_": "CryoDMG%", "pyro_dmg_": "PyroDMG%", "hydro_dmg_": "HydroDMG%",
    "electro_dmg_": "ElectroDMG%", "anemo_dmg_": "AnemoDMG%", "geo_dmg_": "GeoDMG%", "dendro_dmg_": "DendroDMG%"
}

MAX_LEVEL = {5: 20, 4: 16, 3: 12, 2: 8, 1: 4}


def all_substats(artifact):
    """Return activated and hidden substats together."""
    return (
        artifact.get("substats", [])
        + artifact.get("unactivatedSubstats", [])
    )


def possible_substat_rolls(substat, possible_rolls, tolerance=None):
    initial = substat.get("initialValue", 0)
    current = substat.get("value", 0)
    increase = current - initial

    key = substat.get("key")

    if tolerance is None:
        if key in ("hp", "atk", "def"):
            tolerance = 1.5
        elif key == "eleMas":
            tolerance = 1.0
        else:
            tolerance = 0.15

    if abs(increase) < tolerance:
        return [
            {
                "rolls": 0,
                "error": 0
            }
        ]

    solutions = []

    for upgrades in range(1, 6):
        for combo in product(possible_rolls, repeat=upgrades):
            error = abs(sum(combo) - increase)

            if error <= tolerance:
                solutions.append({
                    "rolls": upgrades,
                    "error": error
                })

    best_by_count = {}

    for solution in solutions:
        rolls = solution["rolls"]

        if (
            rolls not in best_by_count
            or solution["error"] < best_by_count[rolls]["error"]
        ):
            best_by_count[rolls] = solution

    return list(best_by_count.values())

def resolve_artifact_rolls(artifact, roll_values):
    """
    Resolve actual upgrade rolls across the entire artifact using GOOD's
    totalRolls as the constraint.

    Returns a dictionary:
        {
            "substat_rolls": {key: rolls},
            "total_upgrade_rolls": N
        }
    """

    rarity = artifact.get("rarity", 5)

    table = (
        roll_values["five_star"]
        if rarity >= 5
        else roll_values["four_star"]
    )

    substats = artifact.get("substats", [])

    upgrade_budget = (
        artifact.get("totalRolls", len(substats))
        - len(substats)
    )

    possibilities = []

    for sub in substats:
        options = possible_substat_rolls(
            sub,
            table[sub["key"]]
        )

        possibilities.append({
            "key": sub["key"],
            "options": options
        })

    # Search combinations of possible solutions
    best = None

    option_lists = [
        p["options"]
        for p in possibilities
    ]

    for combination in product(*option_lists):
        total_upgrades = sum(
            option["rolls"]
            for option in combination
        )

        if total_upgrades != upgrade_budget:
            continue

        total_error = sum(
            option["error"]
            for option in combination
        )

        candidate = {
            "error": total_error,
            "rolls": {
                possibilities[i]["key"]: combination[i]["rolls"]
                for i in range(len(possibilities))
            }
        }

        if (
            best is None
            or candidate["error"] < best["error"]
        ):
            best = candidate

    return best


def roll_count_for_artifact(artifact, useful_stats, roll_values):
    """
    Count useful substat rolls using artifact-level roll resolution.

    GOOD totalRolls is treated as the source of truth. The resolver determines
    how those upgrade rolls were distributed across substats while accounting
    for rounding ambiguity. Each substat also carries 1 "base" roll simply for
    being one of the artifact's initial lines, on top of any upgrade rolls
    resolved for it - resolve_artifact_rolls only measures value gained past
    that initial roll, so it has to be added back in here.
    """
    substats = artifact.get("substats", [])

    base_rolls = sum(
        1
        for s in substats
        if STAT_LABEL.get(s.get("key")) in useful_stats
    )

    resolved = resolve_artifact_rolls(
        artifact,
        roll_values
    )

    if resolved is not None:
        upgrade_rolls = sum(
            rolls
            for key, rolls in resolved["rolls"].items()
            if STAT_LABEL.get(key) in useful_stats
        )
        return base_rolls + upgrade_rolls

    # No combination of per-substat rolls summed to exactly match totalRolls
    # (missing/bad totalRolls, tolerance mismatch, etc). Rather than treating
    # the artifact as having zero upgrades, fall back to each useful
    # substat's own best-fit roll estimate from its observed initial->current
    # growth, ignoring whether the total reconciles against totalRolls. A
    # substat that visibly grew still gets credit even when the artifact-level
    # budget doesn't add up.
    rarity = artifact.get("rarity", 5)
    table = (
        roll_values["five_star"]
        if rarity >= 5
        else roll_values["four_star"]
    )

    upgrade_rolls = 0

    for sub in substats:
        label = STAT_LABEL.get(sub.get("key"))
        if label not in useful_stats:
            continue

        options = possible_substat_rolls(sub, table[sub["key"]])

        if options:
            best = min(options, key=lambda o: o["error"])
            upgrade_rolls += best["rolls"]
        else:
            # Even the single-substat search found no 1-5 roll combination
            # within tolerance (e.g. an odd rounding artifact in the export).
            # Fall back to a coarse estimate: raw increase divided by the
            # average possible roll value, clamped to a sane 0-5 range,
            # so a substat with visible growth still contributes something
            # instead of silently contributing zero.
            possible_rolls = table[sub["key"]]
            increase = sub.get("value", 0) - sub.get("initialValue", 0)
            avg_roll = sum(possible_rolls) / len(possible_rolls)
            estimate = round(increase / avg_roll) if avg_roll else 0
            upgrade_rolls += max(0, min(5, estimate))

    return base_rolls + upgrade_rolls

def effective_useful_pool(main_stat_key, useful_stats):
    main_label = STAT_LABEL.get(main_stat_key)
    pool = len(useful_stats)
    if main_label and main_label in useful_stats:
        pool -= 1
    return pool


def valid_main_stat(artifact, cfg, slot):
    """Check whether an artifact has an acceptable main stat for this character slot."""
    allowed = cfg.get("main_stats", {}).get(slot, [])
    if not allowed or "ANY" in allowed:
        return True

    main_label = STAT_LABEL.get(artifact.get("mainStatKey"))
    return main_label in allowed


def parse_good_export(good_json, roster):
    """Group equipped artifacts by (character, slot). Only equipped artifacts are scored."""
    by_char = defaultdict(dict)
    for art in good_json.get("artifacts", []):
        loc = art.get("location")
        if not loc or loc not in roster:
            continue
        slot = SLOT_MAP.get(art.get("slotKey"))
        if slot is None:
            continue
        by_char[loc][slot] = art
    return by_char