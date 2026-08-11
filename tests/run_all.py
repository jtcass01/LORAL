"""Run every test and report a pass/fail table.

Each test is a standalone script that asserts its own invariants and exits
non-zero on failure, so no test framework is required.

Every test runs with the PROJECT ROOT as its working directory. The tests
reference source as 'src/robot' and datasets by bare filename, so the runner
owns that convention rather than each test hard-coding a path back up the tree.

    python tests/run_all.py            # everything
    python tests/run_all.py robot      # one group
    python tests/run_all.py -v         # show output from failures
"""
import argparse
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = pathlib.Path(__file__).resolve().parent

# Tests needing a dataset that may not be present in a fresh checkout. They are
# skipped rather than failed, since their absence is not a code defect.
NEEDS_DATA = {
    "test_real_stream.py": [],
    "test_decode_loop.py": ["experiment_data/C_data_rate_sweep/comms_sweep_v6.jsonl"],
    "test_generator.py": [],
}


def discover(groups):
    out = []
    for group in sorted(p.name for p in TESTS.iterdir() if p.is_dir()):
        if groups and group not in groups:
            continue
        for f in sorted((TESTS / group).glob("test_*.py")):
            out.append((group, f))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("groups", nargs="*", help="Limit to these subdirectories.")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Print captured output for failures.")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    tests = discover(args.groups)
    if not tests:
        raise SystemExit("no tests found")

    print(f"running {len(tests)} tests from {ROOT}\n")
    results = []
    for group, path in tests:
        missing = [d for d in NEEDS_DATA.get(path.name, []) if not (ROOT / d).exists()]
        if missing:
            print(f"  {group}/{path.name:<28} SKIP  (needs {', '.join(missing)})")
            results.append((group, path.name, "SKIP", 0.0, ""))
            continue

        t0 = time.time()
        try:
            proc = subprocess.run([sys.executable, str(path)], cwd=ROOT,
                                  capture_output=True, text=True,
                                  timeout=args.timeout)
            ok = proc.returncode == 0
            output = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            ok, output = False, f"timed out after {args.timeout:.0f}s"
        dt = time.time() - t0
        status = "PASS" if ok else "FAIL"
        print(f"  {group}/{path.name:<28} {status}  {dt:5.1f}s")
        results.append((group, path.name, status, dt, output))

    npass = sum(1 for r in results if r[2] == "PASS")
    nfail = sum(1 for r in results if r[2] == "FAIL")
    nskip = sum(1 for r in results if r[2] == "SKIP")
    print(f"\n{npass} passed, {nfail} failed, {nskip} skipped")

    if args.verbose or nfail:
        for group, name, status, _, output in results:
            if status == "FAIL":
                print(f"\n{'=' * 70}\nFAILED {group}/{name}\n{'=' * 70}")
                print(output[-2500:] if output else "(no output)")

    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
