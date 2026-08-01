"""
Genshin artifact farming scorer.
...
"""
import argparse
import json
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
from recommendations import build_recommendations
from render_html import render_html
from thresholds import compute_thresholds


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
    ap.add_argument("good_export", help="Path to your Irminsul/GOOD JSON export")
    ap.add_argument("--out", default="dashboard.html")
    args = ap.parse_args()

    roster, rules, roll_values = load_configs()
    good_json = json.loads(Path(args.good_export).read_text(encoding="utf-8"))

    by_char = parse_good_export(good_json, roster)
    bench_results = find_bench_potential(good_json, roster, rules, roll_values)
    bench_lookup = bench_potential_lookup(bench_results)
    bench_candidates = bench_candidates_lookup(bench_results)

    char_results = []
    for name, cfg in roster.items():
        artifacts = by_char.get(name, {})
        char_results.append(score_character(name, cfg, artifacts, rules, roll_values, bench_lookup, bench_candidates))

    domain_results = score_domains(char_results, rules)
    recommendations = build_recommendations(bench_results, char_results)

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

    render_html(
        char_results, domain_results, recommendations, args.out,
        flex_results=flex_results, inventory_results=inventory_results,
    )


if __name__ == "__main__":
    main()