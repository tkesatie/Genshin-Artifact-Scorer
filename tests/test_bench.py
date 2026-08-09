"""
Unit tests for bench.py's upgrade-potential roll math, focused on the
roll-pool ceiling fix: an artifact's "max_rolls" (optimistic ceiling) must
reflect the fact that +4 upgrade events can only ever land on the piece's
OWN substats (plus a revealed useful hidden line). A piece with no useful
substat in its pool can never gain a useful roll no matter how many events
remain - its ceiling is just its current roll count, and treating future
events as if they could all land useful overstates the piece badly enough
to flip its tier verdict (the Kuki flower case).

Run with: pytest tests/test_bench.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bench import max_possible_useful_rolls


ROLL_VALUES = {
    "five_star": {
        "atk": [10.0, 12.0, 14.0, 16.0],
        "def": [10.0, 12.0, 14.0, 16.0],
        "critRate_": [3.0, 3.5, 4.0, 4.5],
        "critDMG_": [6.0, 7.0, 8.0, 9.0],
        "eleMas": [16.0, 19.0, 21.0, 23.0],
    },
    "four_star": {},
}

# Mimics KukiShinobu's useful stats (roster.yaml): EM and HP%.
USEFUL = ["EM", "HP%"]


def substat(key, value=0.0):
    # value == initialValue == 0 -> no measured growth, so `current_rolls` is
    # exactly the count of useful LINES (each line carries one base roll),
    # keeping every expected number below an easy hand-check.
    return {"key": key, "value": value, "initialValue": value}


def make_artifact(level=0, rarity=5, substats=None, hidden=None):
    return {
        "id": "test-art",
        "rarity": rarity,
        "level": level,
        "substats": [substat(s) for s in (substats or [])],
        "unactivatedSubstats": [substat(h) for h in (hidden or [])],
    }


def test_no_useful_substats_ceiling_is_current_rolls():
    # Regression for the Kuki flower case: substats DEF/ATK/CR/CD, none useful
    # for a character who wants EM/HP%, and no hidden useful line to reveal.
    # Every future upgrade event lands on those four non-useful lines, so the
    # true ceiling is 0 - the old code claimed 0 + all remaining events (5 for
    # a level-0 5-star), which made the piece look able to reach Excellent.
    art = make_artifact(level=0, substats=["def", "atk", "critRate_", "critDMG_"])
    assert max_possible_useful_rolls(art, USEFUL, ROLL_VALUES) == (0, 0)


def test_no_useful_substats_ceiling_zero_at_partial_level():
    # Same piece already leveled to +4: 4 remaining events, still 0 useful.
    art = make_artifact(level=4, substats=["def", "atk", "critRate_", "critDMG_"])
    assert max_possible_useful_rolls(art, USEFUL, ROLL_VALUES) == (0, 0)


def test_non_useful_hidden_line_still_zero_ceiling():
    # 3-line piece whose hidden 4th line is ALSO non-useful: the reveal event
    # adds nothing and the pool stays all-useless, so the ceiling stays 0.
    art = make_artifact(level=0, substats=["def", "atk", "critRate_"], hidden=["critDMG_"])
    assert max_possible_useful_rolls(art, USEFUL, ROLL_VALUES) == (0, 0)


def test_useful_hidden_line_keeps_old_ceiling_behavior():
    # 3-line piece with a useful hidden line: the reveal guarantees +1 and
    # that line then joins the roll pool, so all remaining events can still
    # land useful. ceiling = current(0) + reveal(1) + events left(4) = 5.
    art = make_artifact(level=0, substats=["def", "atk", "critRate_"], hidden=["eleMas"])
    assert max_possible_useful_rolls(art, USEFUL, ROLL_VALUES) == (0, 5)


def test_useful_active_substat_keeps_old_ceiling_behavior():
    # One useful active substat (EM) -> every remaining event can roll onto it.
    # ceiling = current(1 base line) + all 5 remaining events = 6.
    art = make_artifact(level=0, substats=["eleMas", "def", "atk", "critRate_"])
    assert max_possible_useful_rolls(art, USEFUL, ROLL_VALUES) == (1, 6)