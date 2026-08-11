"""Static checks on the LaTeX sources that a build would otherwise surface late.

Catches the failure modes that are silent until compile time or, worse, that
compile cleanly and render wrong:

  * \\input paths that do not resolve to a file
  * \\cite keys with no matching bib entry (these render as [?])
  * \\includegraphics targets that are missing from the figures directory
  * \\ref labels with no matching \\label

Run from the project root:  python tools/check_paper.py
"""
import pathlib
import re
import sys

PAPER = pathlib.Path("docs/IEEE Paper")
FIGDIR = PAPER / "figures"


def tex_sources():
    return [PAPER / "main.tex"] + sorted((PAPER / "sections").glob("*.tex"))


def read(p):
    return p.read_text(encoding="utf-8", errors="replace")


def main():
    problems = []
    main_tex = read(PAPER / "main.tex")
    body = "\n".join(read(p) for p in tex_sources())

    # --- \input targets ---
    for rel in re.findall(r"\\input\{([^}]+)\}", main_tex):
        rel = rel if rel.endswith(".tex") else rel + ".tex"
        if not (PAPER / rel).exists():
            problems.append(f"\\input target missing: {rel}")

    # --- citations ---
    # Only real entries: anchored to line start, and excluding the @STRING
    # macros that fill IEEEabrv.bib. A looser pattern matches those macros and
    # the brace-laden comments around them, which produced hundreds of junk
    # "keys" and a false undefined-citation report.
    bib = "\n".join(read(p) for p in PAPER.glob("*.bib"))
    defined = set(re.findall(
        r"^@(?!STRING|COMMENT|PREAMBLE)[A-Za-z]+\s*\{\s*([^,\s{}]+)\s*,",
        bib, re.M | re.I))
    used = set()
    for group in re.findall(r"\\cite\{([^}]*)\}", body):
        used |= {k.strip() for k in group.split(",") if k.strip()}
    for key in sorted(used - defined):
        problems.append(f"undefined citation (renders as [?]): {key}")
    unused = sorted(defined - used)

    # --- figures ---
    figs = set(re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", body))
    for f in sorted(figs):
        stem = pathlib.Path(f).stem
        if not any((FIGDIR / f"{stem}{e}").exists() for e in (".pdf", ".png", ".eps", "")):
            problems.append(f"figure missing from figures/: {f}")

    # --- labels and refs ---
    labels = set(re.findall(r"\\label\{([^}]+)\}", body))
    refs = set(re.findall(r"\\ref\{([^}]+)\}", body))
    for r in sorted(refs - labels):
        problems.append(f"\\ref to undefined label: {r}")

    print(f"sources    {len(tex_sources())} tex files")
    print(f"citations  {len(used)} used, {len(defined)} defined")
    print(f"figures    {len(figs)} referenced")
    print(f"labels     {len(labels)} defined, {len(refs)} referenced")
    if unused:
        print(f"\nnote: bib entries defined but never cited: {', '.join(unused)}")

    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nno problems found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
