"""
Genshin artifact farming scorer.
...
"""
import argparse
import json
import sys
from pathlib import Path

from artifact_utils import effective_useful_pool, parse_good_export, SLOT_MAP, valid_main_stat
from bench import (
    bench_candidates_lookup,
    bench_potential_lookup,
    find_bench_potential,
    max_possible_useful_rolls,
    matched_characters_for_set
)
from character_scoring import score_character, score_domains
from config import load_configs
from flex import find_flex_candidates
from inventory import classify_inventory_artifact
from recommendations import build_ceiling_only_candidates, build_recommendations
from render_html import render_html
from snapshot import maybe_update_snapshot
from stat_targets import load_stat_targets, score_all_stat_targets
from team_damage import load_teams, score_all_team_damage
from thresholds import compute_thresholds
from validate_config import has_errors, validate_config


def apply_imaginarium_theater_filter(roster, rules):
    """Optionally narrow the roster to characters relevant to the currently
    active Imaginarium Theater elements (see README's Imaginarium Theater
    Filter section).

    `usage: Active` characters are always kept, since they're farmed for
    reasons independent of Theater. `usage: IT Only` characters are kept
    only when their `element` is one of the configured `elements`. When the
    filter is disabled (or `imaginarium_theater` is absent from rules.yaml),
    the roster passes through unchanged.

    This is applied exactly once, in main(), before the (possibly narrowed)
    roster is threaded into every downstream consumer - bench evaluation,
    character/domain scoring, flex, inventory matching, and the progress
    snapshot all just take `roster` as an input rather than re-deriving it,
    so filtering it here is what actually excludes filtered characters
    everywhere the README promises.
    """
    theater_cfg = rules.get("imaginarium_theater", {}) or {}
    if not theater_cfg.get("enabled"):
        return roster

    active_elements = set(theater_cfg.get("elements", []) or [])

    return {
        name: cfg
        for name, cfg in roster.items()
        if cfg.get("usage") == "Active" or cfg.get("element") in active_elements
    }


def best_fit_for_artifact(artifact, roster, slot, roll_values, rules):
    """Evaluate this artifact against every roster character whose main-stat
    config allows this slot, regardless of set. Returns fits sorted by
    ceiling, descending (in-set breaks ties), so fits[0] is the best possible
    home for this piece. Each fit carries its own 'excellent' threshold so
    the inventory classifier can check whether this piece has genuine
    excellent-tier potential for that specific character."""
    fits = []
    for name, cfg in roster.items():
        if not valid_main_stat(artifact, cfg, slot):
            continue
        useful_stats = [str(s) for s in cfg["useful_stats"]]
        current, ceiling = max_possible_useful_rolls(artifact, useful_stats, roll_values)
        eff_pool = effective_useful_pool(artifact.get("mainStatKey"), useful_stats)
        good, excellent = compute_thresholds(rules, cfg["usage"], cfg["role"], slot, eff_pool, name)
        fits.append({
            "character": name,
            "ceiling": ceiling,
            "current_rolls": current,
            "useful_stats": useful_stats,
            "in_set": name in matched_characters_for_set(artifact.get("setKey"), roster),
            "good": good,
            "excellent": excellent,
        })
    fits.sort(key=lambda f: (-f["ceiling"], not f["in_set"]))
    return fits


def build_inventory_results(good_json, roster, rules, roll_values):
    """Classify every unequipped artifact against every roster character
    whose main-stat config allows this slot (not just set-matched ones)."""
    results = []

    for art in good_json.get("artifacts", []):
        if art.get("location"):
            continue

        slot = SLOT_MAP.get(art.get("slotKey"))
        if slot is None:
            continue

        fits = best_fit_for_artifact(art, roster, slot, roll_values, rules)

        classification = classify_inventory_artifact(art, fits)
        classification["artifact"] = art
        classification["slot"] = slot
        classification["ceiling"] = fits[0]["ceiling"] if fits else 0
        classification["fits"] = fits[:3]  # top 3 alternate homes, for display
        results.append(classification)

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("good_export", nargs="?", default=None,
                     help="Path to your Irminsul/GOOD JSON export. Not required with --validate-only.")
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--snapshot-path", default=None,
                     help="Override the snapshot file location (default: rules.yaml snapshot.path).")
    ap.add_argument("--snapshot-interval-hours", type=float, default=None,
                     help="Override the minimum hours between snapshot saves (default: rules.yaml snapshot.min_interval_hours).")
    ap.add_argument("--no-snapshot", action="store_true",
                     help="Skip the run-to-run progress snapshot entirely for this run.")
    ap.add_argument("--skip-validation", action="store_true",
                     help="Skip the config pre-flight validation pass entirely.")
    ap.add_argument("--validate-only", action="store_true",
                     help="Run config pre-flight validation and exit without scoring.")
    args = ap.parse_args()

    if not args.validate_only and not args.good_export:
        ap.error("good_export is required unless --validate-only is set")

    roster, rules, roll_values = load_configs()

    if not args.skip_validation:
        issues = validate_config(roster, rules)
        if issues:
            print(f"Config validation: {len(issues)} issue(s) found.")
            for issue in issues:
                print(f"  {issue}")
        else:
            print("Config validation: no issues found.")

        if args.validate_only:
            sys.exit(1 if has_errors(issues) else 0)

        if has_errors(issues):
            print("\nAborting before scoring: fix the ERROR-level issue(s) above, "
                  "or rerun with --skip-validation to force it anyway.")
            sys.exit(1)
    elif args.validate_only:
        print("--validate-only has no effect together with --skip-validation; nothing to check.")
        return

    total_roster_count = len(roster)
    roster = apply_imaginarium_theater_filter(roster, rules)
    theater_cfg = rules.get("imaginarium_theater", {}) or {}
    if theater_cfg.get("enabled"):
        print(
            f"Imaginarium Theater filter active (elements={theater_cfg.get('elements', [])}): "
            f"{len(roster)}/{total_roster_count} roster characters included."
        )

    good_json = json.loads(Path(args.good_export).read_text(encoding="utf-8"))

    by_char = parse_good_export(good_json, roster)

    stat_targets = load_stat_targets()
    stat_target_results = score_all_stat_targets(by_char, roster, stat_targets)
    if stat_target_results:
        over_count = sum(len(r["over_target"]) for r in stat_target_results)
        under_count = sum(len(r["under_target"]) for r in stat_target_results)
        print(f"Stat targets: {len(stat_target_results)} character(s) configured "
              f"({over_count} stat(s) over target, {under_count} still under).")

    teams = load_teams()
    team_damage_results = score_all_team_damage(by_char, roster, teams)
    if team_damage_results:
        print(f"Team damage context: {len(team_damage_results)} character/team combination(s) "
              f"across {len(teams.get('teams') or {})} configured team(s).")

    bench_results = find_bench_potential(good_json, roster, rules, roll_values)
    bench_lookup = bench_potential_lookup(bench_results)
    bench_candidates = bench_candidates_lookup(bench_results)

    char_results = []
    for name, cfg in roster.items():
        artifacts = by_char.get(name, {})
        char_results.append(score_character(name, cfg, artifacts, rules, roll_values, bench_lookup, bench_candidates))

    domain_results = score_domains(char_results, rules)
    recommendations = build_recommendations(bench_results, char_results)
    ceiling_only_results = build_ceiling_only_candidates(bench_results, char_results)

    flex_min_gain = rules.get("flex_rules", {}).get("min_ev_gain", 2.0)
    flex_results = find_flex_candidates(
        good_json, roster, char_results, roll_values, min_ev_gain=flex_min_gain
    )

    inventory_results = build_inventory_results(
        good_json, roster, rules, roll_values
    )

    strongbox_count = sum(1 for i in inventory_results if i["action"] == "SAFE_STRONGBOX")
    elixir_count = sum(1 for i in inventory_results if i["action"] == "SANCTIFY_ELIXIR")
    print(f"Flex slot suggestions: {len(flex_results)}")
    print(f"Inventory: {strongbox_count} strongbox, {elixir_count} elixir fodder, "
          f"{sum(1 for i in inventory_results if i['action'] == 'KEEP')} keep, "
          f"{sum(1 for i in inventory_results if i['action'] == 'REVIEW')} review")

    progress_changes = None
    if not args.no_snapshot:
        snap_cfg = rules.get("snapshot", {})
        snapshot_path = args.snapshot_path or snap_cfg.get("path", "snapshot.json")
        min_interval_hours = (
            args.snapshot_interval_hours
            if args.snapshot_interval_hours is not None
            else snap_cfg.get("min_interval_hours", 24)
        )

        progress_changes = maybe_update_snapshot(snapshot_path, char_results, min_interval_hours)
        if progress_changes is None:
            print(f"Snapshot: last save was under {min_interval_hours}h ago, skipping (no diff shown).")
        elif progress_changes:
            print("Progress since last snapshot:")
            for line in progress_changes:
                print(f"  {line}")
        else:
            print("Snapshot saved. No changes since last snapshot.")

    render_html(
        char_results, domain_results, recommendations, args.out,
        flex_results=flex_results, inventory_results=inventory_results,
        progress_changes=progress_changes, ceiling_only_results=ceiling_only_results,
        stat_target_results=stat_target_results, team_damage_results=team_damage_results,
    )


if __name__ == "__main__":
    main()