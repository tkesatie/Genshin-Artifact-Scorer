"""
Persistent cache for strongbox probabilities.
Tracks the highest build-optimality probability ever seen for each artifact.

Guarantees:
- Every run, `confirm_cache` verifies each cached piece still exists in the
  current inventory AND that the piece at its id is the same piece that was
  cached (via a level-independent content fingerprint). Artifact ids are
  positional indices reassigned each run, so this protects against a
  destroyed/strongboxed piece shifting every later id onto a different
  artifact.
- Entries are stored as {"prob": <float>, "fp": <sha1>} for JSON
  round-tripping. Legacy flat-float entries are migrated in place.
"""

import hashlib
import json
import os

CACHE_PATH = "strongbox_cache.json"


def artifact_fingerprint(artifact):
    """
    Stable, content-based identity for a piece of gear.

    Uses the fields that never change while a piece is leveled or equipped:
    set key, slot key, rarity, main stat key, and the substat
    (key, initialValue) pairs from both visible and unactivated substats.
    Level, current substat values, lock, and location are deliberately
    excluded, so investing EXP into a piece does not evict its cached
    best probability.

    Returns a hex sha1 string.
    """
    sub_entries = []
    for sub in (artifact.get("substats") or []) + (artifact.get("unactivatedSubstats") or []):
        key = str(sub.get("key"))
        try:
            initial = float(sub.get("initialValue", sub.get("value", 0.0)))
        except (TypeError, ValueError):
            initial = 0.0
        # repr keeps float formatting deterministic for identical numbers.
        sub_entries.append((key, repr(initial)))
    sub_entries.sort()

    identity = {
        "set": str(artifact.get("setKey")),
        "slot": str(artifact.get("slotKey")),
        "rarity": int(artifact.get("rarity", 0)),
        "main": str(artifact.get("mainStatKey")),
        "substats": sub_entries,
    }
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def load_cache():
    """Load the cache from disk. Returns empty dict if file doesn't exist."""
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_cache(cache):
    """Save the cache to disk."""
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def confirm_cache(cache, artifacts):
    """
    Confirm every cached piece still exists in the current inventory.

    Called every run, before the cache is used or extended. Drops entries
    whose artifact id is no longer in `artifacts` (piece destroyed), and
    drops entries whose stored fingerprint no longer matches the piece
    currently at that id (a different piece slid into a positional index
    because an earlier piece was removed). Surviving legacy flat-float
    entries are migrated to the {"prob", "fp"} shape in place.

    Args:
        cache: The existing cache dict (artifact_id -> float or {prob, fp}).
        artifacts: Current artifact list with positional 'id' fields assigned.

    Returns:
        The confirmed cache dict (mutated in place, also returned for convenience).
    """
    current = {}
    for art in artifacts or []:
        art_id = art.get("id")
        if art_id is not None:
            current[str(art_id)] = artifact_fingerprint(art)

    for art_id in list(cache.keys()):
        entry = cache[art_id]
        fp = current.get(art_id)
        if fp is None:
            # Piece no longer exists in the inventory.
            del cache[art_id]
        elif isinstance(entry, dict):
            if entry.get("fp") is not None and entry.get("fp") != fp:
                # Same id, but a different piece now occupies it.
                del cache[art_id]
            else:
                entry["fp"] = fp  # backfill/refresh fingerprint
        else:
            # Legacy flat-float entry -> migrate to the {"prob", "fp"} shape.
            cache[art_id] = {"prob": entry, "fp": fp}
    return cache


def update_cache(cache, prob_lookup, artifacts=None):
    """
    Merge new probabilities into the cache, keeping the max per artifact.

    Each entry is stored as {"prob": <max ever seen>, "fp": <fingerprint>}
    so a later confirm_cache can prove the piece still exists. When
    `artifacts` is provided, the fingerprint is computed from the current
    piece at that id; legacy flat-float entries are treated as the existing
    probability.

    Args:
        cache: The existing cache dict (artifact_id -> float or {prob, fp}).
        prob_lookup: Dict from the current optimizer run (artifact_id -> float).
        artifacts: Optional current artifact list (with 'id' fields) used to
            stamp fingerprints.

    Returns:
        The updated cache dict (mutated in place, also returned for convenience).
    """
    fp_lookup = {}
    if artifacts is not None:
        for art in artifacts or []:
            art_id = art.get("id")
            if art_id is not None:
                fp_lookup[str(art_id)] = artifact_fingerprint(art)

    for art_id, prob in prob_lookup.items():
        # Ensure keys are strings for JSON compatibility.
        art_id = str(art_id)
        entry = cache.get(art_id)
        old_prob = entry.get("prob") if isinstance(entry, dict) else entry
        # Only update if the new probability is higher.
        if old_prob is not None and old_prob >= prob:
            continue
        if isinstance(entry, dict):
            entry["prob"] = prob
        else:
            cache[art_id] = {"prob": prob}
        fp = fp_lookup.get(art_id)
        if fp is not None:
            cache[art_id]["fp"] = fp
    return cache