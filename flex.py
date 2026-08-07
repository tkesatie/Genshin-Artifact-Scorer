from artifact_utils import SLOT_MAP, valid_main_stat
from bench import SET_ALIASES, expected_useful_rolls


def is_four_piece_locked(cfg):
    """True if the character wants a single dedicated 4pc set. 2pc/2pc or
    other split builds are excluded from flex consideration for now."""
    return "/" not in str(cfg.get("set", ""))


def weakest_equipped_slot(char_result):
    """(slot, roll_count) for the character's least-rolled equipped slot."""
    slots = char_result["slots"]
    slot, info = min(slots.items(), key=lambda kv: kv[1]["roll_count"])
    return slot, info["roll_count"]


def find_flex_candidates(good_json, roster, char_results, roll_values, min_ev_gain=2.0):
    """
    For 4pc-locked characters only, checks whether an off-set artifact beats
    the character's weakest equipped slot by enough to be worth flexing.
    Only ever touches ONE slot - the other four stay in-set, so the 4pc
    bonus is preserved. Off-set candidates match on slot + valid main stat,
    setKey is ignored entirely.

    NOTE: this does not price in the value of the 4pc set bonus itself,
    only substat EV. min_ev_gain is a blunt proxy for "worth breaking the
    set" - tune it up if you're seeing bad flex suggestions.
    """
    flex_flags = []

    for r in char_results:
        char_name = r["name"]
        cfg = roster[char_name]

        if not is_four_piece_locked(cfg):
            continue

        slot, equipped_rolls = weakest_equipped_slot(r)
        useful_stats = [str(s) for s in cfg["useful_stats"]]
        own_set_keys = set(SET_ALIASES.get(cfg.get("set"), [cfg.get("set")]))

        best = None
        for art in good_json.get("artifacts", []):
            if SLOT_MAP.get(art.get("slotKey")) != slot:
                continue
            if art.get("setKey") in own_set_keys:
                continue  # in-set - bench.py's job, not flex's
            if art.get("location") not in (None, "", char_name):
                continue  # equipped by someone else
            if not valid_main_stat(art, cfg, slot):
                continue

            _, expected = expected_useful_rolls(art, useful_stats, roll_values)
            if best is None or expected > best["expected_rolls"]:
                best = {
                    "character": char_name, "slot": slot, "set": art.get("setKey"),
                    "expected_rolls": round(expected, 2), "equipped_rolls": equipped_rolls,
                    "level": art.get("level", 0), "rarity": art.get("rarity", 5),
                    "artifact_id": art.get("id"),
                }

        if best and (best["expected_rolls"] - equipped_rolls) >= min_ev_gain:
            flex_flags.append(best)

    return flex_flags