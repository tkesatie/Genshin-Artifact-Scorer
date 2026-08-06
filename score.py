"""
Genshin artifact farming scorer.
...
"""
import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

# Optional progress bar
try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from artifact_utils import (
    effective_useful_pool,
    parse_good_export,
    SLOT_MAP,
    valid_main_stat,
    roll_count_for_artifact,      # <-- added
)
from bench import (
    bench_candidates_lookup,
    bench_potential_lookup,
    find_bench_potential,
    max_possible_useful_rolls,    # <-- added
    matched_characters_for_set,
    SET_ALIASES,
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

# NEW imports
from optimizer import compute_optimal_probabilities


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


def convert_to_cg_artifact(good_artifact_dict):
    """Convert a GOOD artifact dict to a candidate_generation.Artifact object."""
    from candidate_generation import Artifact as CGArtifact
    return CGArtifact(
        substats=good_artifact_dict.get("substats", []),
        unactivatedSubstats=good_artifact_dict.get("unactivatedSubstats", []),
        mainStatKey=good_artifact_dict.get("mainStatKey", ""),
        mainStatValue=good_artifact_dict.get("mainStatValue", 0.0),
        location=good_artifact_dict.get("location")
    )


def compute_expected_20_roll_value(artifact, roll_values, useful_stats):
    """
    Compute an estimate of the artifact's total useful roll value if leveled to +20.
    Used to rank candidates for the optimizer.
    """
    from bench import expected_useful_rolls
    current, expected = expected_useful_rolls(artifact, useful_stats, roll_values)
    return expected


def apply_single_character_filter(roster, character_name):
    """Narrows the roster to only the specified character, if provided.
    """
    if character_name is None:
        return roster
    
    if character_name not in roster:
        print(f"Warning: Character '{character_name}' not found in roster. Scoring all characters.")
        return roster
        
    return {character_name: roster[character_name]}


def build_team_context_lookup(teams):
    """Build a {character_name: team_context} lookup from teams.yaml.

    For each team, every member gets that team's `assumptions` dict as
    their team_context (so team-provided bonuses like `team_em` only
    apply when the character is actually on a configured team). If a
    character is on multiple teams, the first team listed wins - the
    main scoring loop scores each character once, so it can only carry
    one team context.
    """
    lookup = {}
    for team_cfg in (teams.get("teams") or {}).values():
        assumptions = team_cfg.get("assumptions", {}) or {}
        for char_name in (team_cfg.get("members", []) or []):
            if char_name not in lookup:
                lookup[char_name] = assumptions
    return lookup

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
    ap.add_argument("--char", default=None,
                     help="Restrict scoring to a single character from the roster.")
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
    roster = apply_single_character_filter(roster, args.char)
    if args.char:
        print(f"Single character filter active: {len(roster)}/{total_roster_count} roster characters included.")
    theater_cfg = rules.get("imaginarium_theater", {}) or {}
    if theater_cfg.get("enabled"):
        print(
            f"Imaginarium Theater filter active (elements={theater_cfg.get('elements', [])}): "
            f"{len(roster)}/{total_roster_count} roster characters included."
        )

    good_json = json.loads(Path(args.good_export).read_text(encoding="utf-8"))

    # --- Assign unique IDs to artifacts ---
    for i, art in enumerate(good_json.get("artifacts", [])):
        art['id'] = i  # use index as unique ID

    by_char = parse_good_export(good_json, roster)

    stat_targets = load_stat_targets()
    # ---- Extract ER targets for optimizer ----
    stat_floors_by_char = {}
    for char, targets in stat_targets.items():
        # Get the character's config block
        if isinstance(targets, dict) and "Default" in targets:
            char_targets = targets["Default"]
        else:
            char_targets = targets

        floors = dict(char_targets.get("minimums", {}) or {})
        
        # Optional: keep ER in the main block for compatibility, merge it
        er_val = char_targets.get("ER")
        if er_val is not None and er_val > 0:
            floors["energy_recharge"] = er_val

        if floors:
            stat_floors_by_char[char] = floors
    # ------------------------------------------
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

    team_context_lookup = build_team_context_lookup(teams)

    bench_results = find_bench_potential(good_json, roster, rules, roll_values)
    bench_lookup = bench_potential_lookup(bench_results)
    bench_candidates = bench_candidates_lookup(bench_results)

    char_results = []
    for name, cfg in roster.items():
        artifacts = by_char.get(name, {})
        team_context = team_context_lookup.get(name, {})
        char_results.append(score_character(name, cfg, artifacts, rules, roll_values, bench_lookup, bench_candidates, team_context=team_context))

    domain_results = score_domains(char_results, rules)
    recommendations = build_recommendations(bench_results, char_results)
    ceiling_only_results = build_ceiling_only_candidates(bench_results, char_results)

    # =========================================================================
    # NEW: Global optimizer for each character
    # =========================================================================
    # Read optimizer config from rules.yaml
    opt_cfg = rules.get("optimizer", {})
    num_sims = opt_cfg.get("num_sims", 1000)
    in_set_pool_size = opt_cfg.get("in_set_pool_size", 5)
    off_set_pool_size = opt_cfg.get("off_set_pool_size", 5)
    apply_ceiling_filter = opt_cfg.get("apply_ceiling_filter", True)
    candidate_pools_by_char = {}

    print(f"Running global optimizer (num_sims={num_sims}, "
          f"in_set_pool={in_set_pool_size}, off_set_pool={off_set_pool_size}, "
          f"ceiling_filter={apply_ceiling_filter})...")

    # Build a lookup from character -> list of bench results
    char_bench_results = defaultdict(list)
    for b in bench_results:
        char_bench_results[b["character"]].append(b)

    # We'll store probabilities in a dict: (character, slot, artifact_id) -> probability
    optimal_probabilities = {}
    # Fraction of simulations where no combo met the stat floors, per character
    infeasible_rate_by_char = {}

    required_slots = {"Flower", "Feather", "Sands", "Goblet", "Circlet"}

    char_items = list(roster.items())
    if HAS_TQDM:
        iterator = tqdm(char_items, desc="Optimizing characters", unit="char")
    else:
        iterator = char_items
        print(f"Processing {len(char_items)} characters...")

    for char_name, cfg in iterator:
        # Get current equipped artifacts
        current_artifacts = by_char.get(char_name, {})
        if not all(s in current_artifacts for s in required_slots):
            continue  # skip if missing pieces

        char_benches = char_bench_results.get(char_name, [])
        if not char_benches:
            continue

        target_short = cfg.get("set")
        if not target_short or "/" in target_short:
            continue  # split-set not supported for optimizer yet
        target_keys = set(SET_ALIASES.get(target_short, [target_short]))

        in_set_pools = {slot: [] for slot in required_slots}
        off_set_pools = {slot: [] for slot in required_slots}
        useful_stats = [str(s) for s in cfg.get("useful_stats", [])]

        candidate_pools_by_char[char_name] = {
            "in_set": in_set_pools,
            "off_set": off_set_pools,
            "current": current_artifacts,
            "useful_stats": useful_stats,
        }

        # Pre-compute current roll counts per slot for ceiling filter
        current_rolls_cache = {}
        for slot in required_slots:
            art = current_artifacts.get(slot)
            if art:
                current_rolls_cache[slot] = roll_count_for_artifact(art, useful_stats, roll_values)
            else:
                current_rolls_cache[slot] = 0

        for b in char_benches:
            slot = b["slot"]
            art = b["original_artifact"]
            is_in_set = art.get("setKey") in target_keys

            # --- CEILING FILTER ---
            if apply_ceiling_filter:
                _, ceiling = max_possible_useful_rolls(art, useful_stats, roll_values)
                current_rolls = current_rolls_cache.get(slot, 0)
                if ceiling < current_rolls:
                    continue  # skip this candidate, it can never beat the current piece

            expected_val = compute_expected_20_roll_value(art, roll_values, useful_stats)
            if is_in_set:
                in_set_pools[slot].append((expected_val, art))
            else:
                off_set_pools[slot].append((expected_val, art))

        # ------------------------------------------------------------------
        # Seed off-set pools from the FULL inventory, not just bench results.
        #
        # bench.find_bench_potential only evaluates an artifact for characters
        # whose configured SET matches that artifact's setKey
        # (matched_characters_for_set). That means a non-Deepwood piece never
        # appears in Nahida's bench results, so char_benches alone can never
        # populate her off-set pool - the dashboard would show zero off-pieces
        # even when her inventory has plenty of strong options.
        #
        # flex.py already ignores setKey when finding off-set flex candidates;
        # the optimizer should do the same. Scan the whole export for pieces
        # that fit this character's slot + main stat, are actually available
        # (unequipped or self-equipped), and aren't the character's own set.
        # ------------------------------------------------------------------
        for art in good_json.get("artifacts", []):
            slot = SLOT_MAP.get(art.get("slotKey"))
            if slot not in required_slots:
                continue
            if art.get("setKey") in target_keys:
                continue  # in-set: already handled by bench results above
            if art.get("location") not in (None, "", char_name):
                continue  # equipped by someone else - not available
            # Skip duplicates already added (from bench results, if any)
            # off_set_pools[slot] contains (expected_val, art) tuples
            if any(existing[1].get("id") == art.get("id") for existing in off_set_pools[slot]):
                continue
            if not valid_main_stat(art, cfg, slot):
                continue

            if apply_ceiling_filter:
                _, ceiling = max_possible_useful_rolls(art, useful_stats, roll_values)
                current_rolls = current_rolls_cache.get(slot, 0)
                if ceiling < current_rolls:
                    continue  # can never beat the currently equipped piece

            expected_val = compute_expected_20_roll_value(art, roll_values, useful_stats)
            off_set_pools[slot].append((expected_val, art))

        # Sort and slice each pool
        for slot in required_slots:
            in_set_pools[slot].sort(key=lambda x: x[0], reverse=True)
            off_set_pools[slot].sort(key=lambda x: x[0], reverse=True)
            in_set_pools[slot] = [art for _, art in in_set_pools[slot][:in_set_pool_size]]
            off_set_pools[slot] = [art for _, art in off_set_pools[slot][:off_set_pool_size]]

            # Include current equipped piece if not already present
            current = current_artifacts.get(slot)
            if current is not None:
                is_in_set = current.get("setKey") in target_keys
                pool = in_set_pools if is_in_set else off_set_pools
                if not any(art.get('id') == current.get('id') for art in pool[slot]):
                    pool[slot].append(current)

        stat_floors = stat_floors_by_char.get(char_name)   # None means no constraint
        damage_model = cfg.get("damage_model", "none")

        # Team context for build optimality: Active characters use the first
        # team in teams.yaml order that lists them; IT Only characters get no
        # team context (their builds are evaluated without team bonuses).
        team_context = (
            team_context_lookup.get(char_name, {})
            if cfg.get("usage") == "Active"
            else {}
        )

        probs_result = compute_optimal_probabilities(
            char_config=cfg,
            in_set_pools=in_set_pools,
            off_set_pools=off_set_pools,
            current_artifacts=current_artifacts,
            roll_values=roll_values,
            target_set_keys=target_keys,
            num_sims=num_sims,
            stat_floors=stat_floors,
            damage_model=damage_model,
            team_context=team_context
        )
        probs = probs_result["probabilities"]
        infeasible_rate_by_char[char_name] = probs_result["infeasible_rate"]

        for slot in required_slots:
            for art in in_set_pools[slot] + off_set_pools[slot]:
                art_id = art.get('id')
                if art_id is not None and art_id in probs:
                    optimal_probabilities[(char_name, slot, art_id)] = probs[art_id]

    optimizer_candidates_by_char = {}
    for char_name, pools in candidate_pools_by_char.items():
        slot_candidates = {}
        for slot in required_slots:
            candidates = []
            # Combine in-set and off-set
            for art in pools["in_set"][slot] + pools["off_set"][slot]:
                art_id = art.get('id')
                if art_id is not None:
                    prob = optimal_probabilities.get((char_name, slot, art_id), 0.0)
                else:
                    prob = 0.0
                # Determine if this is the equipped piece (match by id)
                is_equipped = (
                    art.get('id') == pools["current"].get(slot, {}).get('id')
                )
                candidates.append({
                    "artifact": art,
                    "probability": prob,
                    "is_equipped": is_equipped,
                })
            # Sort by probability descending, equipped piece will fall where it belongs
            candidates.sort(key=lambda x: x["probability"], reverse=True)
            slot_candidates[slot] = candidates
        optimizer_candidates_by_char[char_name] = slot_candidates
    
    # Attach probabilities to bench_results
    for b in bench_results:
        char_name = b["character"]
        slot = b["slot"]
        art_id = b.get("artifact_id")
        if art_id is not None:
            prob = optimal_probabilities.get((char_name, slot, art_id), 0.0)
            b["optimal_probability"] = round(prob * 100, 1)
        else:
            b["optimal_probability"] = 0.0

    # Also attach to recommendations
    for rec in recommendations:
        art_id = rec.get("artifact_id")
        if art_id is not None:
            for b in bench_results:
                if b.get("artifact_id") == art_id and b["character"] == rec["character"] and b["slot"] == rec["slot"]:
                    rec["optimal_probability"] = b["optimal_probability"]
                    break
        else:
            rec["optimal_probability"] = 0.0

    print("Optimizer complete.")

    # =========================================================================

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
        prob_lookup=optimal_probabilities,
        equipped_artifacts_by_char=by_char,
        roster=roster,
        optimizer_candidates_by_char=optimizer_candidates_by_char,   # <-- NEW
        infeasible_rate_by_char=infeasible_rate_by_char,
    )


if __name__ == "__main__":
    main()