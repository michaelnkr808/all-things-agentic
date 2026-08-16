"""Cloud tool factories (Malik).

Each factory closes over a fixed resource_id (a folder ID, dataset name,
bucket+prefix, ...) so the resulting tool can only ever touch that one
resource. The planner never sees these — it only ever picks a department
by name (config.py). Not wired into the default filesystem gatherer; this
is the cloud fallback path.

Only the Drive folder tool is fully implemented as of now, see TODOs.

The Drive tools need the optional ``google-api-python-client`` package
(``pip install google-api-python-client``) — imports are deferred so this
module loads without it.
"""

from __future__ import annotations

import io

from google.adk.tools import FunctionTool


def make_drive_folder_tools(folder_id: str) -> list[FunctionTool]:
    """Returns [list_files, download_file] tools scoped to one folder."""

    def _drive_service():
        import google.auth
        from googleapiclient.discovery import build

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        return build("drive", "v3", credentials=credentials)

    def list_files_in_folder() -> list[dict]:
        """Lists all files in a drive folder."""
        service = _drive_service()
        results = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="files(id, name, mimeType, modifiedTime)",
            )
            .execute()
        )
        return results.get("files", [])

    def download_file(file_id: str) -> str:
        """Downloads a file by ID (must come from list_files_in_folder) and returns its local path."""
        service = _drive_service()
        meta = service.files().get(fileId=file_id, fields="parents,name").execute()
        if folder_id not in meta.get("parents", []):
            raise ValueError("File is not in the allowed folder.")

        request = service.files().get_media(fileId=file_id)
        path = f"/tmp/{meta['name']}"
        from googleapiclient.http import MediaIoBaseDownload

        with io.FileIO(path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return path

    return [FunctionTool(list_files_in_folder), FunctionTool(download_file)]


def make_bigquery_tools(dataset_id: str) -> list[FunctionTool]:
    """TODO: wire up google.adk.tools.BigQueryToolset here, scoped to
    dataset_id with write_mode=WriteMode.BLOCKED."""
    raise NotImplementedError("BigQuery tools")


def make_gcs_prefix_tools(bucket_and_prefix: str) -> list[FunctionTool]:
    """TODO: wrap google cloud storage client calls (list_blobs / download)
    scoped to bucket_and_prefix, mirroring the Drive pattern above."""
    raise NotImplementedError("Google cloud storage prefix tools")
