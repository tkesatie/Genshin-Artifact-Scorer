# Import necessary modules
from typing import List, Dict
from artifact_utils import valid_main_stat
from bench import project_artifact_to_max

def projected_inherent_value(artifact: dict, useful_stats: list, roll_values: dict) -> float:
    """Project the artifact to max level, then compute its inherent value."""
    projected = project_artifact_to_max(artifact, roll_values)
    return inherent_value(projected, useful_stats, roll_values)

class Artifact:
    def __init__(self, substats: List[Dict], unactivatedSubstats: List[Dict],
                 mainStatKey: str, mainStatValue: float, location: str = None):
        self.substats = substats
        self.unactivatedSubstats = unactivatedSubstats
        self.mainStatKey = mainStatKey
        self.mainStatValue = mainStatValue
        self.location = location

def inherent_value(artifact: Artifact, useful_stats: List[str], roll_values: Dict[str, float]) -> float:
    val = 0.0
    for sub in artifact.substats + artifact.unactivatedSubstats:
        if sub['key'] in useful_stats:
            val += sub['value'] / roll_values[sub['key']]
    return val

def get_top_k_candidates(character_config: Dict, inventory_artifacts: List[Artifact],
                         roll_values: Dict, current_artifacts: Dict[str, Artifact],
                         k: int = 5) -> Dict[str, List[Artifact]]:
    result = {}
    slots = ['flower', 'feather', 'sands', 'goblet', 'circlet']
    useful_stats = character_config['useful_stats']

    for slot in slots:
        # 1. Filter: valid main stat and unequipped
        filtered = [
            art for art in inventory_artifacts
            if valid_main_stat(art, character_config, slot)
            and (art.get('location') is None or art.get('location') == '')
        ]

        # 2. Score each artifact by its projected max‑level value
        scored = []
        for art in filtered:
            score = projected_inherent_value(art, useful_stats, roll_values)
            scored.append((score, art))

        # 3. Sort descending by score
        scored.sort(key=lambda x: x[0], reverse=True)

        # 4. Take top k (but we’ll ensure the equipped piece is included)
        top_k = [art for _, art in scored[:k]]

        # 5. Ensure equipped artifact is always in the list
        equipped = current_artifacts.get(slot)
        if equipped is not None:
            # If equipped is not already in top_k, replace the lowest‑scoring candidate
            if equipped not in top_k:
                # Remove the last (lowest score) candidate if we have k already
                if len(top_k) == k:
                    top_k.pop()  # drop the worst
                top_k.append(equipped)
            # If equipped is already there, no change needed

        # 6. (Optional) You may want to re‑sort to show scores descending after swap
        # but it's not necessary for correctness – the dashboard can display in any order.

        result[slot] = top_k  # always length == k (or less if not enough artifacts)

    return result