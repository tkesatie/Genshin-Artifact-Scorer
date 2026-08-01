from artifact_utils import SLOT_MAP, STAT_LABEL, valid_main_stat
from bench import matched_characters_for_set


def artifact_users(artifact, roster):
    """Find roster characters who could actually use this artifact."""
    set_key = artifact.get("setKey")
    slot = SLOT_MAP.get(artifact.get("slotKey"))

    users = []

    for name in matched_characters_for_set(set_key, roster):
        cfg = roster[name]

        if valid_main_stat(artifact, cfg, slot):
            users.append(name)

    return users


def strong_off_piece_candidate(artifact, ceiling, rules):
    """
    Conservative protection for rare high-value off-pieces.
    This intentionally starts broad and can be tightened later.
    """

    off_piece_rules = rules.get("off_piece_rules", {})

    protected = off_piece_rules.get(
        "protected_main_stats",
        []
    )

    main_stat = STAT_LABEL.get(
        artifact.get("mainStatKey")
    )

    if main_stat not in protected:
        return False

    minimum_ceiling = off_piece_rules.get(
        "minimum_ceiling",
        7
    )

    return ceiling >= minimum_ceiling


def classify_inventory_artifact(artifact, rules, ceiling, equipped_baseline=0, best_fit=None):
    """
    Returns:
        REVIEW          - beats the best-fit character's equipped piece, check manually
        SAFE_STRONGBOX  - no valid roster user, OR loses to equipped, AND level 0 (no EXP lost)
        SANCTIFY_ELIXIR - no valid roster user, OR loses to equipped, BUT level 1+ (EXP sunk)
    """
    level = artifact.get("level", 0)

    if best_fit is None:
        reason = "No roster character's main-stat config allows this slot's main stat"
        users = []
    else:
        char, in_set = best_fit["character"], best_fit["in_set"]
        set_note = "" if in_set else " (off-set)"
        if ceiling > equipped_baseline:
            return {
                "action": "REVIEW",
                "reason": f"Ceiling {ceiling} beats {char}'s equipped {equipped_baseline}{set_note}",
                "users": [char],
            }
        reason = f"Ceiling {ceiling} does not beat {char}'s equipped {equipped_baseline}{set_note}"
        users = [char]

    if level == 0:
        return {"action": "SAFE_STRONGBOX", "reason": f"{reason}, no EXP invested", "users": users}
    return {"action": "SANCTIFY_ELIXIR", "reason": f"{reason}, EXP already sunk", "users": users}