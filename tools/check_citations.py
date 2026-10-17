"""Check that every reference-list entry in the e-Portfolio draft is cited in the body.

Reads the submission draft, splits the body (before the References heading) from the
reference list, extracts a leading surname from each reference entry, and reports
whether that surname appears in the body text.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DRAFT = Path(__file__).with_name("eportfolio_tasks_1-4.md")


def main() -> int:
    text = DRAFT.read_text(encoding="utf-8")

    marker = "\n## References"
    if marker not in text:
        print("FAIL: no '## References' heading found")
        return 2

    body, _, tail = text.partition(marker)
    # Reference list ends at the next top-level heading (Appendix A).
    refs_block = tail.split("\n## Appendix")[0]

    entries = [
        line.strip()
        for line in refs_block.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    print(f"Body words: {len(body.split())}")
    print(f"Reference entries: {len(entries)}\n")

    uncited: list[str] = []
    for entry in entries:
        surname = re.match(r"([A-Z][A-Za-z\-']+)", entry)
        if not surname:
            continue
        name = surname.group(1)
        # Body citation forms: "Name (2019)", "(Name, 2019)", "Name et al.", "Name and X".
        # Corporate authors often appear in text by acronym, e.g. entry
        # "National Institute of Standards and Technology (NIST) (2023)" cited as "(NIST, 2023)".
        acronym = re.match(r"[A-Za-z .,'\-]+?\(([A-Z]{2,10})\)", entry)
        patterns = [rf"\b{re.escape(name)}\b"]
        if acronym:
            patterns.append(rf"\b{re.escape(acronym.group(1))}\b")
        cited = any(re.search(p, body) for p in patterns)
        flag = "cited  " if cited else "UNCITED"
        if not cited:
            uncited.append(entry)
        print(f"{flag} {entry[:78]}")

    print()
    if uncited:
        print(f"{len(uncited)} reference(s) not cited in the body:")
        for entry in uncited:
            print(f"  - {entry[:100]}")
        return 1
    print("All reference-list entries appear in the body text.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
