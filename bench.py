"""
Module: bench.py

Purpose:
This module is responsible for evaluating the potential upgrade value of artifacts that are currently on the bench (i.e., not equipped by any character). It helps in identifying which artifacts have the most potential to improve when upgraded, based on their current state and the characters who could benefit from them.

Responsibilities:
1. **Artifact Evaluation**: Assessing the upgrade potential of each artifact on the bench.
2. **Character Matching**: Determining which characters would benefit from a given artifact set.
3. **Statistical Calculations**: Computing expected and maximum possible rolls for useful substats.
4. **Thresholds Calculation**: Comparing the artifact's potential to predefined thresholds (Good, Excellent) to determine its upgrade value.

Architectural Role:
This module serves as a business logic layer within the application. It is responsible for processing raw data from artifacts and providing meaningful insights about their upgrade potential. Higher-level modules, such as orchestrators or presentation layers, will use this module to make decisions based on the artifact evaluation results.

Intended Dependencies:
- **artifact_utils**: Provides utility functions related to artifact parsing and roll calculations.
- **thresholds**: Contains logic for computing thresholds that determine the quality of an artifact's stats.
- **roster.yaml**: Configuration file containing character-specific settings, including which sets they use and what stats are considered useful.

Boundaries:
This module should not handle user input or output. It focuses solely on processing artifact data and providing evaluation results. Any UI-related logic or data fetching should be handled by other modules.

Public API:
- `find_bench_potential(good_json, roster, rules, roll_values)`: Evaluates the upgrade potential of all artifacts on the bench.
- `bench_expected_lookup(bench_results)`: Provides a lookup table for the best expected bench value per character and slot.
- `bench_candidates_lookup(bench_results)`: Returns a list of valid bench candidates (excluding dead ends) for each character and slot.

"""

from collections import defaultdict

from artifact_utils import (
    MAX_LEVEL,
    SLOT_MAP,
    STAT_LABEL,
    effective_useful_pool,
    roll_count_for_artifact,
    valid_main_stat,
)
from thresholds import compute_thresholds


# Artifact upgrade behavior:
# - Every +4 levels gives one substat upgrade
# - If a 5-star starts with 3 lines, first upgrade unlocks the 4th line
# - Once all lines are active, upgrades are distributed randomly
# - EV assumes useful stats are the same probability as the current artifact state

UPGRADE_EVENTS = {
    5: 5,   # +4, +8, +12, +16, +20
    4: 4,
    3: 3,
    2: 2,
    1: 1,
}

# Your roster.yaml's "set" field is a short label (e.g. "Scroll", "VV"), but the
# real GOOD export's setKey is the full internal name (e.g. "NighttimeWhispersInTheStill").
# This maps short labels -> the setKey(s) Irminsul actually writes, so bench
# artifacts can be matched to the characters who'd want them.
# CHECK THIS against your own export - setKey spelling varies by exporter/version,
# and any set not listed here just won't get matched to a character.
SET_ALIASES = {
    "Galleries": ["FinaleOfTheDeepGalleries"],  # placeholder - verify against your export
    "Scroll": ["ScrollOfTheHeroOfCinderCity"],
    "Obsidian": ["ObsidianCodex"],
    "Night Sky": ["NightOfTheSkysUnveiling"],
    "TotM": ["TenacityOfTheMillelith"],
    "NO": ["NoblesseOblige"],
    "VV": ["ViridescentVenerer"],
    "SMS": ["SongOfDaysPast"],
    "Deepwood": ["DeepwoodMemories"],
    "Aubade": ["AubadeOfMorningstarAndMoon"],
    "Ocean-Hued Clam": ["OceanHuedClam"],
    "Gilded": ["GildedDreams"],
    "Emblem": ["EmblemOfSeveredFate"],
    "Petra": ["ArchaicPetra"],
    "Nighttime": ["NighttimeWhispersInTheStill"],
    "Instructor": ["Instructor"],
    "Golden Troupe": ["GoldenTroupe"],
    "Thundering Fury": ["ThunderingFury"],
    "ADayCarvedFromRisingWinds": ["ADayCarvedFromRisingWinds"],
    "Berserker": ["Berserker"],
    "CelestialGift": ["CelestialGift"],
    "DesertPavilionChronicle": ["DesertPavilionChronicle"],
    "DisenchantmentInDeepShadow": ["DisenchantmentInDeepShadow"],
    "FlowerOfParadiseLost": ["FlowerOfParadiseLost"],
    "Gambler": ["Gambler"],
    "GladiatorsFinale": ["GladiatorsFinale"],
    "Husk": ["HuskOfOpulentDreams"],
    "LongNightsOath": ["LongNightsOath"],
    "MaidenBeloved": ["MaidenBeloved"],
    "MarechausseeHunter": ["MarechausseeHunter"],
    "NoblesseOblige": ["NoblesseOblige"],
    "NymphsDream": ["NymphsDream"],
    "PaleFlame": ["PaleFlame"],
    "ShimenawasReminiscence": ["ShimenawasReminiscence"],
    "SilkenMoonsSerenade": ["SilkenMoonsSerenade"],
    "TheExile": ["TheExile"],
    "Thundersoother": ["Thundersoother"],
    "TinyMiracle": ["TinyMiracle"],
    "VourukashasGlow": ["VourukashasGlow"],
    "WanderersTroupe": ["WanderersTroupe"],
}


def matched_characters_for_set(set_key, roster):
    """Which roster characters would want an artifact with this setKey."""
    matches = []
    for name, cfg in roster.items():
        short_set = cfg.get("set")
        aliases = SET_ALIASES.get(short_set, [])
        if set_key == short_set or set_key in aliases:
            matches.append(name)
    return matches


def max_possible_useful_rolls(artifact, useful_stats, roll_values):
    """Optimistic ceiling: current useful rolls plus guaranteed hidden-line
    reveal (if useful) plus all remaining upgrades landing on useful stats."""
    rarity = artifact.get("rarity", 5)
    level = artifact.get("level", 0)
    max_level = MAX_LEVEL.get(rarity, 20)

    remaining_events = max(0, (max_level - level) // 4)

    current_rolls = roll_count_for_artifact(
        artifact,
        useful_stats,
        roll_values,
        rarity
    )

    hidden_subs = artifact.get("unactivatedSubstats", [])

    guaranteed_gain = 0

    # 3-line artifact: first upgrade reveals the known 4th line
    if hidden_subs and remaining_events > 0:
        hidden_label = STAT_LABEL.get(hidden_subs[0].get("key"))

        if hidden_label in useful_stats:
            guaranteed_gain += 1

        remaining_events -= 1

    # Remaining upgrades can theoretically all hit useful stats
    max_additional = guaranteed_gain + remaining_events

    return current_rolls, current_rolls + max_additional


def expected_useful_rolls(artifact, useful_stats, roll_values):
    """
    Expected useful rolls remaining from artifact upgrades.
    """

    rarity = artifact.get("rarity", 5)
    level = artifact.get("level", 0)
    max_level = MAX_LEVEL.get(rarity, 20)

    current_rolls = roll_count_for_artifact(
        artifact,
        useful_stats,
        roll_values,
        rarity
    )

    remaining_events = max(
        0,
        (max_level - level) // 4
    )

    active_subs = artifact.get("substats", [])
    hidden_subs = artifact.get("unactivatedSubstats", [])

    useful_active = sum(
        1
        for s in active_subs
        if STAT_LABEL.get(s.get("key")) in useful_stats
    )

    active_count = len(active_subs)

    expected_gain = 0

    # First event: unlock hidden line
    if hidden_subs and remaining_events > 0:
        hidden_useful = (
            STAT_LABEL.get(hidden_subs[0].get("key"))
            in useful_stats
        )

        if hidden_useful:
            expected_gain += 1
            useful_active += 1

        active_count += 1
        remaining_events -= 1

    # Remaining events are actual rolls
    if remaining_events > 0 and active_count > 0:
        useful_probability = useful_active / active_count
        expected_gain += remaining_events * useful_probability

    return (
        current_rolls,
        round(current_rolls + expected_gain, 2)
    )


def find_bench_potential(good_json, roster, rules, roll_values):
    """Under-max-level artifacts, evaluated against every roster character
    who uses that artifact set (including the character currently holding it)."""
    results = []
    for art in good_json.get("artifacts", []):
        rarity = art.get("rarity", 5)
        level = art.get("level", 0)
        if level >= MAX_LEVEL.get(rarity, 20):
            continue  # already maxed, nothing left to gain

        slot = SLOT_MAP.get(art.get("slotKey"))
        if slot is None:
            continue

        equipped_by = art.get("location")
        set_key = art.get("setKey")

        for char_name in matched_characters_for_set(set_key, roster):
            # If the artifact is equipped, ONLY evaluate it for the character holding it
            if equipped_by and equipped_by != char_name:
                continue

            cfg = roster[char_name]

            if not valid_main_stat(art, cfg, slot):
                continue

            useful_stats = [str(s) for s in cfg["useful_stats"]]
            eff_pool = effective_useful_pool(art.get("mainStatKey"), useful_stats)
            good, excellent = compute_thresholds(rules, cfg["usage"], cfg["role"], slot, eff_pool, char_name)
            current, ceiling = max_possible_useful_rolls(
                art,
                useful_stats,
                roll_values
            )

            _, expected = expected_useful_rolls(
                art,
                useful_stats,
                roll_values
            )

            if ceiling < good:
                verdict = "Dead end"
            elif ceiling < excellent:
                verdict = "Could reach Good"
            else:
                verdict = "Could reach Excellent"

            main_label = STAT_LABEL.get(art.get("mainStatKey"), art.get("mainStatKey"))

            substat_display = []
            for sub in art.get("substats", []):
                label = STAT_LABEL.get(sub.get("key"), sub.get("key"))
                is_useful = label in useful_stats
                substat_display.append((label, sub.get("value"), is_useful, False))

            for sub in art.get("unactivatedSubstats", []):
                label = STAT_LABEL.get(sub.get("key"), sub.get("key"))
                is_useful = label in useful_stats
                substat_display.append((label, sub.get("value"), is_useful, True))

            results.append({
                "character": char_name, "slot": slot, "set": set_key,
                "level": level, "rarity": rarity, "current_rolls": current,
                "max_rolls": ceiling, "expected_rolls": round(expected, 2), "good": good, "excellent": excellent,
                "verdict": verdict, "main_stat": main_label,
                "substats": substat_display,
                "levels_needed": MAX_LEVEL.get(rarity, 20) - level,
                "equipped_by": equipped_by  # track who holds it
            })

    results.sort(key=lambda r: (-r["max_rolls"], -r["current_rolls"]))
    return results


def bench_expected_lookup(bench_results):
    """(character, slot) -> best expected bench value."""
    lookup = {}

    for b in bench_results:
        key = (b["character"], b["slot"])

        if key not in lookup or b["expected_rolls"] > lookup[key]:
            lookup[key] = b["expected_rolls"]

    return lookup


def bench_candidates_lookup(bench_results):
    """(character, slot) -> list of valid bench candidate dicts (excluding Dead ends)."""
    lookup = defaultdict(list)
    for b in bench_results:
        if b["verdict"] != "Dead end":
            lookup[(b["character"], b["slot"])].append(b)
    return lookup
