"""
Module: render_html

Purpose:
This module is responsible for generating an HTML report that visualizes artifact farming data. It provides a comprehensive dashboard to help users understand the status of their artifacts, domains, and recommended swaps.

Responsibilities:
1. **HTML Generation**: Create an HTML file that includes tables summarizing character artifact statuses, domain scores, and recommended swaps.
2. **Data Formatting**: Format various data points such as substats, badges, and verdicts into a user-friendly HTML format.
3. **Sorting and Filtering**: Implement sorting functionality for the tables based on different columns to help users prioritize their farming activities.

Architectural Role:
This module serves as part of the presentation layer in the application. It is expected to be used by higher-level modules that process artifact data and generate reports. The module does not contain business logic or orchestration; it focuses solely on transforming structured data into an HTML format for display.

Intended Dependencies:
- **Dependencies**: This module relies on Python's standard library, specifically the `pathlib` module for file path operations.
- **Project Modules**: It assumes the existence of other modules that provide the data structures (`char_results`, `domain_results`, `recommendations`) used in its functions. These modules are responsible for collecting and processing artifact information.

Boundaries:
- This module should not handle data collection or business logic. Its primary role is to format and present data.
- Logic related to artifact scoring, EV calculations, and recommendation generation should be handled by other modules.
- The module does not perform any file I/O operations beyond writing the generated HTML file; all data input should come from external sources.

Public API:
- `render_html(char_results, domain_results, recommendations, out_path, ..., multi_piece_results=None, prob_lookup=None)`: This is the main function that generates the HTML report. `prob_lookup` is a dict mapping (character, slot, artifact_id) -> probability percentage.
"""

import json
from pathlib import Path


def _esc_attr(value):
    """Minimal HTML-attribute escaping for values (like character names)
    embedded in data-* attributes."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


STATUS_COLOR = {
    "Farming": "#e05a4e", "Usable": "#e0a94e", "Finished": "#4e8ee0",
    "Luxury": "#4ec97a", "Needs Work": "#e05a4e", "Good": "#e0a94e",
    "Excellent": "#4ec97a", "Missing": "#999999",
}

SET_BONUS_COLOR = {
    "4pc": "#4ec97a", "2pc": "#e0a94e", "None": "#e05a4e",
    "Split (unverified)": "#999999", "N/A": "#999999",
}

INVENTORY_COLOR = {
    "KEEP": "#4ec97a", "REVIEW": "#e0a94e",
    "SANCTIFY_ELIXIR": "#4e8ee0", "SAFE_STRONGBOX": "#999999",
}

STAT_TARGET_COLOR = {
    "Under Target": "#e05a4e", "Near Target": "#e0a94e", "Exceeds Target": "#4ec97a",
}

def substat_display_for(artifact, useful_stats):
    """Render an artifact's active + hidden substats, bolding whatever
    counts as useful for the given stat list."""
    from artifact_utils import STAT_LABEL
    parts = []
    for sub in artifact.get("substats", []):
        label = STAT_LABEL.get(sub.get("key"), sub.get("key"))
        text = f"{label}+{sub.get('value')}"
        if label in useful_stats:
            text = f"<b>{text}</b>"
        parts.append(text)
    for sub in artifact.get("unactivatedSubstats", []):
        label = STAT_LABEL.get(sub.get("key"), sub.get("key"))
        text = f"{label}+{sub.get('value')}"
        if label in useful_stats:
            text = f"<b>{text}</b>"
        parts.append(f'{text} <span style="font-size:10px;color:#888;">(hidden)</span>')
    return ", ".join(parts) if parts else "—"


def format_substats_html(substats, rarity=5, level=0):
    """Formats substats list.
    - Bold if useful
    - Italics if unactivated / potential 4th line
    """
    formatted = []

    for label, val, is_useful in substats:
        text = f"{label}+{val}" if val else label
        if is_useful:
            text = f"<b>{text}</b>"
        formatted.append(text)

    if rarity == 5 and level < 4 and len(substats) == 3:
        formatted.append("<i>+1 Hidden Line</i>")

    return ", ".join(formatted)


def _filter_input_html(table_id, placeholder):
    """Small, self-contained filter box for a given table. Filtering itself
    happens client-side in vanilla JS (see the wireup script at the bottom
    of render_html) — this just emits the input + its "no matches" message."""
    return (
        f'<div class="table-filter-wrap">'
        f'<input type="text" class="table-filter" data-table="{table_id}" placeholder="{placeholder}">'
        f'<div class="table-filter-empty" data-empty-for="{table_id}">No matching rows.</div>'
        f'</div>'
    )


def render_html(char_results, domain_results, recommendations, out_path,
                 flex_results=None, inventory_results=None, progress_changes=None,
                 ceiling_only_results=None, stat_target_results=None, team_damage_results=None,
                 multi_piece_results=None, prob_lookup=None,
                 equipped_artifacts_by_char=None, roster=None,
                 optimizer_candidates_by_char=None):   # <-- NEW
    flex_results = flex_results or []
    inventory_results = inventory_results or []
    ceiling_only_results = ceiling_only_results or []
    stat_target_results = stat_target_results or []
    team_damage_results = team_damage_results or []
    multi_piece_results = multi_piece_results or {}
    prob_lookup = prob_lookup or {}
    optimizer_candidates_by_char = optimizer_candidates_by_char or {}
    SLOT_ORDER = ["Flower", "Feather", "Sands", "Goblet", "Circlet"]

    slots_with_real_option = {(b["character"], b["slot"]) for b in recommendations}
    slots_with_real_option |= {(f["character"], f["slot"]) for f in flex_results}
    slots_with_real_option |= {(c["character"], c["slot"]) for c in ceiling_only_results}

    char_results_sorted = sorted(char_results, key=lambda r: -r["score"])
    domain_sorted = sorted(domain_results.items(), key=lambda kv: -kv[1]["score"])

    def badge(text):
        color = STATUS_COLOR.get(text, "#999")
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{text}</span>'

    def slot_marker(char_name, slot, s):
        if (char_name, slot) in slots_with_real_option:
            return ' <span title="A bench/flex candidate clears this slot\u2019s threshold - see Upgrade Options.">\u2191</span>'
        if s["upgradeable"]:
            return (
                ' <span style="color:#777;" title="A bench piece\u2019s optimistic ceiling beats what\u2019s equipped, '
                'but none clear your Good/Excellent threshold on expected value yet.">\u2303</span>'
            )
        return ""

    rows = []
    for r in char_results_sorted:
        slot_html = " ".join(
            f'<span title="{slot}: {s["roll_count"]} rolls equipped (need {s["good"]}/{s["excellent"]}); best bench candidate: EV {s["bench_expected"]}, max {s["bench_ceiling"]}">{badge(s["status"])}{slot_marker(r["name"], slot, s)}</span>'
            for slot, s in r["slots"].items()
        )

        ss = r.get("set_status", {})
        bonus_text = ss.get("active_bonus", "N/A")
        bonus_color = SET_BONUS_COLOR.get(bonus_text, "#999")
        if ss.get("target"):
            bonus_title = f'Target: {ss["target"]} · {ss.get("matching", "?")}/5 pieces match'
        else:
            bonus_title = "No target set configured"
        set_bonus_html = (
            f'<span title="{bonus_title}" style="background:{bonus_color};color:#fff;'
            f'padding:2px 8px;border-radius:10px;font-size:12px;">{bonus_text}</span>'
        )

        status_html = badge(r['status'])
        if r.get("set_bonus_mismatch"):
            status_html += (
                ' <span title="Roll quality looks great, but the set bonus isn\'t actually active - '
                'check equipped pieces against the target set." style="color:#e0a94e;">⚠</span>'
            )

        rows.append(f"""
        <tr class="char-row" data-character="{_esc_attr(r['name'])}">
        <td>{r['name']}</td>
        <td>{r['usage']}</td>
        <td>{r['role']}</td>
        <td>{status_html}</td>
        <td>{set_bonus_html}</td>
        <td>{r['completion']}/5</td>
        <td>{r['excellent_pieces']}</td>
        <td>{r['upgrades_good']} / {r['upgrades_excellent']}</td>
        <td>{slot_html}</td>
        <td>{r['domain']}</td>
        <td>{round(r['score'],1)}</td>
        </tr>""")

    domain_rows = []
    for name, d in domain_sorted:
        domain_rows.append(f"""
        <tr>
          <td>{name}</td>
          <td>{', '.join(d['characters'])}</td>
          <td>{round(d['score'],1)}</td>
          <td>{d['active']}</td>
          <td>{d['it_only']}</td>
        </tr>""")

    VERDICT_COLOR = {
        "Major Breakthrough": "#9c27b0",
        "Patch / Fix": "#e05a4e",
        "Luxury Upgrade": "#4ec97a",
        "Minor Polish": "#e0a94e",
        "Dead end": "#999999",
        "Flex Candidate": "#4e8ee0",
        "High Risk": "#b8860b",
    }

    def vbadge(text):
        color = VERDICT_COLOR.get(text, "#999")
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{text}</span>'

    def ibadge(text):
        color = INVENTORY_COLOR.get(text, "#999")
        label = text.replace("_", " ").title()
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{label}</span>'

    def substat_str(substats):
        parts = []
        for item in substats:
            if len(item) == 4:
                label, val, is_useful, is_unactivated = item
            else:
                label, val, is_useful = item
                is_unactivated = False

            text = f"{label} +{val}" if val else label
            if is_useful:
                text = f"<b>{text}</b>"
            if is_unactivated:
                text = f'{text} <span style="font-size:10px;color:#888;border:1px solid #555;padding:0 3px;border-radius:3px;">UNLOCKED</span>'

            parts.append(text)

        return ", ".join(parts)

    rec_rows = []
    for b in recommendations:
        gain = b["max_rolls"] - b["equipped_rolls"]
        slot_display = f"{b['slot']} <span style='font-size:11px;color:#88aaff;'>(Equipped)</span>" if b.get("is_self_equipped") else b['slot']
        opt_prob = b.get('optimal_probability')
        if opt_prob is None:
            # try to get from prob_lookup using artifact_id
            art_id = b.get('artifact_id')
            if art_id is not None:
                opt_prob = prob_lookup.get((b['character'], b['slot'], art_id), 0.0)
        opt_prob_display = f"{opt_prob}%" if opt_prob > 0 else "—"

        rec_rows.append(f"""
        <tr>
          <td>{b['character']}</td>
          <td>{slot_display}</td>
          <td>{b['set']}</td>
          <td>Main: {b['main_stat']}<br>{substat_str(b['substats'])}</td>
          <td>{b['rarity']}★ Lv{b['level']} ({b['levels_needed']} levels to max)</td>
          <td>{b['equipped_rolls']}</td>
          <td>{b['current_rolls']} → EV {b['expected_rolls']}<br>(Max {b['max_rolls']})</td>
          <td>{opt_prob_display}</td>
          <td>{vbadge(b['verdict'])}</td>
        </tr>""")

    flex_rows = []
    for f in flex_results:
        gain = round(f["expected_rolls"] - f["equipped_rolls"], 2)
        opt_prob = prob_lookup.get((f["character"], f["slot"], f.get("artifact_id")), 0.0)
        opt_prob_display = f"{opt_prob}%" if opt_prob > 0 else "—"
        flex_rows.append(f"""
        <tr>
          <td>{f['character']}</td>
          <td>{f['slot']}</td>
          <td>{f['set']} <span style="font-size:11px;color:#888;">(off-set)</span></td>
          <td>{f['rarity']}★ Lv{f['level']}</td>
          <td>{f['equipped_rolls']}</td>
          <td>{f['expected_rolls']}</td>
          <td style="color:#4ec97a;">+{gain}</td>
          <td>{opt_prob_display}</td>
        </tr>""")

    inventory_rows = []
    inventory_sorted = sorted(
        inventory_results,
        key=lambda i: ({"REVIEW": 0, "SANCTIFY_ELIXIR": 1, "SAFE_STRONGBOX": 2}.get(i["action"], 3), -i["ceiling"])
    )
    for i in inventory_sorted:
        art = i["artifact"]
        main_label = art.get("mainStatKey", "?")
        fits = i.get("fits", [])

        if fits:
            best = fits[0]
            best_fit_display = f"{best['character']}{'' if best['in_set'] else ' (off-set)'} — ceiling {best['ceiling']}"
            substats_display = substat_display_for(art, best["useful_stats"])
            alt_display = ", ".join(f"{f['character']} ({f['ceiling']})" for f in fits[1:]) or "—"
        else:
            best_fit_display = "No valid roster user"
            substats_display = substat_display_for(art, [])
            alt_display = "—"

        inventory_rows.append(f"""
        <tr>
        <td>{art.get('setKey', '?')}</td>
        <td>{i['slot']}</td>
        <td>{art.get('rarity', '?')}★ Lv{art.get('level', 0)}</td>
        <td>{main_label}</td>
        <td>{substats_display}</td>
        <td>{best_fit_display}</td>
        <td>{alt_display}</td>
        <td>{ibadge(i['action'])}</td>
        <td>{i['reason']}</td>
        </tr>""")

    strongbox_count = sum(1 for i in inventory_results if i["action"] == "SAFE_STRONGBOX")
    elixir_count = sum(1 for i in inventory_results if i["action"] == "SANCTIFY_ELIXIR")

    def stat_badge(text):
        color = STAT_TARGET_COLOR.get(text, "#999")
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{text}</span>'

    stat_target_rows = []
    for rep in sorted(stat_target_results, key=lambda r: (r["name"], r["context"] != "Default", r["context"])):
        context_display = (
            rep["context"] if rep["context"] == "Default"
            else f'{rep["context"]} <span style="font-size:11px;color:#888;">(team)</span>'
        )
        for stat, s in sorted(rep["stats"].items()):
            note = ""
            if stat in rep["over_target"]:
                note = '<span style="color:#4ec97a;">Deprioritize \u2014 redirect future rolls elsewhere</span>'
            elif stat in rep["under_target"]:
                note = '<span style="color:#e05a4e;">Still a priority</span>'
            sign = "+" if s["delta"] >= 0 else ""
            stat_target_rows.append(f"""
            <tr>
              <td>{rep['name']}</td>
              <td>{context_display}</td>
              <td>{stat}</td>
              <td>{s['current']}</td>
              <td>{s['target']}</td>
              <td>{sign}{s['delta']}</td>
              <td>{stat_badge(s['status'])}</td>
              <td>{note}</td>
            </tr>""")

    team_damage_rows = []
    for rep in sorted(team_damage_results, key=lambda r: (r["team"], r["name"])):
        scaling_display = rep["scaling_stat"] or '<span style="color:#888;">none tracked</span>'
        team_damage_rows.append(f"""
        <tr>
          <td>{rep['name']}</td>
          <td>{rep['team']}</td>
          <td>{scaling_display}</td>
          <td>{rep['crit_multiplier']}\u00d7</td>
          <td>{rep['stat_multiplier']}\u00d7</td>
          <td>{rep['dmg_multiplier']}\u00d7</td>
          <td>{rep['res_multiplier']}\u00d7</td>
          <td>{rep['reaction_multiplier']}\u00d7</td>
          <td><b>{rep['rdi']}</b></td>
        </tr>""")

    # -------------------------------------------------------------------------
    # MODAL GENERATION (UNIFIED)
    # -------------------------------------------------------------------------
    character_modal_html = {}

    for r in char_results:
        name = r["name"]
        char_cfg = roster.get(name, {}) if roster else {}
        useful_stats = [str(s) for s in char_cfg.get("useful_stats", [])]

        # --- Try to use optimizer candidates ---
        char_candidates = optimizer_candidates_by_char.get(name)
        if char_candidates:
            # Build a 5‑column grid with equipped + top candidates per slot
            options_columns = []
            for slot in SLOT_ORDER:
                candidates = char_candidates.get(slot, [])
                # Limit to top 5 (candidates already sorted by probability descending)
                top_candidates = candidates[:5]
                if not top_candidates:
                    options_columns.append(f"""
                    <div class="modal-slot-column">
                      <div class="modal-slot-column-head">{slot}</div>
                      <p class="modal-empty">No candidates found.</p>
                    </div>""")
                    continue

                # Build card for each candidate
                cards = []
                for c in top_candidates:
                    art = c["artifact"]
                    prob = c["probability"] * 100   # convert fraction to percent
                    prob_display = f"{prob:.1f}%" if prob > 0 else "—"
                    equipped_marker = " (Equipped)" if c.get("is_equipped", False) else ""
                    card = f"""
                    <div class="modal-card { 'equipped-card' if c.get('is_equipped', False) else '' }">
                      <div class="modal-card-head">{art.get('setKey', '?')}{equipped_marker}</div>
                      <div class="modal-card-body">
                        Main: {art.get('mainStatKey', '?')}<br>
                        {substat_display_for(art, useful_stats)}<br>
                        {art.get('rarity', '?')}★ Lv{art.get('level', 0)}<br>
                        <span style="color:#4e8ee0;">Build Optimality: {prob_display}</span>
                      </div>
                    </div>"""
                    cards.append(card)
                body = "".join(cards)
                options_columns.append(f"""
                <div class="modal-slot-column">
                  <div class="modal-slot-column-head">{slot}</div>
                  {body}
                </div>""")
            options_html = f'<div class="modal-slots-grid">{"".join(options_columns)}</div>'
            current_slots_html = ""   # not used; we merged everything
            upgrade_caption = (
                "<p style='color:#999;font-size:13px;margin-top:-4px;'>"
                "The currently equipped piece is marked. The sum of probabilities across candidates in a slot is approximately 100%."
                "</p>"
            )
        else:
            # --- Fallback to old separate equipped + upgrade sections ---
            equipped_dict = equipped_artifacts_by_char.get(name, {}) if equipped_artifacts_by_char else {}
            current_cards = []
            for slot in SLOT_ORDER:
                art = equipped_dict.get(slot)
                if not art:
                    current_cards.append(
                        f'<div class="modal-slot-column"><div class="modal-slot-column-head">{slot}</div>'
                        f'<p class="modal-empty">Empty</p></div>'
                    )
                    continue
                substat_html = substat_display_for(art, useful_stats) or "—"
                prob = prob_lookup.get((name, slot, art.get('id')), 0.0) if prob_lookup else 0.0
                prob_percent = prob * 100
                prob_display = f"{prob_percent:.1f}%" if prob_percent > 0 else "—"
                card = f"""
                <div class="modal-slot-column">
                  <div class="modal-slot-column-head">{slot}</div>
                  <div class="modal-card equipped-card" style="border-left: 3px solid #4e8ee0;">
                    <div class="modal-card-head">{art.get('setKey', '?')} <span style="font-size:11px;color:#888;">(Equipped)</span></div>
                    <div class="modal-card-body">
                      Main: {art.get('mainStatKey', '?')}<br>
                      {substat_html}<br>
                      {art.get('rarity', '?')}★ Lv{art.get('level', 0)}<br>
                      <span style="color:#4e8ee0;">Build Optimality: {prob_display}</span>
                    </div>
                  </div>
                </div>"""
                current_cards.append(card)
            current_slots_html = f'<div class="modal-slots-grid">{"".join(current_cards)}</div>'

            # Build upgrade options from recommendations/flex/ceiling
            options_by_slot = {slot: [] for slot in SLOT_ORDER}
            for b in [rec for rec in recommendations if rec["character"] == name]:
                prob = b.get('optimal_probability', 0.0)
                if prob == 0.0:
                    art_id = b.get('artifact_id')
                    if art_id is not None:
                        prob = prob_lookup.get((name, b['slot'], art_id), 0.0) if prob_lookup else 0.0
                prob_line = f"Build Optimality: {prob}%" if prob > 0 else ""
                options_by_slot.setdefault(b["slot"], []).append({
                    "verdict": b["verdict"],
                    "gain": b["expected_rolls"] - b["equipped_rolls"],
                    "opt_prob": prob,
                    "html": f"""
                    <div class="modal-card">
                      <div class="modal-card-head">{vbadge(b['verdict'])} {b['set']}</div>
                      <div class="modal-card-body">
                        Main: {b['main_stat']}<br>{substat_str(b['substats'])}<br>
                        {b['rarity']}★ Lv{b['level']} ({b['levels_needed']} levels to max)<br>
                        Equipped: {b['equipped_rolls']} rolls · This piece: {b['current_rolls']} → EV {b['expected_rolls']} (Max {b['max_rolls']})<br>
                        {prob_line}
                      </div>
                    </div>""",
                })
            for f in [fx for fx in flex_results if fx["character"] == name]:
                gain = round(f["expected_rolls"] - f["equipped_rolls"], 2)
                prob = prob_lookup.get((name, f['slot'], f.get('artifact_id')), 0.0) if prob_lookup else 0.0
                prob_line = f"Build Optimality: {prob}%" if prob > 0 else ""
                options_by_slot.setdefault(f["slot"], []).append({
                    "verdict": "Flex Candidate",
                    "gain": gain,
                    "opt_prob": prob,
                    "html": f"""
                    <div class="modal-card">
                      <div class="modal-card-head">{vbadge('Flex Candidate')} {f['set']} <span class="modal-offset-tag">(off-set)</span></div>
                      <div class="modal-card-body">
                        {f['rarity']}★ Lv{f['level']} · Equipped EV {f['equipped_rolls']} → Candidate EV {f['expected_rolls']}
                        <span style="color:#4ec97a;">(+{gain})</span><br>
                        {prob_line}
                      </div>
                    </div>""",
                })
            for c in [co for co in ceiling_only_results if co["character"] == name]:
                gain = c["max_rolls"] - c["equipped_rolls"]
                prob = prob_lookup.get((name, c['slot'], c.get('artifact_id')), 0.0) if prob_lookup else 0.0
                prob_line = f"Build Optimality: {prob}%" if prob > 0 else ""
                options_by_slot.setdefault(c["slot"], []).append({
                    "verdict": "High Risk",
                    "gain": gain,
                    "opt_prob": prob,
                    "html": f"""
                    <div class="modal-card high-risk">
                      <div class="modal-card-head">{vbadge('High Risk')} {c['set']}</div>
                      <div class="modal-card-body">
                        Main: {c['main_stat']}<br>{substat_str(c['substats'])}<br>
                        {c['rarity']}★ Lv{c['level']} ({c['levels_needed']} levels to max)<br>
                        Equipped: {c['equipped_rolls']} rolls · This piece: {c['current_rolls']} → EV {c['expected_rolls']} (Max {c['max_rolls']})<br>
                        <span style="color:#b8860b;font-size:11px;">Ceiling-only: best case beats equipped, but expected value doesn't clear your threshold.</span><br>
                        {prob_line}
                      </div>
                    </div>""",
                })
            options_columns = []
            for slot in SLOT_ORDER:
                candidates = options_by_slot.get(slot, [])
                candidates.sort(key=lambda c: (-c.get('opt_prob', 0.0), -c.get('gain', 0)))
                top5 = candidates[:5]
                body = "".join(c["html"] for c in top5) or '<p class="modal-empty">No options beat what’s equipped.</p>'
                options_columns.append(f"""
                <div class="modal-slot-column">
                  <div class="modal-slot-column-head">{slot}</div>
                  {body}
                </div>""")
            options_html = f'<div class="modal-slots-grid">{"".join(options_columns)}</div>'
            upgrade_caption = (
                "<p class='modal-options-caption'>Dashed amber cards are High Risk: best-case ceiling beats what's equipped, but expected value doesn't clear your threshold yet - a bigger gamble than anything else shown here.</p>"
            )

        # --- Multi-piece (unchanged) ---
        multi_piece_list = multi_piece_results.get(name, [])
        multi_piece_html = ""
        if multi_piece_list:
            rows_mp = []
            for idx, combo in enumerate(multi_piece_list, start=1):
                gain_pct = combo.get("gain", 0) * 100
                gain_str = f"{gain_pct:.1f}%"
                slots_swapped = "Multiple slots"
                synergy = combo.get("synergy", None)
                synergy_str = f"+{synergy*100:.1f}%" if synergy is not None and synergy > 0.005 else "—"
                rows_mp.append(f"""
                <tr>
                  <td>{idx}</td>
                  <td>{slots_swapped}</td>
                  <td style="color:#4ec97a;">{gain_str}</td>
                  <td>{synergy_str}</td>
                </tr>
                """)
            multi_piece_html = f"""
            <h4>Multi‑Piece Upgrade Pathways</h4>
            <p style="color:#999;font-size:13px;">Top multi‑slot combinations that improve damage when swapped together. Gain is relative to current build. Synergy calculation is not yet implemented (placeholder).</p>
            <table style="width:auto; margin-bottom:12px; font-size:13px;">
              <thead>
                <tr><th>Rank</th><th>Slots Swapped</th><th>Expected Gain</th><th>Synergy</th></tr>
              </thead>
              <tbody>{''.join(rows_mp)}</tbody>
            </table>
            """
        else:
            multi_piece_html = f"""
            <h4>Multi‑Piece Upgrade Pathways</h4>
            <p class="modal-empty">No multi‑piece combos found for this character.</p>
            """

        ss = r.get("set_status", {})
        target_line = (
            f"Set target: {ss['target']} ({ss.get('active_bonus', 'N/A')} active, {ss.get('matching', '?')}/5 matching)"
            if ss.get("target") else "No target set configured"
        )

        # Build modal HTML - unified view when optimizer data exists, else split view
        if optimizer_candidates_by_char.get(name):
            # Unified: only one grid
            character_modal_html[name] = f"""
            <div class="modal-meta">{r['usage']} · {r['role']} · Domain: {r['domain']} · Score {round(r['score'], 1)}<br>{target_line}</div>
            <h4>Artifact Candidates per Slot (sorted by Build Optimality)</h4>
            {upgrade_caption}
            {options_html}
            {multi_piece_html}
            """
        else:
            # Fallback: split view
            character_modal_html[name] = f"""
            <div class="modal-meta">{r['usage']} · {r['role']} · Domain: {r['domain']} · Score {round(r['score'], 1)}<br>{target_line}</div>
            <h4>Current Equipped Artifacts</h4>
            <p style="color:#999;font-size:13px;margin-top:-4px;">Build Optimality reflects how close this piece is to the best found for this slot.</p>
            {current_slots_html}
            <h4>Upgrade Options (top 5 per slot, sorted by Build Optimality)</h4>
            {upgrade_caption}
            {options_html}
            {multi_piece_html}
            """

    character_modal_json = json.dumps(character_modal_html).replace("</", "<\\/")

    # ------------------------ HTML template (unchanged) ------------------------
    if progress_changes is None:
        progress_html = (
            '<p style="color:#999;font-size:13px;">Snapshot not due yet — '
            'not enough time has passed since the last save, so no diff is shown this run.</p>'
        )
    elif len(progress_changes) == 0:
        progress_html = '<p style="color:#999;font-size:13px;">No changes since the last snapshot.</p>'
    else:
        items = "".join(f"<li>{line}</li>" for line in progress_changes)
        progress_html = f'<ul style="margin:0;padding-left:20px;line-height:1.6;">{items}</ul>'

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Artifact Farming Dashboard</title>
<style>
body {{ font-family: -apple-system, Segoe UI, sans-serif; background:#1a1a1a; color:#eee; padding:24px; }}
h1, h2 {{ font-weight: 600; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 32px; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; font-size: 14px; }}
th {{ cursor: pointer; background:#242424; position: sticky; top:0; }}
tr:hover {{ background: #242424; }}
.char-row {{ cursor: pointer; }}
.char-row:hover {{ background: #2a2a2a; }}

#charModalOverlay {{
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6);
  align-items: flex-start; justify-content: center; padding: 40px 16px; z-index: 1000;
}}
#charModal {{
  background: #1f1f1f; border: 1px solid #333; border-radius: 10px;
  max-width: 1100px; width: 96%; max-height: 88vh; overflow-y: auto;
  padding: 24px 28px; box-shadow: 0 12px 40px rgba(0,0,0,0.5);
}}
#charModal h3 {{ margin: 0 0 4px 0; font-size: 22px; }}
#charModal h4 {{ margin: 20px 0 8px 0; font-size: 14px; color: #aaa; text-transform: uppercase; letter-spacing: 0.04em; }}
#charModalClose {{
  float: right; background: none; border: none; color: #999; font-size: 20px;
  cursor: pointer; line-height: 1; padding: 4px 8px;
}}
#charModalClose:hover {{ color: #fff; }}
.modal-meta {{ color: #bbb; font-size: 13px; line-height: 1.6; }}
.modal-slot-row {{ padding: 8px 0; border-bottom: 1px solid #2c2c2c; font-size: 13px; }}
.modal-slot-name {{ display: inline-block; min-width: 70px; font-weight: 600; }}
.modal-slot-detail {{ color: #888; font-size: 12px; margin-top: 2px; }}
.modal-card {{ background: #262626; border-radius: 6px; padding: 10px 12px; margin-bottom: 8px; font-size: 13px; }}
.modal-card.high-risk {{ background: #262019; border: 1px dashed #b8860b; }}
.modal-card-head {{ margin-bottom: 4px; }}
.modal-card-body {{ color: #ccc; line-height: 1.5; }}
.modal-offset-tag {{ font-size: 11px; color: #888; margin-left: 4px; }}
.modal-empty {{ color: #999; font-size: 13px; }}
.modal-options-caption {{ color: #888; font-size: 11px; margin: -4px 0 8px 0; }}

.modal-slots-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }}
.modal-slot-column {{ background: #202020; border-radius: 6px; padding: 8px; min-height: 32px; }}
.modal-slot-column-head {{
  font-size: 11px; font-weight: 600; color: #aaa; text-transform: uppercase;
  letter-spacing: 0.04em; text-align: center; margin-bottom: 6px;
}}
.modal-slots-grid .modal-card {{ font-size: 12px; padding: 8px 10px; }}
.modal-slots-grid .modal-card-head {{ margin-bottom: 4px; }}

@media (max-width: 620px) {{
  .modal-slots-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}

.table-filter-wrap {{ margin-bottom: 10px; }}
.table-filter {{
  width: 100%; max-width: 320px; box-sizing: border-box;
  background: #242424; border: 1px solid #3a3a3a; border-radius: 6px;
  color: #eee; font-size: 13px; padding: 7px 10px;
}}
.table-filter::placeholder {{ color: #777; }}
.table-filter:focus {{ outline: none; border-color: #4e8ee0; }}
.table-filter-empty {{ color: #999; font-size: 13px; padding: 8px 12px; display: none; }}
.equipped-card {{ background: #1e2a3a; }}
</style></head>
<body>
<h1>Artifact Farming Dashboard</h1>

<div id="charModalOverlay">
  <div id="charModal">
    <button id="charModalClose" aria-label="Close">&times;</button>
    <h3 id="charModalName"></h3>
    <div id="charModalBody"></div>
  </div>
</div>

<h2>Progress Since Last Snapshot</h2>
{progress_html}

<h2>Characters (sorted by farming priority)</h2>
<p style="color:#999;font-size:13px;">Click a character row for a detail view: current slots and the best 5 upgrade/flex
options per slot. In the Slots column (here and in the detail view), <span style="color:#eee;">↑</span> means a specific bench or
flex piece clears this slot's threshold - see its Upgrade Options. A fainter <span style="color:#777;">⌃</span> means some bench
piece's optimistic ceiling beats what's equipped, but none clear the threshold on expected value yet, so nothing is listed below it.</p>
{_filter_input_html("charTable", "Filter by name, domain, status...")}
<table id="charTable">
<thead><tr><th>Character</th><th>Usage</th><th>Role</th><th>Status</th><th>Set Bonus</th><th>Completion</th>
<th>Excellent</th><th>Good / Exc Upgrades</th><th>Slots</th><th>Domain</th><th>Score</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>

<h2>Stat Targets (Phase 1-2)</h2>
<p style="color:#999;font-size:13px;max-width:750px;">Opt-in, manually-configured targets from stat_targets.yaml -
characters with none configured don't appear here. Each character can have a Default target plus per-team
overrides (Phase 2) shown side by side, since the same character can need different things in different teams
(e.g. less ER when someone else is holding the field). Totals are main stat + activated substats, assuming every
equipped piece is at max level (20 for 5-star), <b>not</b> its actual current level - treat these as "current at
full investment," not exact. Only ER/CR/CD/EM/ATK%/HP%/DEF% are supported; absolute totals like a real HP number
aren't yet, since that needs base-stat/weapon data this project doesn't track. See DAMAGE_CALCULATOR_DESIGN.md
for the roadmap.</p>
{_filter_input_html("statTargetTable", "Filter by character, team, or stat...")}
<table id="statTargetTable">
<thead><tr><th>Character</th><th>Context</th><th>Stat</th><th>Current</th><th>Target</th><th>Delta</th><th>Status</th><th>Note</th></tr></thead>
<tbody>{''.join(stat_target_rows) if stat_target_rows else '<tr><td colspan="8" style="color:#999;">No characters have stat targets configured yet \u2014 add them to stat_targets.yaml.</td></tr>'}</tbody>
</table>

<h2>Team Damage Context (early)</h2>
<p style="color:#999;font-size:13px;max-width:750px;">The Relative Damage Index (RDI) is <b>not</b> a real damage
number or a "% team damage" figure - it's a same-character comparison tool. Crit multiplier and scaling-stat
multiplier come from this character's own equipped stats; DMG/RES/reaction multipliers are hand-typed team
constants from teams.yaml, not derived from any mechanics formula. EM is intentionally excluded (its real
contribution depends on which reaction formula applies - see team_damage.py's module docstring). Useful for
sanity-checking the inputs and, later, for comparing two loadouts on the <b>same</b> character; not yet wired
into artifact recommendations. Pure supports whose value is a buff they give teammates (not their own hit) will
read as low RDI even when they're doing their job. See DAMAGE_CALCULATOR_DESIGN.md for the roadmap.</p>
{_filter_input_html("teamDamageTable", "Filter by character or team...")}
<table id="teamDamageTable">
<thead><tr><th>Character</th><th>Team</th><th>Scaling Stat</th><th>Crit</th><th>Stat</th><th>DMG</th><th>RES</th><th>Reaction</th><th>RDI</th></tr></thead>
<tbody>{''.join(team_damage_rows) if team_damage_rows else '<tr><td colspan="9" style="color:#999;">No teams configured yet \u2014 add them to teams.yaml.</td></tr>'}</tbody>
</table>

<h2>Domains (sorted by farming priority)</h2>
{_filter_input_html("domainTable", "Filter by domain or character...")}
<table id="domainTable">
<thead><tr><th>Domain</th><th>Characters</th><th>Score</th><th>Active</th><th>IT Only</th></tr></thead>
<tbody>{''.join(domain_rows)}</tbody>
</table>

<h2>Recommended Swaps (bench pieces that beat what's currently equipped)</h2>
<p style="color:#999;font-size:13px;max-width:750px;">"Ceiling" assumes every remaining upgrade lands on a useful
stat - not a guarantee, but tells you whether leveling this specific piece is worth the resin. <b>Bold</b> substats
are the ones that count for this character. Up to 3 candidates shown per slot when more than one qualifies.<br>
<b>Build Optimality</b> estimates how close the equipped build is to the best build found by the optimizer for this character's current objective. It is intended for comparing artifact quality, not predicting in-game damage. (<span style="color:#888;">—</span> means damage optimization is not applicable for this character.)</p>
{_filter_input_html("recTable", "Filter by character, slot, set, verdict...")}
<table id="recTable">
<thead><tr><th>Character</th><th>Slot</th><th>Set</th><th>Artifact</th><th>Level</th>
<th>Equipped Now</th><th>Current → Ceiling</th><th>Build Optimality</th><th>Verdict</th></tr></thead>
<tbody>{''.join(rec_rows)}</tbody>
</table>

<h2>Flex Slot Suggestions (4pc-locked characters only)</h2>
<p style="color:#999;font-size:13px;max-width:750px;">Off-set candidates for a single weak slot, evaluated without
breaking the character's 4pc bonus - the other four slots stay in-set. This does not price in the value of the 4pc
set bonus itself, so treat these as leads to sanity-check, not auto-swaps. <b>Build Optimality</b> displayed for informational purposes; flex pieces are evaluated independently.</p>
{_filter_input_html("flexTable", "Filter by character or slot...")}
<table id="flexTable">
<thead><tr><th>Character</th><th>Slot</th><th>Off-Set Candidate</th><th>Level</th>
<th>Equipped EV</th><th>Candidate EV</th><th>Gain</th><th>Build Optimality</th></tr></thead>
<tbody>{''.join(flex_rows) if flex_rows else '<tr><td colspan="8" style="color:#999;">No flex candidates cleared the EV-gain threshold.</td></tr>'}</tbody>
</table>

<h2>Inventory Cleanup ({strongbox_count} strongbox, {elixir_count} elixir fodder)</h2>
<p style="color:#999;font-size:13px;max-width:750px;">"Ceiling" is this artifact's maximum possible useful-roll
count for its single best-fit roster character - any character whose main-stat rules allow this slot, in-set or
not. <b>Bold</b> substats are the ones that count for that character. "Alt Homes" lists other characters this piece
could also work for, ranked by ceiling. <b>Review</b>: beats that character's current equipped piece for the slot.
<b>Sanctify Elixir</b>: loses to what's equipped, but has EXP invested (level 1+), so route to Elixir. <b>Safe
Strongbox</b>: loses to what's equipped, level 0, nothing lost either way.</p>
{_filter_input_html("inventoryTable", "Filter by set, slot, action, best fit...")}
<table id="inventoryTable">
<thead><tr><th>Set</th><th>Slot</th><th>Level</th><th>Main Stat</th><th>Substats</th>
<th>Best Fit</th><th>Alt Homes</th><th>Action</th><th>Reason</th></tr></thead>
<tbody>{''.join(inventory_rows) if inventory_rows else '<tr><td colspan="9" style="color:#999;">No unequipped artifacts found.</td></tr>'}</tbody>
</table>

<script>
document.querySelectorAll('table').forEach(table => {{
  const headers = table.querySelectorAll('th');
  headers.forEach((th, idx) => {{
    th.addEventListener('click', () => {{
      const rows = Array.from(table.querySelectorAll('tbody tr'));
      const asc = th.dataset.asc !== 'true';
      headers.forEach(h => h.dataset.asc = '');
      th.dataset.asc = asc;
      rows.sort((a, b) => {{
        const av = a.children[idx].innerText, bv = b.children[idx].innerText;
        const an = parseFloat(av), bn = parseFloat(bv);
        if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
        return asc ? av.localeCompare(bv) : bv.localeCompare(av);
      }});
      rows.forEach(r => table.querySelector('tbody').appendChild(r));
    }});
  }});
}});

document.querySelectorAll('.table-filter').forEach(input => {{
  const table = document.getElementById(input.dataset.table);
  if (!table) return;
  const emptyMsg = document.querySelector(`.table-filter-empty[data-empty-for="${{input.dataset.table}}"]`);
  input.addEventListener('input', () => {{
    const q = input.value.trim().toLowerCase();
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    let visibleCount = 0;
    rows.forEach(row => {{
      const match = q === '' || row.innerText.toLowerCase().includes(q);
      row.style.display = match ? '' : 'none';
      if (match) visibleCount++;
    }});
    if (emptyMsg) emptyMsg.style.display = (visibleCount === 0 && rows.length > 0) ? '' : 'none';
  }});
}});

const characterModalData = {character_modal_json};
const charModalOverlay = document.getElementById('charModalOverlay');
const charModalName = document.getElementById('charModalName');
const charModalBody = document.getElementById('charModalBody');

function openCharacterModal(name) {{
  const content = characterModalData[name];
  if (content === undefined) return;
  charModalName.textContent = name;
  charModalBody.innerHTML = content;
  charModalOverlay.style.display = 'flex';
}}

function closeCharacterModal() {{
  charModalOverlay.style.display = 'none';
}}

document.querySelectorAll('.char-row').forEach(row => {{
  row.addEventListener('click', () => openCharacterModal(row.dataset.character));
}});
document.getElementById('charModalClose').addEventListener('click', closeCharacterModal);
charModalOverlay.addEventListener('click', (e) => {{
  if (e.target === charModalOverlay) closeCharacterModal();
}});
document.addEventListener('keydown', (e) => {{
  if (e.key === 'Escape') closeCharacterModal();
}});
</script>
</body></html>"""
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"Wrote {out_path}")