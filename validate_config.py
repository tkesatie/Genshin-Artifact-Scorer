"""
Module: validate_config

Purpose:
Pre-flight validation of `roster.yaml` / `rules.yaml`, run by `score.py`
before scoring begins. Catches the class of config-drift/typo bugs that
currently fail silently — a character that mysteriously never gets bench
matches, a slot that never validates, a threshold lookup that would blow up
mid-run — and reports them clearly up front instead.

Responsibilities:
1. Detect the YAML unquoted-boolean/null gotcha (most notably Noblesse
   Oblige's short label "NO" becoming Python `False`) in any roster field
   that's meant to hold a string.
2. Detect `usage|role` combinations with no matching entry in
   `rules.base_thresholds`, which `thresholds.compute_thresholds` needs.
3. Detect roster `set` values with no coverage in `bench.SET_ALIASES`,
   which silently means that character gets zero bench-upgrade evaluation.
   "/"-delimited split-set labels are intentionally excluded from this check
   (see README: `character_scoring.compute_set_status` and `flex.py` already
   special-case them as "Split (unverified)").
4. Detect slot names used as `main_stats` keys that aren't real slot names,
   drawn from `artifact_utils.SLOT_MAP`'s display values so this check stays
   in sync with the actual slot vocabulary instead of a hardcoded copy.
5. (NEW) Validate new fields introduced for damage calculation:
   - `damage_model`: must be one of "amplifying", "transformative", "none".
   - `primary_stat`: must be one of "ATK", "HP", "DEF", "EM".
   - If `primary_stat` is ATK/HP/DEF, the corresponding `base_atk`/`base_hp`/`base_def`
     must be present and be a positive number.
   - `er_minimum`: if set, must be a positive number (float/int).
   - `er_minimum_by_role` (global rules): if present, must be a dict mapping role -> number.
   - `team_context` (global rules): warn about unknown keys.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ValidationIssue:
    severity: str  # "ERROR" or "WARNING"
    character: Optional[str]  # None for issues not tied to one character
    message: str

    def __str__(self):
        if self.character:
            return f"[{self.severity}] {self.character}: {self.message}"
        return f"[{self.severity}] {self.message}"


def _is_coerced_bool_or_none(value):
    """PyYAML's safe_load turns bare NO/No/no/YES/Y/N/ON/OFF/TRUE/FALSE/~
    (and a few other boolean/null keywords) into a real Python bool or None.
    Any config field meant to hold a string label that comes back as one of
    these types has almost certainly hit that gotcha rather than being
    deliberately set to a boolean."""
    return isinstance(value, bool) or value is None


def _value_has_coercion(value):
    """Same check, but also descends into lists so multi-value fields (e.g.
    a slot allowing more than one main stat) get checked element-by-element."""
    if isinstance(value, list):
        return any(_is_coerced_bool_or_none(v) for v in value)
    return _is_coerced_bool_or_none(value)


def _real_slot_names():
    """Real slot display names, sourced from artifact_utils.SLOT_MAP so this
    stays in sync with the actual slot vocabulary rather than a hardcoded
    copy that could drift."""
    from artifact_utils import SLOT_MAP
    return set(SLOT_MAP.values())


def check_boolean_coercion(roster):
    """Check 1: unquoted-YAML-boolean/null gotcha across roster fields that
    are meant to hold string labels."""
    issues = []
    string_fields = ("usage", "role", "set", "domain")

    for name, cfg in roster.items():
        if _is_coerced_bool_or_none(name):
            issues.append(ValidationIssue(
                "ERROR", str(name),
                "Character name itself was read as a YAML boolean/null - quote it in roster.yaml."
            ))
            continue

        if not isinstance(cfg, dict):
            issues.append(ValidationIssue(
                "ERROR", name,
                f"Character config is not a mapping (got {type(cfg).__name__}) - check roster.yaml indentation."
            ))
            continue

        for field in string_fields:
            if field not in cfg:
                continue
            value = cfg[field]
            if _is_coerced_bool_or_none(value):
                issues.append(ValidationIssue(
                    "ERROR", name,
                    f"`{field}` was read as {value!r} instead of a string - likely an unquoted "
                    f"YAML boolean/null keyword (e.g. NO, Yes, Null) in the source file. "
                    f'Quote the value in roster.yaml, e.g. {field}: "NO".'
                ))

        main_stats = cfg.get("main_stats") or {}
        if isinstance(main_stats, dict):
            for slot, main_stat in main_stats.items():
                if _is_coerced_bool_or_none(slot):
                    issues.append(ValidationIssue(
                        "ERROR", name,
                        f"A `main_stats` slot key was read as {slot!r} instead of a string - quote the slot name."
                    ))
                    continue
                if _value_has_coercion(main_stat):
                    issues.append(ValidationIssue(
                        "ERROR", name,
                        f"`main_stats.{slot}` was read as {main_stat!r} instead of a string/'ANY' - "
                        "likely an unquoted YAML boolean/null keyword."
                    ))

    return issues


def check_usage_role_thresholds(roster, rules):
    """Check 2: every character's `usage|role` combo must have a matching
    entry in rules.yaml's base_thresholds, or thresholds.compute_thresholds
    has nothing to look up for them."""
    issues = []
    base_thresholds = rules.get("base_thresholds", {}) or {}

    for name, cfg in roster.items():
        if not isinstance(cfg, dict):
            continue  # already reported by check_boolean_coercion
        usage = cfg.get("usage")
        role = cfg.get("role")
        if _is_coerced_bool_or_none(usage) or _is_coerced_bool_or_none(role):
            continue  # already reported by check_boolean_coercion

        key = f"{usage}|{role}"
        if key not in base_thresholds:
            issues.append(ValidationIssue(
                "ERROR", name,
                f'No base_thresholds entry for "{key}" in rules.yaml - threshold lookup will fail '
                "for this character at scoring time."
            ))

    return issues


def check_set_aliases(roster):
    """Check 3: every character's `set` label (excluding documented
    "/"-delimited split-set labels) should resolve through
    bench.SET_ALIASES, or bench.py will never evaluate upgrade candidates
    for that character's set."""
    issues = []
    try:
        from bench import SET_ALIASES
    except ImportError:
        return [ValidationIssue(
            "WARNING", None,
            "Could not import bench.SET_ALIASES (bench.py missing?) - skipping set-coverage check."
        )]

    for name, cfg in roster.items():
        if not isinstance(cfg, dict):
            continue
        set_label = cfg.get("set")
        if _is_coerced_bool_or_none(set_label) or not isinstance(set_label, str):
            continue  # already reported, or not a checkable string
        if "/" in set_label:
            continue  # documented split-set case - handled separately, not a bug
        if set_label not in SET_ALIASES:
            issues.append(ValidationIssue(
                "WARNING", name,
                f'set "{set_label}" has no entry in bench.SET_ALIASES - bench.py will never surface '
                "upgrade candidates for this character's set."
            ))

    return issues


def check_slot_names(roster):
    """Check 4: `main_stats` keys should be real slot names, or
    valid_main_stat will silently never match that slot for that character."""
    issues = []
    try:
        valid_slots = _real_slot_names()
    except ImportError:
        return [ValidationIssue(
            "WARNING", None,
            "Could not import artifact_utils.SLOT_MAP - skipping slot-name check."
        )]

    for name, cfg in roster.items():
        if not isinstance(cfg, dict):
            continue
        main_stats = cfg.get("main_stats") or {}
        if not isinstance(main_stats, dict):
            continue
        for slot in main_stats:
            if _is_coerced_bool_or_none(slot):
                continue  # already reported
            if slot not in valid_slots:
                issues.append(ValidationIssue(
                    "WARNING", name,
                    f'main_stats references slot "{slot}", which isn\'t a recognized slot name '
                    f"({', '.join(sorted(valid_slots))}). Likely a typo - valid_main_stat will "
                    "never match this slot for this character."
                ))

    return issues


# ============================================================================
# NEW CHECKS FOR DAMAGE CALCULATOR FIELDS
# ============================================================================

def check_primary_stat(roster):
    """Check that `primary_stat`, if set, is one of the allowed values,
    and that the corresponding base stat is present and positive."""
    issues = []
    allowed = {"ATK", "HP", "DEF", "EM"}
    for name, cfg in roster.items():
        if not isinstance(cfg, dict):
            continue
        ps = cfg.get("primary_stat", "ATK")
        if ps not in allowed:
            issues.append(ValidationIssue(
                "ERROR", name,
                f'primary_stat "{ps}" is not one of {allowed}.'
            ))
            continue

        if ps == "ATK":
            base = cfg.get("base_atk")
            if base is None or not isinstance(base, (int, float)) or base <= 0:
                issues.append(ValidationIssue(
                    "ERROR", name,
                    f"primary_stat is ATK but base_atk is missing or invalid (must be a positive number)."
                ))
        elif ps == "HP":
            base = cfg.get("base_hp")
            if base is None or not isinstance(base, (int, float)) or base <= 0:
                issues.append(ValidationIssue(
                    "ERROR", name,
                    f"primary_stat is HP but base_hp is missing or invalid (must be a positive number)."
                ))
        elif ps == "DEF":
            base = cfg.get("base_def")
            if base is None or not isinstance(base, (int, float)) or base <= 0:
                issues.append(ValidationIssue(
                    "ERROR", name,
                    f"primary_stat is DEF but base_def is missing or invalid (must be a positive number)."
                ))
        # EM requires no base stat
    return issues


def check_er_minimum(roster, rules):
    """Check that each character's `er_minimum`, if set, is a positive number.
    Also check that rules.yaml contains an `er_minimum_by_role` mapping; if missing,
    warn (since fallback is 180)."""
    issues = []

    # Warn if rules.yaml is missing er_minimum_by_role
    er_by_role = rules.get("er_minimum_by_role")
    if er_by_role is None:
        issues.append(ValidationIssue(
            "WARNING", None,
            "rules.yaml does not define `er_minimum_by_role` - will default to 180 for all roles."
        ))
    elif not isinstance(er_by_role, dict):
        issues.append(ValidationIssue(
            "WARNING", None,
            "`er_minimum_by_role` in rules.yaml is not a mapping - will default to 180."
        ))

    for name, cfg in roster.items():
        if not isinstance(cfg, dict):
            continue
        er = cfg.get("er_minimum")
        if er is None:
            continue
        if not isinstance(er, (int, float)) or er <= 0:
            issues.append(ValidationIssue(
                "ERROR", name,
                f'er_minimum "{er}" must be a positive number (float/int).'
            ))
    return issues


def check_team_context(rules):
    """Warn about unknown keys inside `team_context` if present."""
    issues = []
    tc = rules.get("team_context")
    if tc is None:
        return issues
    if not isinstance(tc, dict):
        issues.append(ValidationIssue(
            "WARNING", None,
            "`team_context` in rules.yaml is not a mapping - will be ignored."
        ))
        return issues

    allowed_keys = {"external_flat_stat", "external_dmg_bonus", "external_em"}
    for key in tc.keys():
        if key not in allowed_keys:
            issues.append(ValidationIssue(
                "WARNING", None,
                f'team_context key "{key}" is unrecognized - allowed: {allowed_keys}.'
            ))
    return issues


def check_evaluation_pipeline(roster):
    """Check that `evaluation_pipeline` steps reference registered evaluator
    types. Catches typoed step names before the optimizer/dashboard hits a
    runtime ValueError mid-run."""
    issues = []
    try:
        from pipeline import EvaluatorRegistry
    except ImportError:
        return [ValidationIssue(
            "WARNING", None,
            "Could not import pipeline.EvaluatorRegistry (pipeline.py missing?) - skipping pipeline check."
        )]

    for name, cfg in roster.items():
        if not isinstance(cfg, dict):
            continue
        steps = cfg.get("evaluation_pipeline")
        if steps is None:
            continue
        if not isinstance(steps, list) or not steps:
            issues.append(ValidationIssue(
                "ERROR", name,
                "`evaluation_pipeline` must be a non-empty list of step dicts "
                '(e.g. [{type: standard_damage}, {type: personal_damage}]).'
            ))
            continue
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                issues.append(ValidationIssue(
                    "ERROR", name,
                    f"evaluation_pipeline[{i}] is not a mapping (got {type(step).__name__}) "
                    "- each step must be a dict with a `type` key."
                ))
                continue
            step_type = step.get("type")
            if not isinstance(step_type, str) or not step_type:
                issues.append(ValidationIssue(
                    "ERROR", name,
                    f"evaluation_pipeline[{i}] is missing a string `type` key."
                ))
                continue
            if step_type not in EvaluatorRegistry:
                issues.append(ValidationIssue(
                    "ERROR", name,
                    f"evaluation_pipeline[{i}] references unknown step type "
                    f'"{step_type}". Available: {", ".join(sorted(EvaluatorRegistry))}.'
                ))
    return issues


# ============================================================================
# MAIN VALIDATION ENTRY POINT
# ============================================================================

def validate_config(roster, rules):
    """Run all pre-flight checks and return the combined list of issues."""
    issues = []
    issues += check_boolean_coercion(roster)
    issues += check_usage_role_thresholds(roster, rules)
    issues += check_set_aliases(roster)
    issues += check_slot_names(roster)
    # New checks
    issues += check_primary_stat(roster)
    issues += check_er_minimum(roster, rules)
    issues += check_team_context(rules)
    issues += check_evaluation_pipeline(roster)
    return issues


def has_errors(issues):
    return any(issue.severity == "ERROR" for issue in issues)