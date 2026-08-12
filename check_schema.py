#!/usr/bin/env python3
"""Check that both schemas are well-formed LinkML, and report their rules.

The other checks read the YAML directly and so cannot see a construct LinkML
would reject: a misspelled `value_presence`, a rule naming a slot the class does
not have, an import that does not resolve. Loading through SchemaView is what
makes those errors surface, because it is the loader the ecosystem uses.

Rules get a line each. They are the only constraints stated in storage and
dropped by the projection, so `review/validate_record.py` evaluates them against
extraction records; listing them here is how a rule that stops being enforced
becomes visible.

Usage:
    python3 check_schema.py
"""

from __future__ import annotations

from pathlib import Path
import sys

from linkml_runtime.utils.schemaview import SchemaView

ROOT = Path(__file__).resolve().parent
SCHEMAS = (
    ROOT / "neuroimaging-study-storage.yaml",
    ROOT / "neuroimaging-study-extraction.yaml",
)


def report(path: Path) -> list[str]:
    """Load one schema and return its problems, printing what it holds."""

    problems: list[str] = []
    view = SchemaView(str(path))
    classes = view.all_classes()
    print(f"{path.name}: {len(classes)} classes, {len(view.all_enums())} enums")

    for class_name, definition in classes.items():
        slots = set(view.class_slots(class_name))
        for rule in definition.rules or []:
            named = {
                slot
                for block in (rule.preconditions, rule.postconditions)
                if block is not None
                for slot in (block.slot_conditions or {})
            }
            unknown = sorted(named - slots)
            if unknown:
                problems.append(
                    f"{class_name}: a rule names {', '.join(unknown)}, "
                    f"which {class_name} does not have"
                )
            print(f"  rule {class_name}: {rule.description}")

    return problems


def main() -> int:
    problems: list[str] = []
    for path in SCHEMAS:
        try:
            problems.extend(report(path))
        except Exception as error:  # noqa: BLE001 -- any load failure is the finding
            problems.append(f"{path.name}: will not load -- {error}")

    if problems:
        print("\nproblems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nBoth schemas load and every rule names slots its class has.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
