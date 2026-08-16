"""Drive structure setup (Malik) — build the department tree for the demo.

Creates the shared-drive folder layout that maps 1:1 onto the departments
in config/environment.yaml, uploads sample files (including the locked `hr`
department), grants the service account read access, and prints the
folder_ids to paste into each department's `storage.folder_id`.

Usage (from repo root, with a service account as ADC):
    python -m agentic.gatherers.cloud.drive_setup --drive-id <shared_drive_id>
    python -m agentic.gatherers.cloud.drive_setup --create-drive "AllThingsAgentic"

Idempotent: existing folders/files are reused, only missing ones are made.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from agentic.gatherers.cloud.drive import _FOLDER_MIME, _service

# department name -> {file path in the dept -> file content}
SAMPLE_TREE: dict[str, dict[str, str]] = {
    "engineering": {
        "roadmap.md": "# Engineering roadmap\n\nQ3: ship the agent fleet.\n",
        "q3-spend.md": "# Q3 engineering spend\n\nCloud + tooling: 420k USD.\n",
        "sprint-notes/q3-retrospective.txt": "Retro: permissions gate landed on time.\n",
    },
    "finance": {
        "q3-budget.gsheet": "category,amount_usd\ncloud,420000\nlegal,85000\n",  # exported -> csv
        "vendor-contracts/aws-q3.csv": "vendor,annual_usd\nAWS,180000\n",
    },
    "hr": {
        "comp-bands.md": "# Compensation bands\n\nL5: 200-260k USD.\n",
    },
}


def _service_account_email(service) -> str | None:
    from google.auth import default

    creds, _ = default()
    email = getattr(creds, "service_account_email", None)
    return email


def _find_folder(service, drive_id: str, name: str, parent_id: str) -> str | None:
    page = (
        service.files()
        .list(
            q=f"'{parent_id}' in parents and name = '{name}' and mimeType = '{_FOLDER_MIME}' and trashed = false",
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            driveId=drive_id,
        )
        .execute()
    )
    files = page.get("files", [])
    return files[0]["id"] if files else None


def _mkdir(service, drive_id: str, name: str, parent_id: str) -> str:
    existing = _find_folder(service, drive_id, name, parent_id)
    if existing:
        return existing
    created = (
        service.files()
        .create(
            body={
                "name": name,
                "mimeType": _FOLDER_MIME,
                "parents": [parent_id],
            },
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )
    return created["id"]


def _mkdirs(service, drive_id: str, parent_id: str, rel_dir: str) -> str:
    current = parent_id
    for part in rel_dir.split("/"):
        if not part:
            continue
        current = _mkdir(service, drive_id, part, current)
    return current


def _upload_file(service, drive_id: str, parent_id: str, name: str, content: str) -> None:
    existing = _find_by_name(service, drive_id, name, parent_id)
    if existing:
        return
    service.files().create(
        body={"name": name, "parents": [parent_id]},
        media_body=content.encode("utf-8"),
        media_type="text/plain",
        fields="id",
        supportsAllDrives=True,
    ).execute()


def _find_by_name(service, drive_id: str, name: str, parent_id: str) -> str | None:
    page = (
        service.files()
        .list(
            q=f"'{parent_id}' in parents and name = '{name}' and trashed = false",
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            driveId=drive_id,
        )
        .execute()
    )
    files = page.get("files", [])
    return files[0]["id"] if files else None


def _create_shared_drive(service, name: str) -> str:
    created = (
        service.drives()
        .create(
            requestId=str(uuid.uuid4()),
            body={"name": name},
        )
        .execute()
    )
    return created["id"]


def _grant_sa(service, drive_id: str, sa_email: str) -> None:
    if not sa_email:
        return
    service.permissions().create(
        fileId=drive_id,
        body={"role": "reader", "type": "user", "emailAddress": sa_email},
        supportsAllDrives=True,
    ).execute()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--drive-id", help="existing shared drive id")
    group.add_argument("--create-drive", metavar="NAME", help="create a new shared drive")
    parser.add_argument("--skip-grant", action="store_true", help="do not grant the SA reader access")
    args = parser.parse_args()

    service = _service()
    if args.create_drive:
        drive_id = _create_shared_drive(service, args.create_drive)
        print(f"Created shared drive: {args.create_drive} (id: {drive_id})")
    else:
        drive_id = args.drive_id

    ids: dict[str, str] = {}
    for dept, files in SAMPLE_TREE.items():
        folder_id = _mkdir(service, drive_id, dept, drive_id)
        ids[dept] = folder_id
        for rel, content in files.items():
            dir_name, _, file_name = rel.rpartition("/")
            parent = _mkdirs(service, drive_id, folder_id, dir_name) if dir_name else folder_id
            _upload_file(service, drive_id, parent, file_name, content)

    _mkdir(service, drive_id, "_meta", drive_id)

    if not args.skip_grant:
        sa_email = _service_account_email(service)
        if sa_email:
            _grant_sa(service, drive_id, sa_email)
            print(f"Granted {sa_email} reader access on shared drive {drive_id}")
        else:
            print("No service-account email on current credentials; skipping grant.", file=sys.stderr)

    print("\nPaste these into config/environment.yaml under each department's `storage.folder_id`:")
    for dept, fid in ids.items():
        print(f"  {dept}: {fid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
