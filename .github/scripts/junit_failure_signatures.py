"""List failed JUnit cases without changing the pytest result.

Missing XML files are an empty signature. A present but unreadable file
still raises, so a broken report is visible.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def signatures(paths: list[Path]) -> dict[str, object]:
    failures: list[dict[str, str]] = []
    for path in paths:
        if not path.is_file():
            continue
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            failure = case.find("failure")
            error = case.find("error")
            node = failure if failure is not None else error
            if node is None:
                continue
            message = (node.get("message") or node.text or "").strip().splitlines()
            failures.append(
                {
                    "file": path.name,
                    "classname": case.get("classname") or "",
                    "name": case.get("name") or "",
                    "kind": "failure" if failure is not None else "error",
                    "message": message[0] if message else "",
                }
            )
    return {"failure_count": len(failures), "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("xml", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = signatures([Path(item) for item in args.xml])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
