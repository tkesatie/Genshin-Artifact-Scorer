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


def total_rolls_so_far(artifact):
    """Deterministic (or safely-bounded) count of total roll-events across
    ALL active substats so far - derived purely from level/line-state, never
    from substat values. This is the actual constraint roll allocation must
    respect.

    Unambiguous when a hidden line is still waiting to be revealed: total =
    active_lines + events_used, since every event so far has been a pure
    increment.

    Ambiguous when 4 lines are active and none are hidden: the export can't
    tell us whether this piece started with 4 lines (all events were
    increments) or started with 3 and already revealed the 4th (one event
    was a reveal, not an increment). Those two histories differ by exactly
    1. We take the lower bound - it can undercount by 1 on a 4-line-native
    artifact, but it can never claim more rolls happened than the piece
    could physically have received.
    """
    level = artifact.get("level", 0)
    events_used = level // 4
    active_lines = len(artifact.get("substats", []))
    hidden = artifact.get("unactivatedSubstats", [])

    if hidden or active_lines < 4:
        return active_lines + events_used

    return active_lines - 1 + events_used  # conservative: assume a reveal already happened


def roll_count_for_artifact(artifact, useful_stats, roll_values, rarity):
    """
    Real substats only ever land on one of ~4 discrete per-roll values, so
    the true roll count for the whole artifact is a known integer (see
    total_rolls_so_far). Rounding each substat independently and summing
    ignores that shared budget and can invent rolls that never happened.
    Instead, allocate the artifact's actual roll budget across its active
    substats using the largest-remainder method, then report the useful
    slice of that allocation.
    """
    table = roll_values["five_star"] if rarity >= 5 else roll_values["four_star"]
    active = artifact.get("substats", [])
    if not active:
        return 0

    budget = total_rolls_so_far(artifact)

    entries = []
    for sub in active:
        key = sub.get("key")
        avg = table.get(key, 0)
        estimate = (sub.get("value", 0) / avg) if avg else 0.0
        entries.append({"label": STAT_LABEL.get(key), "floor": int(estimate), "frac": estimate - int(estimate)})

    diff = budget - sum(e["floor"] for e in entries)

    if diff > 0:
        # leftover budget goes to the substats closest to their next roll
        for e in sorted(entries, key=lambda e: -e["frac"])[:diff]:
            e["floor"] += 1
    elif diff < 0:
        # floors already overshoot the known budget - trim from the
        # substats with the weakest evidence for that extra roll first
        for e in sorted(entries, key=lambda e: e["frac"])[: -diff]:
            e["floor"] = max(0, e["floor"] - 1)

    return sum(e["floor"] for e in entries if e["label"] in useful_stats)


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