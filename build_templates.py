#!/usr/bin/env python3
"""
VectraSpace v11 — build_templates.py

Run this ONCE after uploading the refactored files alongside vectraspace.py.
It extracts every HTML constant from the monolith and writes them into
templates_loader.py, replacing all the __XXX_HTML_CONTENT__ placeholders.

Usage:
    python build_templates.py               # reads vectraspace.py in same dir
    python build_templates.py --src path/to/vectraspace.py

After running, you can delete (or keep) vectraspace.py — templates_loader.py
is now fully self-contained.
"""

import argparse
import ast
import re
import sys
from pathlib import Path

# Maps placeholder token → variable name in vectraspace.py
TARGETS = {
    "__DASHBOARD_HTML_CONTENT__":         "DASHBOARD_HTML",
    "__SCENARIOS_HTML_CONTENT__":         "SCENARIOS_HTML",
    "__CALC_HTML_CONTENT__":              "CALC_HTML",
    "__GLOSSARY_HTML_CONTENT__":          "GLOSSARY_HTML",
    "__RESEARCH_HTML_CONTENT__":          "RESEARCH_HTML",
    "__EDU_ORBITAL_HTML_CONTENT__":       "_EDU_ORBITAL_HTML",
    "__EDU_COLLISION_HTML_CONTENT__":     "_EDU_COLLISION_HTML",
    "__EDU_PERTURBATIONS_HTML_CONTENT__": "_EDU_PERTURBATIONS_HTML",
    "__EDU_DEBRIS_HTML_CONTENT__":        "_EDU_DEBRIS_HTML",
    "__ADMIN_HTML_CONTENT__":             "ADMIN_HTML",
    "__LANDING_HTML_CONTENT__":           "LANDING_HTML",
}


def extract_string_constant(source: str, var_name: str) -> str | None:
    """
    Extract the value of a top-level string assignment from Python source.
    Handles triple-quoted strings and concatenated strings via ast.literal_eval.
    """
    # Pattern: VAR_NAME = """..."""  or VAR_NAME = "..."
    # We locate the assignment then let ast parse the value.
    pattern = re.compile(
        r'^' + re.escape(var_name) + r'\s*=\s*',
        re.MULTILINE,
    )
    match = pattern.search(source)
    if not match:
        return None

    # Slice from start of the RHS to end of file and try to parse
    rhs_start = match.end()
    chunk = source[rhs_start:]

    # Try progressively longer slices until ast.parse succeeds
    # (triple-quoted strings can be very long)
    for end in range(100, len(chunk) + 1, 500):
        snippet = chunk[:end]
        try:
            tree = ast.parse(f"_x = {snippet}", mode="exec")
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    value = ast.literal_eval(node.value)
                    if isinstance(value, str):
                        return value
        except SyntaxError:
            continue
        except Exception:
            continue

    return None


def main():
    parser = argparse.ArgumentParser(description="Extract HTML from monolith into templates_loader.py")
    parser.add_argument("--src", default="vectraspace.py",
                        help="Path to monolith source (default: vectraspace.py)")
    parser.add_argument("--loader", default="templates_loader.py",
                        help="Path to templates_loader.py (default: templates_loader.py)")
    args = parser.parse_args()

    src_path    = Path(args.src)
    loader_path = Path(args.loader)

    if not src_path.exists():
        print(f"ERROR: Source file not found: {src_path}")
        print("       Make sure vectraspace.py is in the same directory and try again.")
        sys.exit(1)

    if not loader_path.exists():
        print(f"ERROR: templates_loader.py not found: {loader_path}")
        sys.exit(1)

    print(f"Reading monolith: {src_path} ({src_path.stat().st_size / 1024:.0f} KB)")
    source = src_path.read_text(encoding="utf-8")

    loader_text = loader_path.read_text(encoding="utf-8")

    replaced = 0
    failed   = []

    for placeholder, var_name in TARGETS.items():
        if placeholder not in loader_text:
            print(f"  SKIP  {var_name:40s} — placeholder not found in loader (already replaced?)")
            continue

        print(f"  ...   {var_name:40s} extracting...", end="", flush=True)
        value = extract_string_constant(source, var_name)

        if value is None:
            print(f" FAILED")
            failed.append(var_name)
            continue

        # repr() produces a safe Python string literal (handles quotes/escapes)
        literal = repr(value)
        loader_text = loader_text.replace(f'"{placeholder}"', literal, 1)
        print(f" OK  ({len(value):,} chars)")
        replaced += 1

    loader_path.write_text(loader_text, encoding="utf-8")

    print(f"\n✓ Replaced {replaced}/{len(TARGETS)} constants in {loader_path}")

    if failed:
        print(f"\nWARNING: Could not extract: {', '.join(failed)}")
        print("         Check that these variable names exist in the monolith.")
        sys.exit(2)
    else:
        print("✓ templates_loader.py is now fully self-contained.")
        print("  You can now deploy without vectraspace.py.\n")


if __name__ == "__main__":
    main()
