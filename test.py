import json

from config import load_configs
from artifact_utils import resolve_artifact_rolls


def validate_artifact_rolls(good_json, roll_values):
    unresolved = 0
    resolved_mismatch = 0

    for art in good_json.get("artifacts", []):
        resolved = resolve_artifact_rolls(
            art,
            roll_values
        )

        if resolved is None:
            unresolved += 1

            if unresolved <= 10:
                print(
                    f"\nUnresolved #{unresolved}"
                    f"\nSet: {art.get('setKey')}"
                    f"\nSlot: {art.get('slotKey')}"
                    f"\nLevel: {art.get('level')}"
                    f"\nRarity: {art.get('rarity')}"
                    f"\nGOOD: {art.get('totalRolls')}"
                    f"\nSubstats: {art.get('substats')}"
                )

            continue

        calculated = (
            len(art.get("substats", []))
            + sum(resolved["rolls"].values())
        )

        if calculated != art.get("totalRolls"):
            resolved_mismatch += 1

            if resolved_mismatch <= 10:
                print(
                    f"\nResolved mismatch #{resolved_mismatch}"
                    f"\nSet: {art.get('setKey')}"
                    f"\nSlot: {art.get('slotKey')}"
                    f"\nLevel: {art.get('level')}"
                    f"\nRarity: {art.get('rarity')}"
                    f"\nCalculated: {calculated}"
                    f"\nGOOD: {art.get('totalRolls')}"
                    f"\nResolved rolls: {resolved['rolls']}"
                    f"\nSubstats: {art.get('substats')}"
                )

    print()
    print(f"Unresolved artifacts: {unresolved}")
    print(f"Resolved mismatches: {resolved_mismatch}")


def test_validate_artifact_rolls():
    roster, rules, roll_values = load_configs()

    with open("genshin_data.json", "r", encoding="utf-8") as f:
        good_json = json.load(f)

    validate_artifact_rolls(good_json, roll_values)


if __name__ == "__main__":
    test_validate_artifact_rolls()