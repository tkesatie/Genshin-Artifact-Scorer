class Artifact:
    def __init__(self, substats, unactivatedSubstats,
                 mainStatKey, mainStatValue, location=None):
        self.substats = substats
        self.unactivatedSubstats = unactivatedSubstats
        self.mainStatKey = mainStatKey
        self.mainStatValue = mainStatValue
        self.location = location