"""
Unit tests for render_html.py's Inventory Cleanup strongbox filter + ordering.

The Inventory Cleanup table shows only SAFE_STRONGBOX pieces, ordered by the
in-game artifact strongbox selector's set order (STRONGBOX_SET_ORDER), then by
slot (goblet, feather, circlet, flower, sands), then by the artifact's original
GOOD JSON order (which the stable sort preserves for equal (set, slot) rows).

Run with: pytest tests/test_render_inventory_sort.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from render_html import sort_inventory_for_display


def row(action, set_key, slot, artifact_id, visible_substats=0):
    art = {"setKey": set_key, "id": artifact_id}
    if visible_substats:
        art["substats"] = [{"key": "atk", "value": 1.0} for _ in range(visible_substats)]
    return {
        "action": action,
        "slot": slot,
        "ceiling": 9,
        "artifact": art,
    }


def ids(rows):
    return [r["artifact"]["id"] for r in rows]


def test_filters_to_safe_strongbox_only():
    rows = [
        row("SAFE_STRONGBOX", "EmblemOfSeveredFate", "Flower", 1),
        row("SANCTIFY_ELIXIR", "EmblemOfSeveredFate", "Sands", 2),
        row("MEDIUM_RISK_STRONGBOX", "EmblemOfSeveredFate", "Goblet", 3),
        row("REVIEW", "EmblemOfSeveredFate", "Circlet", 4),
        row("KEEP", "EmblemOfSeveredFate", "Feather", 5),
    ]
    assert ids(sort_inventory_for_display(rows)) == [1]


def test_set_order_follows_strongbox_list():
    # DeepwoodMemories is first in STRONGBOX_SET_ORDER, EmblemOfSeveredFate is
    # near the end, OceanHuedClam is last.
    rows = [
        row("SAFE_STRONGBOX", "OceanHuedClam", "Flower", 1),
        row("SAFE_STRONGBOX", "DeepwoodMemories", "Goblet", 2),
        row("SAFE_STRONGBOX", "EmblemOfSeveredFate", "Sands", 3),
    ]
    assert ids(sort_inventory_for_display(rows)) == [2, 3, 1]


def test_slot_order_within_set():
    # goblet, feather, circlet, flower, sands
    rows = [
        row("SAFE_STRONGBOX", "GildedDreams", "Sands", 1),
        row("SAFE_STRONGBOX", "GildedDreams", "Flower", 2),
        row("SAFE_STRONGBOX", "GildedDreams", "Feather", 3),
        row("SAFE_STRONGBOX", "GildedDreams", "Circlet", 4),
        row("SAFE_STRONGBOX", "GildedDreams", "Goblet", 5),
    ]
    assert ids(sort_inventory_for_display(rows)) == [5, 3, 4, 2, 1]


def test_json_order_is_stable_tiebreaker():
    rows = [
        row("SAFE_STRONGBOX", "PaleFlame", "Goblet", 1),
        row("SAFE_STRONGBOX", "PaleFlame", "Goblet", 2),
        row("SAFE_STRONGBOX", "PaleFlame", "Goblet", 3),
    ]
    assert ids(sort_inventory_for_display(rows)) == [1, 2, 3]


def test_three_liners_sort_before_four_liners_within_set_slot():
    # Matches in-game artifact list order: within the same set+slot, pieces
    # with fewer visible substats (3-line) are listed before 4-line ones.
    rows = [
        row("SAFE_STRONGBOX", "GildedDreams", "Feather", 1, visible_substats=4),
        row("SAFE_STRONGBOX", "GildedDreams", "Feather", 2, visible_substats=3),
        row("SAFE_STRONGBOX", "GildedDreams", "Feather", 3, visible_substats=3),
    ]
    # ids 2 and 3 are 3-liners -> sort before the 4-liner (id 1); ties keep
    # original JSON order.
    assert ids(sort_inventory_for_display(rows)) == [2, 3, 1]


def test_substat_count_does_not_cross_slot_or_set_boundaries():
    rows = [
        row("SAFE_STRONGBOX", "GildedDreams", "Sands", 1, visible_substats=3),
        row("SAFE_STRONGBOX", "DeepwoodMemories", "Goblet", 2, visible_substats=4),
    ]
    # A 4-line DeepwoodMemories goblet still beats a 3-line GildedDreams sands,
    # because set + slot sort before substat count.
    assert ids(sort_inventory_for_display(rows)) == [2, 1]


def test_unlisted_set_sorts_last():
    rows = [
        row("SAFE_STRONGBOX", "MaidenBeloved", "Goblet", 1),  # not in list
        row("SAFE_STRONGBOX", "DeepwoodMemories", "Goblet", 2),
    ]
    assert ids(sort_inventory_for_display(rows)) == [2, 1]


def test_unknown_slot_sorts_last_within_set():
    rows = [
        row("SAFE_STRONGBOX", "DeepwoodMemories", "Bracelet", 1),  # no such display label
        row("SAFE_STRONGBOX", "DeepwoodMemories", "Goblet", 2),
    ]
    assert ids(sort_inventory_for_display(rows)) == [2, 1]