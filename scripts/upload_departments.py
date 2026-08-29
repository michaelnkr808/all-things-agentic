"""Upload department data to a GCS bucket (no gcloud/gsutil needed).

    GOOGLE_APPLICATION_CREDENTIALS=~/gcp-key.json \
    PYTHONPATH=src .venv/bin/python scripts/upload_departments.py \
        --bucket cartographer-demo --department finance

Object keys are `<department>/<relative path>`, which is exactly the layout
the GCS adapter expects: the department name is the prefix, and the prefix is
the containment root, so nothing outside it can ever be listed.
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path

from agentic.env import load_env

DATA_ROOT = Path("data/departments")


def upload(bucket_name: str, departments: list[str], dry_run: bool = False,
           project: str | None = None) -> int:
    bucket = None
    if not dry_run:
        # Only touch credentials when actually uploading, so --dry-run works
        # before a service-account key exists.
        from google.cloud import storage

        # User ADC carries no default project, so it has to come from
        # GOOGLE_CLOUD_PROJECT (loaded from .env) or --project.
        bucket = storage.Client(project=project).bucket(bucket_name)
    sent = 0

    for dept in departments:
        root = DATA_ROOT / dept
        if not root.is_dir():
            print(f"  ! no such department directory: {root}", file=sys.stderr)
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(root).as_posix()
            key = f"{dept}/{rel}"
            size = path.stat().st_size
            print(f"  {key}  ({size:,}b)")
            if not dry_run:
                blob = bucket.blob(key)
                ctype = mimetypes.guess_type(path.name)[0] or "text/plain"
                blob.upload_from_filename(str(path), content_type=ctype)
            sent += 1

    return sent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Upload department data to GCS.")
    ap.add_argument("--bucket", required=True)
    ap.add_argument(
        "--department", action="append", dest="departments", default=None,
        help="repeatable; defaults to every directory under data/departments/",
    )
    ap.add_argument("--dry-run", action="store_true", help="list what would be sent")
    ap.add_argument("--project", default=None, help="overrides GOOGLE_CLOUD_PROJECT")
    args = ap.parse_args(argv)

    load_env()
    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not args.dry_run and not project:
        ap.error("no project: set GOOGLE_CLOUD_PROJECT in .env or pass --project")

    departments = args.departments or sorted(
        p.name for p in DATA_ROOT.iterdir() if p.is_dir()
    )
    print(f"{'DRY RUN: ' if args.dry_run else ''}uploading to gs://{args.bucket}/")
    n = upload(args.bucket, departments, args.dry_run, project)
    print(f"{n} object(s) {'listed' if args.dry_run else 'uploaded'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
