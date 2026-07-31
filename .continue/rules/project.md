# Artifact Scorer Project

## File Access Rules

Do not read entire files unless necessary.

For investigation:
1. Search for relevant functions/classes first.
2. Read only the relevant sections.
3. Avoid loading score.py completely unless the task requires full-file understanding.

## Structure

The project root contains:
- score.py
- test.py

There is no src directory.

Use paths relative to the project root.

## Main file

score.py contains:
- artifact parsing
- scoring calculations
- EV calculations
- recommendation logic

When investigating bugs, start with score.py.