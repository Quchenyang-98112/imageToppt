#!/usr/bin/env python3
"""Review every gallery-eligible non-text asset in bounded Qwen-VL batches.

The prior batch executor capped a slide at ten assets, which left dense pages
with unreviewed icons.  This global executor keeps the same local Top-K
comparison contract, but partitions all asset IDs into bounded calls and writes
one complete, traceable adjudication per source page.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
from qwen_env import load_project_env


load_project_env(Path(__file__))


def priority(asset: dict) -> float:
    bbox = asset.get("bbox") or [0, 0, 0, 0]
    area = float(bbox[2]) * float(bbox[3])
    words = (str(asset.get("semantic", "")) + " " + str(asset.get("role", ""))).lower()
    return area + (25000 if any(word in words for word in ("logo", "icon", "chart", "arrow", "ribbon", "illustration", "flow")) else 0)


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--matches-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    script = TOOLS_ROOT / "qwen_gallery_rerank.py"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[dict] = []
    for audit_path in sorted(args.audit_dir.glob("*.nontext.audit.json"), key=lambda p: p.name):
        stem = audit_path.name.replace(".nontext.audit.json", "")
        source = next((p for p in args.source_dir.iterdir() if p.is_file() and p.stem == stem), None)
        matches = args.matches_dir / f"{stem}.matches.json"
        if source is None or not matches.exists():
            continue
        audit = read(audit_path)
        eligible = [asset for asset in audit.get("imagegenAssets", []) if asset.get("id")]
        eligible.sort(key=priority, reverse=True)
        for index, asset_ids in enumerate(chunks([asset["id"] for asset in eligible], max(1, args.batch_size)), 1):
            jobs.append({
                "stem": stem,
                "index": index,
                "source": source,
                "audit": audit_path,
                "matches": matches,
                "asset_ids": asset_ids,
                "tmp": args.output_dir / "batches" / f"{stem}.batch-{index:02d}.json",
            })

    def invoke(job: dict) -> dict:
        job["tmp"].parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(script),
            "--source", str(job["source"]),
            "--audit", str(job["audit"]),
            "--matches", str(job["matches"]),
            "--output", str(job["tmp"]),
            "--max-assets", str(len(job["asset_ids"])),
            "--asset-ids", ",".join(job["asset_ids"]),
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return {"job": job, "ok": result.returncode == 0 and job["tmp"].exists(), "detail": (result.stdout or result.stderr)[-1000:]}

    completed: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for result in pool.map(invoke, jobs):
            completed.append(result)

    by_stem: dict[str, list[dict]] = {}
    for result in completed:
        by_stem.setdefault(result["job"]["stem"], []).append(result)
    summaries: list[dict] = []
    for stem, results in sorted(by_stem.items()):
        selections: list[dict] = []
        missing: list[str] = []
        for result in sorted(results, key=lambda x: x["job"]["index"]):
            if not result["ok"]:
                missing.extend(result["job"]["asset_ids"])
                continue
            selections.extend(read(result["job"]["tmp"]).get("selections", []))
        reviewed = {row.get("elementId") for row in selections if row.get("elementId")}
        missing.extend(asset_id for result in results for asset_id in result["job"]["asset_ids"] if asset_id not in reviewed)
        payload = {
            "schema": "qwen-gallery-rerank-complete/v1",
            "model": "qwen3-vl-plus",
            "source": str(next(result["job"]["source"] for result in results)),
            "batchCount": len(results),
            "selections": selections,
            "unreviewedAssetIds": sorted(set(missing)),
            "status": "completed" if not missing else "blocked_unreviewed_assets",
        }
        target = args.output_dir / f"{stem}.rerank-complete.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append({
            "stem": stem,
            "approved": sum(1 for selection in selections if selection.get("approved")),
            "rejected": sum(1 for selection in selections if not selection.get("approved")),
            "unreviewed": len(payload["unreviewedAssetIds"]),
            "status": payload["status"],
        })
    manifest = {
        "schema": "qwen-gallery-rerank-complete-batch/v1",
        "workers": args.workers,
        "batchSize": args.batch_size,
        "slides": summaries,
        "failedJobs": [
            {"stem": result["job"]["stem"], "batch": result["job"]["index"], "detail": result["detail"]}
            for result in completed if not result["ok"]
        ],
    }
    (args.output_dir / "rerank-complete-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    if manifest["failedJobs"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
