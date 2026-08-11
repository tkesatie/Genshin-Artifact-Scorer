"""
Unit tests for strongbox_cache.py's per-run piece-existence guarantee.

Artifact ids are positional indices reassigned every run, so confirm_cache
must verify not just that an id still exists, but that the PIECE at that id is
the same piece that was cached (via a level-independent content fingerprint).

Run with: pytest tests/test_strongbox_cache.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from strongbox_cache import (
    artifact_fingerprint,
    confirm_cache,
    load_cache,
    save_cache,
    update_cache,
)
from inventory import classify_inventory_artifact


CONFIG = {
    "strongbox_threshold": 0.0,
    "medium_risk_threshold": 0.10,
    "review_threshold": 0.50,
    "keep_threshold": 0.50,
    "off_set_penalty": 1,
}


def make_artifact(art_id, main_key="atk", subs=None, level=0, set_key="ThunderingFury",
                  slot_key="plume", rarity=5):
    """Build a minimal artifact dict with a positional id."""
    return {
        "id": art_id,
        "setKey": set_key,
        "slotKey": slot_key,
        "level": level,
        "rarity": rarity,
        "mainStatKey": main_key,
        "substats": subs or [],
        "unactivatedSubstats": [],
    }


def test_fingerprint_stable_across_leveling():
    subs = [
        {"key": "critRate_", "value": 5.8, "initialValue": 3.1},
        {"key": "hp", "value": 209.0, "initialValue": 209.0},
    ]
    base = make_artifact(0, subs=subs, level=0)
    leveled = make_artifact(0, subs=subs, level=16)
    leveled["substats"][0]["value"] = 11.6  # an upgrade roll landed
    assert artifact_fingerprint(base) == artifact_fingerprint(leveled)


def test_fingerprint_differs_between_distinct_pieces():
    a = make_artifact(0, subs=[{"key": "atk", "value": 30.0, "initialValue": 30.0}])
    b = make_artifact(0, subs=[{"key": "hp", "value": 209.0, "initialValue": 209.0}])
    c = make_artifact(0, main_key="hp", subs=[{"key": "atk", "value": 30.0, "initialValue": 30.0}])
    assert artifact_fingerprint(a) != artifact_fingerprint(b)
    assert artifact_fingerprint(a) != artifact_fingerprint(c)


def test_confirm_drops_missing_id():
    a = make_artifact(0, main_key="atk")
    cache = {
        "0": {"prob": 0.9, "fp": artifact_fingerprint(a)},
        "1": {"prob": 0.1, "fp": "y"},
    }
    confirm_cache(cache, [a])  # only id 0 exists this run
    assert set(cache.keys()) == {"0"}
    assert cache["0"]["prob"] == 0.9


def test_confirm_drops_ids_pointing_at_a_different_piece():
    # Run 1: [A, B, C] at positional ids 0, 1, 2.
    a = make_artifact(0, main_key="atk")
    b = make_artifact(1, main_key="hp")
    c = make_artifact(2, main_key="em")
    cache = {
        "0": {"prob": 0.0, "fp": artifact_fingerprint(a)},
        "1": {"prob": 0.9, "fp": artifact_fingerprint(b)},
        "2": {"prob": 0.0, "fp": artifact_fingerprint(c)},
    }
    # Run 2: A was strongboxed; the export is now [B, C] with ids reassigned
    # 0, 1 — every id points at a different piece than before.
    b_now = dict(b, id=0)
    c_now = dict(c, id=1)
    confirm_cache(cache, [b_now, c_now])
    assert cache == {}  # nothing may be trusted: stale probs would flip advice


def test_confirm_keeps_matching_piece_and_refreshes_fp():
    cache = {"0": {"prob": 0.8, "fp": artifact_fingerprint(make_artifact(0, main_key="atk"))}}
    confirm_cache(cache, [make_artifact(0, main_key="atk")])
    assert cache["0"]["prob"] == 0.8


def test_confirm_keeps_leveled_version_of_same_piece():
    subs = [{"key": "atk", "value": 30.0, "initialValue": 30.0}]
    cache = {"0": {"prob": 0.8, "fp": artifact_fingerprint(make_artifact(0, subs=subs, level=0))}}
    leveled = make_artifact(0, subs=subs, level=16)
    leveled["substats"][0]["value"] = 74.6  # invested EXP, same piece
    confirm_cache(cache, [leveled])
    assert cache["0"]["prob"] == 0.8  # not evicted by leveling


def test_confirm_migrates_legacy_float_entries():
    a = make_artifact(0, main_key="atk")
    cache = {"0": 0.75}
    confirm_cache(cache, [a])
    assert cache["0"] == {"prob": 0.75, "fp": artifact_fingerprint(a)}


def test_update_cache_keeps_max_and_stamps_fingerprint():
    a = make_artifact(0, main_key="atk")
    fp = artifact_fingerprint(a)
    cache = {}
    update_cache(cache, {0: 0.3}, [a])
    assert cache["0"] == {"prob": 0.3, "fp": fp}
    update_cache(cache, {0: 0.6}, [a])
    assert cache["0"]["prob"] == 0.6
    update_cache(cache, {0: 0.4}, [a])  # lower prob never lowers the max
    assert cache["0"]["prob"] == 0.6
    assert cache["0"]["fp"] == fp


def test_update_cache_upgrades_legacy_float_entry():
    a = make_artifact(0, main_key="atk")
    cache = {"0": 0.5}
    update_cache(cache, {0: 0.7}, [a])
    assert cache["0"] == {"prob": 0.7, "fp": artifact_fingerprint(a)}


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("strongbox_cache.CACHE_PATH", str(tmp_path / "cache.json"))
    cache = {"0": {"prob": 0.9, "fp": "abc"}, "1": {"prob": 0.1, "fp": "def"}}
    save_cache(cache)
    assert load_cache() == cache


def test_score_flow_confirm_then_update():
    a0 = make_artifact(0, main_key="atk")
    b1 = make_artifact(1, main_key="hp")
    cache = {
        "0": {"prob": 0.9, "fp": artifact_fingerprint(a0)},
        "1": {"prob": 0.1, "fp": artifact_fingerprint(b1)},
    }
    # This run a0 is gone, so b1 now occupies positional id 0.
    b_now = dict(b1, id=0)
    cache = confirm_cache(cache, [b_now])
    assert cache == {}  # ids drifted -> nothing carried over
    cache = update_cache(cache, {0: 0.7}, [b_now])
    assert cache["0"] == {"prob": 0.7, "fp": artifact_fingerprint(b_now)}


def test_classify_accepts_dict_and_float_entries():
    art = {"id": "a1", "level": 0, "setKey": "DeepwoodMemories", "slotKey": "flower"}
    as_dict = classify_inventory_artifact(
        art, [], prob_cache={"a1": {"prob": 0.9, "fp": "x"}}, inventory_config=CONFIG
    )
    assert as_dict["action"] == "KEEP"
    as_float = classify_inventory_artifact(
        art, [], prob_cache={"a1": 0.9}, inventory_config=CONFIG
    )
    assert as_float["action"] == "KEEP"