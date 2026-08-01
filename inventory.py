from artifact_utils import SLOT_MAP, valid_main_stat
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


def excellence_bar(fit):
    """The ceiling this fit must clear to be worth leveling. In-set pieces
    just need to reach excellent, like any equipped piece would. Off-set
    pieces have to clear excellent by one more roll - an off-piece is only
    worth breaking a set for if it'd plausibly be the best piece on that
    character outright, not merely a piece that happens to be excellent."""
    return fit["excellent"] if fit["in_set"] else fit["excellent"] + 1


def classify_inventory_artifact(artifact, fits):
    """
    Ignores what's currently equipped - the question is whether this piece
    has real excellent-tier upside on its own merits for ANY valid roster
    character, not just whether it happens to beat what's equipped today.

    Returns:
        REVIEW          - ceiling clears the excellence bar for at least
                           one valid character (in-set: >= excellent,
                           off-set: >= excellent + 1), check manually
        SAFE_STRONGBOX  - no valid roster user, OR no fit clears its bar,
                           AND level 0 (no EXP lost)
        SANCTIFY_ELIXIR - no valid roster user, OR no fit clears its bar,
                           BUT level 1+ (EXP sunk)
    """
    level = artifact.get("level", 0)

    # fits is already ranked by ceiling (in-set breaking ties), so the
    # first fit that clears its bar is the strongest legitimate case
    clearing = [f for f in fits if f["ceiling"] >= excellence_bar(f)]

    if clearing:
        best = clearing[0]
        char, in_set = best["character"], best["in_set"]
        set_note = "" if in_set else " (off-set)"
        return {
            "action": "REVIEW",
            "reason": f"Ceiling {best['ceiling']} clears {char}'s excellent bar ({excellence_bar(best)}){set_note}",
            "users": [f["character"] for f in clearing],
        }

    if fits:
        top = fits[0]
        char, in_set = top["character"], top["in_set"]
        set_note = "" if in_set else " (off-set)"
        reason = f"Ceiling {top['ceiling']} does not clear {char}'s excellent bar ({excellence_bar(top)}){set_note}"
        users = [char]
    else:
        reason = "No roster character's main-stat config allows this slot's main stat"
        users = []

    if level == 0:
        return {"action": "SAFE_STRONGBOX", "reason": f"{reason}, no EXP invested", "users": users}
    return {"action": "SANCTIFY_ELIXIR", "reason": f"{reason}, EXP already sunk", "users": users}