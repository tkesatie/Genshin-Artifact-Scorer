"""
Module: team_damage

Purpose:
The start of the actual damage calculator layer from DAMAGE_CALCULATOR_DESIGN.md
- the hybrid model's "character calculations determine personal damage
contribution, team context provides the external factors." This module
turns a character's current stat totals (from stat_targets.py) plus a
team's manually-configured constants (teams.yaml) into a single number:
the Relative Damage Index (RDI).

RDI is NOT a real damage number, and it is NOT the design doc's eventual
"+4.5% team damage" output. It's an honest proxy: without base ATK/HP/DEF
and weapon data (see stat_targets.py's own limitations), this project
cannot compute what a character's real hit actually does. What it CAN
compute exactly is how a character's OWN damage multiplier changes
between two artifact loadouts, because base stats are held constant in
that comparison - the base-stat unknown cancels out of the ratio. RDI
exists to make that comparison later (relative_damage_change), and to
give you a sanity-checkable number today.

Responsibilities:
1. Load manually-configured team constants (teams.yaml): membership plus
   a small set of hand-typed damage-relevant assumptions per team.
2. Compute an RDI for a character in a team context, from their current
   stat totals (reusing stat_targets.compute_current_stat_totals - this
   module adds no new stat-parsing logic of its own).
3. Compute the % RDI change between two stat totals for the SAME
   character (relative_damage_change) - the actual "how much does this
   upgrade matter" primitive, ready to wire into bench/recommendation
   candidates once their exact data shape is available to this module.

Architectural Role:
Sits on top of stat_targets.py the way stat_targets.py sits on top of
character_scoring.py: another independent lens on the same equipped-
artifact data, that does not replace or modify roll-count scoring or
stat-target scoring.

Known Limitations (intentional scope - see DAMAGE_CALCULATOR_DESIGN.md):
- No real damage numbers, no real % team damage. See module docstring
  above. Presenting RDI as an absolute quantity would overstate what's
  actually being computed - it's a same-character comparison tool.
- dmg_bonus_pct, resistance_shred_pct, and reaction_multiplier are 100%
  manually entered per team (teams.yaml), matching the design doc's
  hybrid philosophy of using assumptions rather than simulating a
  rotation. They are not derived from character kit data anywhere in
  this project.
- EM is intentionally excluded from the RDI formula. Its real damage
  contribution depends on which specific reaction formula applies
  (Amplifying vs Transformative vs Aggravate/Spread all scale
  differently), and hardcoding those formulas risks silently asserting
  wrong Genshin mechanics. If EM matters for a team, fold your own
  estimate of its effect into that team's reaction_multiplier instead of
  expecting this module to derive it. EM continues to be tracked and
  targeted normally via stat_targets.py.
- The "primary scaling stat" (ATK% / HP% / DEF%) is picked by a simple
  heuristic - whichever of those three appears first in the character's
  useful_stats - not a real per-character kit lookup. Characters with no
  %-stat in useful_stats (pure EM enablers like Sucrose) get a primary
  stat multiplier of 1.0, i.e. RDI for them reflects Crit + team
  constants only. For pure supports whose real value is a buff/debuff
  they apply to teammates rather than their own hit, RDI understates
  their importance entirely - it only measures a character's own damage
  proxy, never what they do for others.
- rotation_length in teams.yaml is reserved for a future ER-estimation
  phase and is not read by anything in this module yet.

Public API:
- load_teams(): loads teams.yaml, returns {} if missing.
- primary_scaling_stat(cfg): "ATK%" | "HP%" | "DEF%" | None for a roster
  character config, via the useful_stats heuristic above.
- relative_damage_index(cfg, current_totals, assumptions): the RDI float
  for one character/team combination.
- relative_damage_change(cfg, totals_before, totals_after, assumptions):
  % RDI change between two stat totals for the same character - the
  primitive a future "estimated impact of this artifact swap" feature
  would call once wired to real candidate-artifact data.
- score_all_team_damage(by_char, roster, teams): one RDI report per
  (character, team) for every team membership found in teams.yaml.
"""

from pathlib import Path

import yaml

from stat_targets import compute_current_stat_totals

HERE = Path(__file__).parent

# Stats considered for "primary scaling stat" - see module docstring.
SCALING_STAT_CANDIDATES = ["ATK%", "HP%", "DEF%"]


def load_teams():
    """Load teams.yaml. Returns {} (no crash) if the file doesn't exist
    yet - team damage context is opt-in, same as stat targets."""
    path = HERE / "teams.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def primary_scaling_stat(cfg):
    """Heuristic: the first of ATK%/HP%/DEF% found in the character's
    useful_stats. Returns None if none are present (e.g. pure EM
    enablers) - see module docstring "Known Limitations" for what that
    means for their RDI."""
    useful_stats = [str(s) for s in cfg.get("useful_stats", [])]
    for stat in SCALING_STAT_CANDIDATES:
        if stat in useful_stats:
            return stat
    return None


def _crit_multiplier(totals):
    """Standard expected-damage-from-crit multiplier: 1 + CR * CD (both
    as fractions). CR is clamped to [0, 100] since real crit rate can't
    exceed 100% in-game even if the tracked total (stat_targets.py, which
    intentionally does NOT clamp - it's showing you raw investment) does.
    CD is clamped at 0 defensively; it isn't expected to go negative."""
    cr = max(0.0, min(totals.get("CR", 0.0), 100.0)) / 100.0
    cd = max(0.0, totals.get("CD", 0.0)) / 100.0
    return 1.0 + cr * cd


def relative_damage_index(cfg, current_totals, assumptions):
    """Compute the RDI for one character in one team context. See module
    docstring for exactly what this is (and isn't)."""
    assumptions = assumptions or {}

    crit_mult = _crit_multiplier(current_totals)

    scaling_stat = primary_scaling_stat(cfg)
    stat_mult = 1.0
    if scaling_stat:
        stat_mult = 1.0 + max(0.0, current_totals.get(scaling_stat, 0.0)) / 100.0

    dmg_mult = 1.0 + assumptions.get("dmg_bonus_pct", 0.0) / 100.0
    res_mult = 1.0 + assumptions.get("resistance_shred_pct", 0.0) / 100.0
    reaction_mult = assumptions.get("reaction_multiplier", 1.0)

    return crit_mult * stat_mult * dmg_mult * res_mult * reaction_mult


def relative_damage_change(cfg, totals_before, totals_after, assumptions):
    """% RDI change between two stat totals for the SAME character. This
    is exact for the crit + primary-stat portion (base stats cancel out
    of the ratio - see module docstring); the team constants are held
    fixed across before/after by construction, since they describe the
    team, not the loadout, so they don't affect this ratio at all. Kept
    as a parameter anyway so the returned rdi_before/rdi_after values are
    directly comparable to relative_damage_index's own output elsewhere.

    Returns None if rdi_before is 0 (shouldn't happen in practice - crit
    multiplier alone is always >= 1 - but guarded rather than dividing by
    zero).
    """
    rdi_before = relative_damage_index(cfg, totals_before, assumptions)
    rdi_after = relative_damage_index(cfg, totals_after, assumptions)
    if rdi_before <= 0:
        return None
    return {
        "rdi_before": round(rdi_before, 4),
        "rdi_after": round(rdi_after, 4),
        "pct_change": round((rdi_after - rdi_before) / rdi_before * 100, 2),
    }


def score_all_team_damage(by_char, roster, teams):
    """One RDI report per (character, team), for every character listed
    as a member of any team in teams.yaml. Characters not on any team's
    member list simply don't appear - team damage context is opt-in the
    same way stat targets are.
    """
    reports = []
    for team_name, team_cfg in (teams.get("teams") or {}).items():
        members = team_cfg.get("members", []) or []
        assumptions = team_cfg.get("assumptions", {}) or {}

        for char_name in members:
            cfg = roster.get(char_name)
            if cfg is None:
                # A team lists a character that isn't in roster.yaml -
                # skip rather than crash; validate_config-style checking
                # of teams.yaml isn't built yet.
                continue

            artifacts_by_slot = by_char.get(char_name, {})
            totals = compute_current_stat_totals(cfg, artifacts_by_slot)
            rdi = relative_damage_index(cfg, totals, assumptions)

            reports.append({
                "name": char_name,
                "team": team_name,
                "scaling_stat": primary_scaling_stat(cfg),
                "crit_multiplier": round(_crit_multiplier(totals), 3),
                "stat_multiplier": round(
                    1.0 + max(0.0, totals.get(primary_scaling_stat(cfg) or "", 0.0)) / 100.0, 3
                ),
                "dmg_multiplier": round(1.0 + assumptions.get("dmg_bonus_pct", 0.0) / 100.0, 3),
                "res_multiplier": round(1.0 + assumptions.get("resistance_shred_pct", 0.0) / 100.0, 3),
                "reaction_multiplier": assumptions.get("reaction_multiplier", 1.0),
                "rdi": round(rdi, 3),
            })

    return reports