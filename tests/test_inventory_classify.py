"""
Unit tests for inventory.py's cache-based strongbox/elixir classification,
focused on the leveled-piece fix: pieces with EXP invested (level >= 1) that
would otherwise be classified as strongbox fodder must be routed to
SANCTIFY_ELIXIR instead, matching the in-game economy (elixir salvages the
invested EXP, strongboxing would waste it).

Run with: pytest tests/test_inventory_classify.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from inventory import classify_inventory_artifact


CONFIG = {
    "strongbox_threshold": 0.0,
    "medium_risk_threshold": 0.10,
    "review_threshold": 0.50,
    "keep_threshold": 0.50,
    "off_set_penalty": 1,
}


def classify(level, prob, artifact_id="a1"):
    art = {
        "id": artifact_id,
        "level": level,
        "setKey": "DeepwoodMemories",
        "slotKey": "flower",
    }
    return classify_inventory_artifact(
        art, [], prob_cache={artifact_id: prob}, inventory_config=CONFIG
    )


def test_leveled_zero_prob_routes_to_elixir():
    assert classify(level=20, prob=0.0)["action"] == "SANCTIFY_ELIXIR"


def test_level_zero_zero_prob_stays_safe_strongbox():
    assert classify(level=0, prob=0.0)["action"] == "SAFE_STRONGBOX"


def test_leveled_low_prob_routes_to_elixir():
    assert classify(level=16, prob=0.05)["action"] == "SANCTIFY_ELIXIR"


def test_level_zero_low_prob_stays_medium_risk_strongbox():
    assert classify(level=0, prob=0.05)["action"] == "MEDIUM_RISK_STRONGBOX"


def test_review_and_keep_unchanged_for_leveled_pieces():
    assert classify(level=20, prob=0.30)["action"] == "REVIEW"
    assert classify(level=20, prob=0.90)["action"] == "KEEP"


def test_leveled_elixir_reason_mentions_exp_already_sunk():
    assert "EXP already sunk" in classify(level=20, prob=0.0)["reason"]
    assert "EXP already sunk" in classify(level=16, prob=0.05)["reason"]


def test_no_cache_hit_falls_back_to_ceiling_elixir_for_leveled():
    art = {"id": "not-in-cache", "level": 20, "setKey": "DeepwoodMemories", "slotKey": "flower"}
    out = classify_inventory_artifact(
        art, [], prob_cache={"other": 0.0}, inventory_config=CONFIG
    )
    assert out["action"] == "SANCTIFY_ELIXIR"


def test_artifact_without_id_uses_ceiling_for_leveled():
    art = {"level": 20, "setKey": "DeepwoodMemories", "slotKey": "flower"}  # no id key
    out = classify_inventory_artifact(
        art, [], prob_cache={"a1": 0.0}, inventory_config=CONFIG
    )
    assert out["action"] == "SANCTIFY_ELIXIR"


def test_artifact_without_id_level_zero_stays_safe_strongbox():
    art = {"level": 0, "setKey": "DeepwoodMemories", "slotKey": "flower"}
    out = classify_inventory_artifact(
        art, [], prob_cache={"a1": 0.0}, inventory_config=CONFIG
    )
    assert out["action"] == "SAFE_STRONGBOX"