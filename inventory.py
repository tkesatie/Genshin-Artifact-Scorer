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


def excellence_bar(fit, off_set_penalty=1):
    """The ceiling this fit must clear to be worth leveling. In-set pieces
    just need to reach excellent, like any equipped piece would. Off-set
    pieces have to clear excellent by `off_set_penalty` more rolls.
    """
    if fit["in_set"]:
        return fit["excellent"]
    return fit["excellent"] + off_set_penalty


def _classify_by_ceiling(artifact, fits, off_set_penalty=1):
    """
    Legacy ceiling-based classification. Used as a fallback when no
    cache entry exists for this artifact.
    """
    level = artifact.get("level", 0)

    clearing = [f for f in fits if f["expected_rolls"] >= excellence_bar(f, off_set_penalty)]

    if clearing:
        best = clearing[0]
        char, in_set = best["character"], best["in_set"]
        set_note = "" if in_set else " (off-set)"
        return {
            "action": "REVIEW",
            "reason": f"Ceiling {best['ceiling']} clears {char}'s excellent bar ({excellence_bar(best, off_set_penalty)}){set_note}",
            "users": [f["character"] for f in clearing],
        }

    if fits:
        top = fits[0]
        char, in_set = top["character"], top["in_set"]
        set_note = "" if in_set else " (off-set)"
        reason = f"Ceiling {top['ceiling']} does not clear {char}'s excellent bar ({excellence_bar(top, off_set_penalty)}){set_note}"
        users = [char]
    else:
        reason = "No roster character's main-stat config allows this slot's main stat"
        users = []

    if level == 0:
        return {"action": "SAFE_STRONGBOX", "reason": f"{reason}, no EXP invested", "users": users}
    return {"action": "SANCTIFY_ELIXIR", "reason": f"{reason}, EXP already sunk", "users": users}


def classify_inventory_artifact(artifact, fits, prob_cache=None, inventory_config=None):
    """
    Classify an unequipped artifact.

    If `prob_cache` is provided and contains this artifact, use the cached
    maximum build-optimality probability across all character runs.

    Otherwise, fall back to the ceiling-based logic.
    """
    artifact_id = artifact.get("id")
    if prob_cache is not None and artifact_id is not None:
        artifact_id = str(artifact_id)
        entry = prob_cache.get(artifact_id, None)
        # Cache entries are {"prob": ..., "fp": ...}; legacy flat floats still work.
        max_prob = entry.get("prob") if isinstance(entry, dict) else entry
        if max_prob is not None:
            # Read thresholds from config, or use defaults.
            config = inventory_config or {}
            safe = config.get("strongbox_threshold", 0.0)
            medium = config.get("medium_risk_threshold", 0.10)
            review = config.get("review_threshold", 0.50)
            keep = config.get("keep_threshold", 0.50)

            if max_prob <= safe:
                if artifact.get("level", 0) >= 1:
                    return {"action": "SANCTIFY_ELIXIR",
                            "reason": "0% build optimality (never optimal), EXP already sunk — route to Elixir",
                            "users": []}
                return {"action": "SAFE_STRONGBOX", "reason": "0% build optimality (never optimal)", "users": []}
            elif max_prob < medium:
                if artifact.get("level", 0) >= 1:
                    return {"action": "SANCTIFY_ELIXIR",
                            "reason": f"Low build optimality ({max_prob:.1%}), EXP already sunk — route to Elixir",
                            "users": []}
                return {"action": "MEDIUM_RISK_STRONGBOX",
                        "reason": f"Low build optimality ({max_prob:.1%}) — strongbox if desperate",
                        "users": []}
            elif max_prob < review:
                return {"action": "REVIEW", "reason": f"Medium build optimality ({max_prob:.1%}) — manual check", "users": []}
            else:
                return {"action": "KEEP", "reason": f"High build optimality ({max_prob:.1%}) — keep this piece", "users": []}

    # Fallback: old ceiling logic.
    off_set_penalty = inventory_config.get("off_set_penalty", 1) if inventory_config else 1
    return _classify_by_ceiling(artifact, fits, off_set_penalty)