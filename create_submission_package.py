"""create_submission_package.py -- one-command submission
packager for the FNA 670 Analytical Appendix deliverable.

What this script does (in order):
  1. Executes analytical_appendix.ipynb top to bottom via
     nbconvert (--execute, --inplace) so the saved notebook
     carries fresh cell outputs.
  2. Exports the executed notebook as static HTML.
  3. Writes a top-level SUBMISSION_README.txt with the
     freeze identity, environment requirements, and
     directory structure documentation.
  4. Packages the notebook + HTML + notebook_data/ +
     SUBMISSION_README.txt into a single ZIP archive named
     forest_capital_analytical_appendix_<date>.zip.
  5. Prints SHA-256 + MD5 checksums so submission integrity
     can be verified.

Run:
    python create_submission_package.py

The script is the one-step submission packager. Running it
from the repo root with the venv activated produces a
submission-ready ZIP with no further manual steps.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path
from textwrap import dedent

# ── Constants ─────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent
NOTEBOOK = REPO_ROOT / "analytical_appendix.ipynb"
NOTEBOOK_HTML = REPO_ROOT / "analytical_appendix.html"
NOTEBOOK_DATA = REPO_ROOT / "notebook_data"
SUBMISSION_README = REPO_ROOT / "SUBMISSION_README.txt"

STRATEGY_HASH = "f2e87dec7dcabe71"
LAST_DATE = "2026-05-31"
N_ROWS = 287
N_STRATEGIES = 10

# Package versions and environment requirements. Kept in
# sync with the notebook's Cell 2 _PINS dict by hand -- if
# Cell 2 ever changes, update here too.
REQUIREMENTS = {
    "python": ">=3.11",
    "pandas": ">=2.0,<3.0",
    "numpy":  ">=1.24,<3.0",
    "scipy":  ">=1.10,<2.0",
    "matplotlib": ">=3.7,<4.0",
    "jupyter": "any recent",
    "nbconvert": "any recent",
}


# ── Helpers ───────────────────────────────────────────────────


def run(cmd: list[str], description: str) -> None:
    """Run a subprocess; raise SystemExit on non-zero return."""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FAILED: {description}")
        print(f"  stderr: {result.stderr[-2000:]}")
        print(f"  stdout: {result.stdout[-1000:]}")
        sys.exit(1)
    print(f"  OK: {description}")


def write_submission_readme() -> None:
    """Top-level README placed inside the ZIP at the root so
    a grader's first action (unzip + read) immediately
    surfaces the freeze identity, the run instructions, and
    the directory layout."""
    today = date.today().isoformat()
    requirements_block = "\n".join(
        f"  {pkg:14s} {ver}" for pkg, ver in REQUIREMENTS.items())
    content = dedent(f"""\
    FOREST CAPITAL PORTFOLIO INTELLIGENCE SYSTEM
    Analytical Appendix (Deliverable 2)
    FNA 670 MSFA Practicum -- Queens University

    Submission date:    {today}
    Strategy hash:      {STRATEGY_HASH}
    Last data date:     {LAST_DATE}
    Observations:       {N_ROWS} monthly rows
    Strategies:         {N_STRATEGIES}

    The notebook in this archive is fully self-contained.
    It reads only from the notebook_data/ directory; there
    are no network calls, no database queries, and no
    proprietary libraries.

    ENVIRONMENT REQUIREMENTS
    ------------------------
    {requirements_block}

    REPRODUCING THE NOTEBOOK
    ------------------------
    Open analytical_appendix.html for the executed notebook
    with all outputs and charts inline. To re-execute from
    source:

        python -m venv venv
        source venv/bin/activate   # Linux/Mac
        # venv\\Scripts\\activate    # Windows
        pip install pandas numpy scipy matplotlib jupyter nbconvert
        jupyter nbconvert --execute --to notebook --inplace \\
            analytical_appendix.ipynb

    A successful end-to-end run with no exception raised in
    Cell 6 is the integrity proof: every cached metric in
    strategy_results.json has been reproduced from raw
    monthly returns within 1e-3 absolute tolerance for
    return-derived metrics and 5e-3 for RF-dependent ratios.

    DIRECTORY STRUCTURE
    -------------------
    analytical_appendix.ipynb   The notebook (source)
    analytical_appendix.html    The notebook (executed,
                                with outputs inline) -- the
                                canonical reading artifact
                                for the grader
    notebook_data/              The static data freeze:
      monthly_returns.csv         287 rows of equity/IG/HY
      ff_factors.csv              Fama-French 3-factor + RF
      rebalance_events.csv        9 council rebalance events
      strategy_results.json       10 strategies w/ full metrics
      README.md                   Per-file column dictionary
                                  and provenance notes

    SUBMISSION TEAM
    ---------------
    Michael Ruurds  (lead engineer, notebook author)
    Bob Thao        (analyst, narrative review)
    Molly Murdock   (final presentation author)
    """)
    SUBMISSION_README.write_text(content, encoding="utf-8")
    print(f"  Wrote {SUBMISSION_README.name}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Main ──────────────────────────────────────────────────────


def main() -> None:
    print("=" * 60)
    print("Forest Capital -- Analytical Appendix submission")
    print("=" * 60)

    # Pre-flight: the four pieces must exist.
    for required in (NOTEBOOK, NOTEBOOK_DATA):
        if not required.exists():
            print(f"FATAL: {required} not found")
            sys.exit(1)

    # Step 1 -- execute the notebook inplace.
    print("\n[1/5] Executing the notebook end-to-end ...")
    run([
        sys.executable, "-m", "jupyter", "nbconvert",
        "--to", "notebook", "--execute", "--inplace",
        "--ExecutePreprocessor.timeout=300",
        str(NOTEBOOK),
    ], "notebook executed top to bottom")

    # Step 2 -- export to HTML.
    print("\n[2/5] Exporting to HTML ...")
    run([
        sys.executable, "-m", "jupyter", "nbconvert",
        "--to", "html",
        str(NOTEBOOK),
    ], "HTML export written")
    if not NOTEBOOK_HTML.exists():
        print(f"FATAL: {NOTEBOOK_HTML} not produced by nbconvert")
        sys.exit(1)

    # Step 3 -- write the SUBMISSION_README.txt.
    print("\n[3/5] Writing SUBMISSION_README.txt ...")
    write_submission_readme()

    # Step 4 -- package into ZIP.
    today_str = date.today().strftime("%Y-%m-%d")
    zip_name = (
        f"forest_capital_analytical_appendix_{today_str}.zip")
    zip_path = REPO_ROOT / zip_name
    print(f"\n[4/5] Packaging {zip_name} ...")
    with zipfile.ZipFile(
        zip_path, "w", zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zf:
        # Top-level files
        zf.write(NOTEBOOK, arcname=NOTEBOOK.name)
        zf.write(NOTEBOOK_HTML, arcname=NOTEBOOK_HTML.name)
        zf.write(
            SUBMISSION_README, arcname=SUBMISSION_README.name)
        # notebook_data/ contents
        for f in sorted(NOTEBOOK_DATA.iterdir()):
            if f.is_file():
                zf.write(
                    f, arcname=f"notebook_data/{f.name}")
    print(f"  Wrote {zip_path}")
    print(f"  Archive size: {zip_path.stat().st_size:,} bytes")

    # Step 5 -- checksums.
    print(f"\n[5/5] Checksums for {zip_name}:")
    sha = sha256_file(zip_path)
    md5 = md5_file(zip_path)
    print(f"  SHA-256: {sha}")
    print(f"  MD5:     {md5}")

    print()
    print("=" * 60)
    print("SUBMISSION PACKAGE READY")
    print("=" * 60)
    print(f"Archive:  {zip_path}")
    print(f"Identity: strategy_hash={STRATEGY_HASH} "
          f"last_date={LAST_DATE} n_rows={N_ROWS}")


if __name__ == "__main__":
    main()
