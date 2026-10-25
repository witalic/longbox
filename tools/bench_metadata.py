#!/usr/bin/env python
"""How much a bulk metadata change costs (design/metadata-management.md §8).

Generates a library straight onto disk, then renames one tag held by a share
of it twice over the same titles: once the way the older passes write (a load,
a save and a full re-index per title, chapter sidecars and all), once through
the operation engine. Run it with the repo's venv, against a local disk and
against a network share:

    .venv/Scripts/python.exe tools/bench_metadata.py
    .venv/Scripts/python.exe tools/bench_metadata.py --titles 10000 --vault //nas/share/bench

The vault is generated in a throwaway directory unless --vault names one; a
named vault must not exist yet, and is left in place for a second run with
--reuse.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.library import operations as ops  # noqa: E402
from app.library.migrations import CURRENT_SCHEMA  # noqa: E402
from app.library.service import Library  # noqa: E402

TAGS = [f"tag {i}" for i in range(400)]


def generate(root: Path, titles: int, holding: float, chapters: int, seed: int = 7) -> None:
    rnd = random.Random(seed)
    for i in range(titles):
        tid = f"t-{i:06d}"
        d = root / "manga" / tid
        (d / "chapters").mkdir(parents=True)
        tags = rnd.sample(TAGS, 8) + (["bench old"] if rnd.random() < holding else [])
        rows = [{"id": f"ch-{n}-{tid}", "num": str(n), "lang": "EN", "group": "g"}
                for n in range(1, chapters + 1)]
        doc = {"schema": CURRENT_SCHEMA,
               "meta": {"title": f"Title {i}", "type": "manga", "tags": tags,
                        "authors": [f"author {rnd.randrange(800)}"]},
               "provenance": {"tags": {"origin": "auto"}},
               "chapters": rows, "user": {}}
        (d / "title.json").write_text(json.dumps(doc), encoding="utf-8")
        for r in rows:
            (d / "chapters" / f"{r['id']}.json").write_text(
                json.dumps({"pages": 20, "size": 4_000_000, "kind": "pages"}), encoding="utf-8")


def old_path(lib: Library, ids: list[str], frm: str, to: str) -> None:
    """How the passes before the engine write: per title, a load, a save and
    a full re-index — a commit per title."""
    for tid in ids:
        with lib.vault.title_lock(tid):
            doc = lib.vault.save_meta_value(
                tid, lambda d: setattr(d.meta, "tags", [to if t == frm else t for t in d.meta.tags]))
            if doc is not None:
                lib._index(tid, doc)


def timed(label: str, fn) -> float:
    t = time.perf_counter()
    fn()
    s = time.perf_counter() - t
    print(f"  {label:<34} {s:8.2f} s")
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--titles", type=int, default=10_000)
    ap.add_argument("--holding", type=float, default=0.5, help="share of titles holding the tag")
    ap.add_argument("--chapters", type=int, default=5, help="chapters (sidecars) per title")
    ap.add_argument("--vault", type=Path, help="where to generate (default: a temp dir)")
    ap.add_argument("--reuse", action="store_true", help="use an existing --vault as it is")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a Windows console is cp125x

    tmp = None if args.vault else Path(tempfile.mkdtemp(prefix="lb-bench-"))
    root = args.vault or tmp
    try:
        if not args.reuse:
            if args.vault and args.vault.exists():
                sys.exit(f"{args.vault} exists — pass --reuse to run against it as it is")
            print(f"generating {args.titles:,} titles × {args.chapters} chapters in {root} …")
            timed("generate", lambda: generate(root, args.titles, args.holding, args.chapters))
        print("opening (index fill) …")
        holder: dict[str, Library] = {}
        timed("open + index", lambda: holder.setdefault("lib", Library(root)))
        lib = holder["lib"]
        # a fresh vault runs its one-time migrations in the background, walking
        # every title — measured alongside them, both paths would be measuring them
        if lib._normalize_thread is not None:
            timed("one-time migrations", lib._normalize_thread.join)
        ids = [tid for tid, doc in ops.in_scope(lib, None, {}) if "bench old" in doc.meta.tags]
        print(f"{len(ids):,} of {lib.count():,} titles hold the tag\n")

        print("old path — load, save, full re-index, one commit per title")
        t_old = timed(f"rename on {len(ids):,} titles", lambda: old_path(lib, ids, "bench old", "bench mid"))

        acts = ops.check([ops.Action(kind="rename", field="tags", values=["bench mid"], to="bench new")])
        print("\nengine — plan from the index, one read + one write per title, batched index")
        docs: dict[str, list] = {}
        timed("scope (index)", lambda: docs.setdefault("d", ops.in_scope(lib, None, {})))
        timed("preview", lambda: ops.plan(lib, docs["d"], acts))
        t_new = timed(f"run on {len(ids):,} titles",
                      lambda: ops.run(lib, ops.in_scope(lib, None, {}), acts, summary="bench", scope={}))
        print(f"\n  per changed title: old {t_old / max(len(ids), 1) * 1000:.2f} ms · "
              f"engine {t_new / max(len(ids), 1) * 1000:.2f} ms · {t_old / max(t_new, 1e-9):.1f}× faster")
        lib.close()
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
