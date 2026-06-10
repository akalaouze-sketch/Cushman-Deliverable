#!/usr/bin/env python3
"""Print a recovered agent trace as a tree in the terminal.

Usage:
    python scripts/show_trace.py            # list recent runs
    python scripts/show_trace.py latest     # the most recent run's tree
    python scripts/show_trace.py <run_id>   # a specific run's tree
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents import trace  # noqa: E402

KIND_TAG = {"llm": "🧠", "agent": "🤖", "tool": "🔧", "io": "📄",
            "reasoning": "💭", "event": "•", "function": "ƒ"}


def _line(node: dict, depth: int) -> None:
    pad = "  " * depth
    tag = KIND_TAG.get(node["kind"], "·")
    status = "" if node["status"] == "ok" else f" [{node['status']}]"
    meta = node.get("meta") or {}
    extra = ""
    if node["kind"] == "llm":
        toks = f"{meta.get('input_tokens', '?')}→{meta.get('output_tokens', '?')} tok"
        extra = f"  ({meta.get('model', '')}, {toks})"
    print(f"{pad}{tag} {node['name']}  {node['duration_ms']}ms{status}{extra}")
    out = node.get("output")
    if out is not None and node["kind"] in ("llm", "reasoning"):
        s = str(out).replace("\n", " ")
        print(f"{pad}    ↳ {s[:120]}")
    for child in node.get("children", []):
        _line(child, depth + 1)


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if not arg:
        rows = trace.load_index(30)
        if not rows:
            print("no traces yet — run something first (parse a PDF, hit /api/report).")
            return
        print(f"{'RUN ID':28} {'NAME':16} {'STATUS':8} {'ms':>7}  spans")
        for r in rows:
            print(f"{r['id']:28} {r['name']:16} {r['status']:8} {r['duration_ms']:>7}  {r['span_count']}")
        print("\nrun:  python scripts/show_trace.py <run_id|latest>")
        return
    data = trace.load_run(arg)
    if not data:
        print(f"trace '{arg}' not found")
        return
    print(f"\n■ RUN {data['id']}  ·  {data['name']}  ·  {data['status']}  ·  {data['duration_ms']}ms"
          f"  ·  {data['span_count']} spans")
    if data.get("input") is not None:
        print(f"  input:  {str(data['input'])[:140]}")
    if data.get("output") is not None:
        print(f"  output: {str(data['output'])[:140]}")
    print("-" * 78)
    for node in data.get("tree", []):
        _line(node, 0)


if __name__ == "__main__":
    main()
