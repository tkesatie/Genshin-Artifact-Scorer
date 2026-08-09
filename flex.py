from artifact_utils import SLOT_MAP, valid_main_stat
from bench import SET_ALIASES, expected_useful_rolls

ALL_SLOTS = ["Flower", "Feather", "Sands", "Goblet", "Circlet"]


def is_four_piece_locked(cfg):
    """True if the character wants a single dedicated 4pc set. 2pc/2pc or
    other split builds are excluded from flex consideration for now."""
    return "/" not in str(cfg.get("set", ""))


def _equipped_set_keys_by_slot(good_json, char_name):
    """{slot: setKey} for this character's currently-equipped pieces."""
    out = {}
    for art in good_json.get("artifacts", []):
        if art.get("location") != char_name:
            continue
        slot = SLOT_MAP.get(art.get("slotKey"))
        if slot:
            out[slot] = art.get("setKey")
    return out


def eligible_flex_slots(good_json, char_name, own_set_keys, matching):
    """
    Which slots can be flexed to an off-set piece without dropping the
    character below 4pc, given `matching` (set_status["matching"] - the
    count of the character's currently-equipped pieces that are already
    from the target set):

      - matching >= 5: every slot is eligible. The character is already
        wearing all 5 from the target set, so swapping any single one
        still leaves 4 in-set.
      - matching == 4: only the single off-set/missing slot is eligible -
        the normal case (4pc is already active via the other four slots;
        the 5th slot is genuinely free to consider off-set options for).
      - matching < 4: no slot is eligible. The 4pc bonus isn't active yet,
        so there's nothing to safely flex without costing the character
        the bonus flexing is supposed to preserve - they need to finish
        the set first (bench.py's job), not weigh off-set trades against
        a bonus they don't have.
    """
    if matching >= 5:
        return set(ALL_SLOTS)
    if matching == 4:
        equipped_keys = _equipped_set_keys_by_slot(good_json, char_name)
        off_slot = next(
            (s for s in ALL_SLOTS if equipped_keys.get(s) not in own_set_keys),
            None,
        )
        return {off_slot} if off_slot else set()
    return set()


def weakest_equipped_slot(char_result, eligible_slots):
    """(slot, roll_count) for the least-rolled slot among eligible_slots
    only - never a slot whose removal would break the 4pc bonus."""
    slots = {s: info for s, info in char_result["slots"].items() if s in eligible_slots}
    if not slots:
        return None, None
    slot, info = min(slots.items(), key=lambda kv: kv[1]["roll_count"])
    return slot, info["roll_count"]


def find_flex_candidates(good_json, roster, char_results, roll_values, min_ev_gain=2.0):
    """
    For 4pc-locked characters only, checks whether an off-set artifact beats
    the character's weakest FLEXABLE equipped slot by enough to be worth
    flexing. A slot only counts as flexable if the character's other four
    slots are (or would remain) fully in-set - see eligible_flex_slots -
    so the 4pc bonus is always preserved, and characters who haven't
    completed their set yet are skipped entirely (finishing the set is
    bench.py's job, not flex's). Off-set candidates match on slot + valid
    main stat, setKey is ignored entirely.

    NOTE: this does not price in the value of the 4pc set bonus itself
    (i.e. how much raw damage the bonus is worth vs. the substat gain),
    only substat EV for the one slot being considered. min_ev_gain is a
    blunt proxy for "worth making this trade" - tune it up if you're
    seeing bad flex suggestions.
    """
    flex_flags = []

    for r in char_results:
        char_name = r["name"]
        cfg = roster[char_name]

        if not is_four_piece_locked(cfg):
            continue

        own_set_keys = set(SET_ALIASES.get(cfg.get("set"), [cfg.get("set")]))
        matching = r.get("set_status", {}).get("matching") or 0
        eligible_slots = eligible_flex_slots(good_json, char_name, own_set_keys, matching)
        if not eligible_slots:
            continue

        slot, equipped_rolls = weakest_equipped_slot(r, eligible_slots)
        if slot is None:
            continue
        useful_stats = [str(s) for s in cfg["useful_stats"]]

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