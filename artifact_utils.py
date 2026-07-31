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


def roll_count_for_artifact(artifact, useful_stats, roll_values, rarity):
    """Estimate total useful rolls on this artifact.

    Real substats only ever land on one of ~4 discrete per-roll values, so the
    true roll count for any single substat is always a whole number. Dividing
    by the average roll value gives a noisy estimate (e.g. 1.89 instead of 2)
    because a roll can land below or above that average - so each substat's
    estimate is rounded to the nearest whole roll before summing, rather than
    left as a raw fraction.
    """
    table = roll_values["five_star"] if rarity >= 5 else roll_values["four_star"]
    total = 0
    for sub in artifact.get("substats", []):
        key = sub.get("key")
        val = sub.get("value", 0)
        label = STAT_LABEL.get(key)
        if label and label in useful_stats and key in table and table[key] > 0:
            total += round(val / table[key])

    return total


def effective_useful_pool(main_stat_key, useful_stats):
    main_label = STAT_LABEL.get(main_stat_key)
    pool = len(useful_stats)
    if main_label and main_label in useful_stats:
        pool -= 1
    return pool


def valid_main_stat(artifact, cfg, slot):
    """Check whether an artifact has an acceptable main stat for this character slot."""
    allowed = cfg.get("main_stats", {}).get(slot, [])
    if not allowed:
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
