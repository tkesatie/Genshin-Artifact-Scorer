# candidate_generation.py
from artifact_utils import SLOT_MAP, STAT_LABEL, valid_main_stat
from collections import defaultdict

def inherent_value(artifact: dict, useful_stats: list, roll_values: dict) -> float:
    """
    Sums the raw values of substats (active + hidden) that are in useful_stats,
    divided by the average roll value for that stat.
    """
    total = 0.0
    for sub in artifact.get("substats", []) + artifact.get("unactivatedSubstats", []):
        key = sub.get("key")
        label = STAT_LABEL.get(key)
        if label in useful_stats:
            avg = sum(roll_values["five_star"].get(key, [0])) / len(roll_values["five_star"].get(key, [1]))
            total += sub.get("value", 0) / avg
    return total

def top_k_diversity_filter(unequipped_artifacts: list, slot: str, useful_stats: list,
                           roll_values: dict, k: int = 5) -> list:
    """
    Groups unequipped artifacts by mainStatKey, picks the highest inherent_value in each group,
    and selects a diverse set of up to k artifacts.
    """
    # Group by main stat key (for diversity)
    groups = defaultdict(list)
    for art in unequipped_artifacts:
        if SLOT_MAP.get(art.get("slotKey")) != slot:
            continue
        main_key = art.get("mainStatKey")
        groups[main_key].append(art)

    # Score each group's top artifact
    group_scores = []
    for main_key, arts in groups.items():
        best = max(arts, key=lambda a: inherent_value(a, useful_stats, roll_values))
        group_scores.append((main_key, best, inherent_value(best, useful_stats, roll_values)))

    # Sort groups by best value descending
    group_scores.sort(key=lambda x: x[2], reverse=True)

    selected = []
    # Force include top 2 from highest group, top 1 from 2nd, top 1 from 3rd
    if group_scores:
        # Highest group: take top 2
        highest_key = group_scores[0][0]
        top_arts = sorted([a for a in groups[highest_key] if a != group_scores[0][1]], 
                          key=lambda a: inherent_value(a, useful_stats, roll_values), reverse=True)
        selected.append(group_scores[0][1])
        if top_arts:
            selected.append(top_arts[0])
        # Second group
        if len(group_scores) > 1:
            selected.append(group_scores[1][1])
        # Third group
        if len(group_scores) > 2:
            selected.append(group_scores[2][1])

    # Backfill from highest group to reach k
    if len(selected) < k:
        remaining = [a for a in groups.get(highest_key, []) if a not in selected]
        remaining.sort(key=lambda a: inherent_value(a, useful_stats, roll_values), reverse=True)
        selected.extend(remaining[:k - len(selected)])

    return selected[:k]