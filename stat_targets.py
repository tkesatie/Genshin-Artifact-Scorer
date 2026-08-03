"""
Module: stat_targets

Purpose:
Phases 1-2 of the future damage calculator (see DAMAGE_CALCULATOR_DESIGN.md):
configurable per-character stat targets, so the scorer can tell you when a
useful stat has already been over-invested and future artifacts should
prioritize something else instead - now with per-team overrides, since a
character's real requirement (ER especially) depends on which team you're
building them for.

Responsibilities:
1. Load manually-configured stat targets per character, plus optional
   per-team overrides (stat_targets.yaml).
2. Resolve, for each character, every applicable context: their default
   targets plus one merged target set per team that overrides them.
3. Sum a character's currently equipped stat totals (main stat + activated
   substats) for the stats that can be totaled without full character
   base-stat/weapon data: ER, CR, CD, EM, ATK%, HP%, DEF%.
4. Compare current totals against each context's targets and classify
   every configured stat as Under / Near / Exceeds target.
5. Surface a simple recommendation per (character, context): which useful
   stats are over target (stop farming these) and which are still under
   target (still worth chasing).

Architectural Role:
Sits alongside character_scoring.py as a second, independent lens on the
same equipped-artifact data (`artifacts_by_slot`) that score_character
already receives. It does not replace, modify, or depend on roll-count
scoring - a character can be "Farming" in character_scoring's terms and
still have a stat that's already over its configured target here.

Known Limitations (intentional, Phase 1-2 scope - see DAMAGE_CALCULATOR_DESIGN.md):
- Only percentage/ratio stats are supported (ER, CR, CD, EM, ATK%, HP%,
  DEF%). Absolute totals like the design doc's "HP: 38,000" example would
  require character base stats and weapon data that aren't modeled
  anywhere in this project - see the design doc's "Why Not Pure Character
  Calculation?" section. Flat HP/ATK/DEF substats are intentionally
  excluded from totals for the same reason: they contribute to an
  absolute stat total this module can't compute.
- Main stat values assume max level (20 for 5-star, 16 for 4-star)
  regardless of the artifact's actual level in the GOOD export, per an
  explicit decision to skip building a per-level main-stat table for now.
  This means "current" totals here are really "current at full
  investment" - they'll overstate a stat on any piece you haven't
  finished leveling. Any UI surfacing this data should say so; don't
  present it as exact.
- 5-star main stat values are the standard, unchanged-since-release
  max-level numbers. 4-star values are an approximation (same spirit as
  roll_values.yaml's own 4-star approximation) - replace with exact
  tables if 4-star precision ever matters here.
- Targets are 100% manually configured (matching the design doc's
  "Initial Approach: Manual Targets"), now with team overrides layered on
  top (the design doc's "Future: Team Overrides"). No automatic ER
  estimation from rotation/particle data yet - that's a later phase.
- A character's equipped artifacts are the same regardless of which team
  context you're viewing - this module never asks "which team is this
  character in right now." It's read-only reporting: "if this character
  needs X in team Y, are they there yet." It doesn't know or care which
  team you actually ran last.

Public API:
- load_stat_targets(): loads stat_targets.yaml, returns {} if missing.
- resolve_target_contexts(char_name, all_targets): {context_name: target
  dict} for a character - "Default" (if any base targets exist) plus one
  entry per team that overrides them, each a full merged target dict.
- compute_current_stat_totals(cfg, artifacts_by_slot): current totals for
  the stats this module supports, for one character's equipped pieces.
- score_stat_targets_for_context(char_name, cfg, artifacts_by_slot,
  context_name, context_targets): stat-target report for one character in
  one context, given an already-resolved target dict.
- score_all_stat_targets(by_char, roster, targets): one report per
  (character, context) for every roster character that has configured
  targets in any context, default or team-scoped.
"""

from pathlib import Path

import yaml

from artifact_utils import STAT_LABEL

HERE = Path(__file__).parent

# Stats this module can total without character base-stat/weapon data.
# See module docstring "Known Limitations."
SUPPORTED_STATS = {"ER", "CR", "CD", "EM", "ATK%", "HP%", "DEF%"}

# Innate character stats before any artifacts are equipped. EM/ATK%/HP%/
# DEF% have no baseline bonus - 0% is the correct "no artifacts" starting
# point for a bonus percentage, unlike CR/CD/ER which everyone starts with.
BASE_STAT = {"ER": 100.0, "CR": 5.0, "CD": 50.0}

# Main stat values at max level only (20 for 5-star, 16 for 4-star) - see
# module docstring "Known Limitations" for why actual artifact level is
# intentionally ignored for now.
MAIN_STAT_VALUE = {
    5: {
        "HP%": 46.6, "ATK%": 46.6, "DEF%": 58.3, "EM": 187.0,
        "ER": 51.8, "CR": 31.1, "CD": 62.2, "Heal%": 35.9,
    },
    4: {
        "HP%": 35.0, "ATK%": 35.0, "DEF%": 43.7, "EM": 140.0,
        "ER": 38.9, "CR": 23.3, "CD": 46.6, "Heal%": 26.9,
    },
}

# Only these slots carry a variable main stat; Flower/Feather are fixed
# flat HP/ATK and don't feed into any stat this module tracks.
VARIABLE_MAIN_STAT_SLOTS = {"Sands", "Goblet", "Circlet"}

# How close "current" needs to be to "target" (in the stat's own units -
# percentage points for %/ratio stats) to read as Near rather than a clean
# Under or Exceeds. Keeps a 169.4-vs-170 ER roll from showing as red.
NEAR_TARGET_TOLERANCE = 3.0


def load_stat_targets():
    """Load stat_targets.yaml. Returns {} (no crash) if the file doesn't
    exist yet - targets are opt-in per character, so most characters will
    have none configured until you add them."""
    path = HERE / "stat_targets.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def compute_current_stat_totals(cfg, artifacts_by_slot):
    """Sum main stat + activated substat contributions for every supported
    stat, across the character's currently equipped pieces.

    Only SUPPORTED_STATS are computed - see module docstring for why
    flat/absolute stats aren't included. Unactivated (hidden 4th-line)
    substats are excluded, matching roll_count_for_artifact's convention
    of only counting what's actually revealed.
    """
    totals = {stat: BASE_STAT.get(stat, 0.0) for stat in SUPPORTED_STATS}

    for slot, art in artifacts_by_slot.items():
        if art is None:
            continue

        rarity = art.get("rarity", 5)
        main_table = MAIN_STAT_VALUE.get(rarity, MAIN_STAT_VALUE[5])

        if slot in VARIABLE_MAIN_STAT_SLOTS:
            main_label = STAT_LABEL.get(art.get("mainStatKey"))
            if main_label in SUPPORTED_STATS:
                totals[main_label] += main_table.get(main_label, 0.0)

        for sub in art.get("substats", []):
            label = STAT_LABEL.get(sub.get("key"))
            if label in SUPPORTED_STATS:
                totals[label] += sub.get("value", 0.0)

    return totals


def resolve_target_contexts(char_name, all_targets):
    """Return {context_name: target_dict} for a character: "Default" (the
    character's own top-level block, if any) plus one entry per team under
    all_targets["teams"] that mentions this character - each team's dict is
    merged on top of Default, so a team only needs to specify the stats it
    actually overrides (e.g. just ER), not repeat every target.

    A character with team overrides but no Default block still gets those
    team contexts (Default is simply omitted). A character with neither
    returns {}.
    """
    base = all_targets.get(char_name) or {}
    contexts = {}
    if base:
        contexts["Default"] = dict(base)

    teams_cfg = all_targets.get("teams", {}) or {}
    for team_name, team_chars in teams_cfg.items():
        override = (team_chars or {}).get(char_name)
        if override is None:
            continue
        merged = dict(base)
        merged.update(override)
        contexts[team_name] = merged

    return contexts


def _status_for(current, target):
    diff = current - target
    if diff >= NEAR_TARGET_TOLERANCE:
        return "Exceeds Target"
    if diff >= -NEAR_TARGET_TOLERANCE:
        return "Near Target"
    return "Under Target"


def score_stat_targets_for_context(char_name, cfg, artifacts_by_slot, context_name, context_targets):
    """Build a stat-target report for one character in one context (either
    "Default" or a named team), given that context's already-resolved
    target dict (see resolve_target_contexts).
    """
    useful_stats = [str(s) for s in cfg.get("useful_stats", [])]
    current_totals = compute_current_stat_totals(cfg, artifacts_by_slot)

    stats = {}
    unsupported_targets = []

    for stat, target in context_targets.items():
        stat = str(stat)
        if stat not in SUPPORTED_STATS:
            # A target was configured for a stat this module can't total
            # yet (e.g. an absolute "HP" target). Surface it rather than
            # silently drop it, so a typo or an aspirational future target
            # doesn't just vanish without explanation.
            unsupported_targets.append(stat)
            continue

        current = round(current_totals.get(stat, 0.0), 1)
        target = float(target)
        stats[stat] = {
            "current": current,
            "target": target,
            "delta": round(current - target, 1),
            "status": _status_for(current, target),
        }

    over_target = [s for s in stats if stats[s]["status"] == "Exceeds Target" and s in useful_stats]
    under_target = [s for s in stats if stats[s]["status"] == "Under Target" and s in useful_stats]
    no_target_configured = [
        s for s in useful_stats
        if s in SUPPORTED_STATS and s not in stats
    ]

    return {
        "name": char_name,
        "context": context_name,
        "stats": stats,
        "over_target": over_target,
        "under_target": under_target,
        "no_target_configured": no_target_configured,
        "unsupported_targets": unsupported_targets,
    }


def score_all_stat_targets(by_char, roster, targets):
    """Convenience wrapper for score.py: build one report per (character,
    context) for every roster character that has targets configured in at
    least one context (Default and/or any team). `by_char` is the same
    (character -> {slot: artifact}) mapping parse_good_export already
    produces - nothing new to compute upstream in score.py.

    A character in two teams with different ER needs gets two reports
    here, one per team, so the dashboard can show both side by side rather
    than forcing a single global answer to "what's the target."
    """
    reports = []
    for name, cfg in roster.items():
        contexts = resolve_target_contexts(name, targets)
        if not contexts:
            continue
        artifacts_by_slot = by_char.get(name, {})
        for context_name, context_targets in contexts.items():
            reports.append(
                score_stat_targets_for_context(name, cfg, artifacts_by_slot, context_name, context_targets)
            )
    return reports