import os
import json
import time
import requests
import msal
import dateutil.parser
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

GRAPH_URL = "https://graph.microsoft.com/v1.0"
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 5


def data_dir() -> Path:
    """Base dir for downloads and sync state. Point it at a persistent disk in production."""
    return Path(os.getenv("INDEXING_DATA_DIR", "."))


def normalize_folder(value) -> str:
    """Library-relative folder path from a plain path or a pasted SharePoint browser URL.

    "3CX Recordings/recordings"  -> "3CX Recordings/recordings"
    ".../AllItems.aspx?id=%2FShared%20Documents%2F3CX%20Recordings%2F1055&viewid=..."
                                 -> "3CX Recordings/1055"
    """
    value = (value or "").strip()
    ids = parse_qs(urlparse(value).query).get("id")
    if ids:
        # `id` starts with the library's URL name ("Shared Documents"), preceded by
        # "sites/<name>" when the library isn't in the root site; neither is a folder
        parts = [p for p in ids[0].split("/") if p]
        skip = 3 if parts and parts[0].lower() in ("sites", "teams") else 1
        return "/".join(parts[skip:])
    return value.strip("/")


def db_key(path) -> str:
    """`metadata.filepath` stored in the DB: path relative to the data dir ("downloads/HR/a.pdf").

    Keeps the key stable when INDEXING_DATA_DIR changes (e.g. a mounted volume).
    """
    try:
        return str(Path(path).relative_to(data_dir()))
    except ValueError:
        return str(path)


class SharePointSyncBase:
    """Walks a SharePoint library and downloads new/modified files.

    A file is only recorded in the state file once the caller confirms it was
    indexed (`commit`), so a failed indexing run is retried on the next sync.
    Files that disappeared (or moved) in SharePoint are exposed in `removed` so
    the caller can drop their chunks and then call `apply_removals`.
    """

    def __init__(
        self,
        download_dir,
        state_file,
        site_path,
        doc_lib_name,
        root_folder=None,
        force=False,
        limit=None,
        version=None,
    ):
        self.tenant_id = os.getenv("OFFICE_365_TENANT_ID")
        self.client_id = os.getenv("OFFICE_365_CLIENT_ID")
        self.client_secret = os.getenv("OFFICE_365_CLIENT_SECRET")
        self.host_name = os.getenv("OFFICE_365_SITE_HOSTNAME")
        self.site_path = site_path
        self.doc_lib_name = doc_lib_name
        self.root_folder = normalize_folder(root_folder)  # e.g. "3CX Recordings"; empty = whole library

        self.download_dir = download_dir
        self.state_file = Path(state_file)
        self.force = force
        self.limit = limit  # max files to download per run; None = no cap
        self.version = version  # chunking version; files indexed with another one are re-indexed
        self.scopes = ["https://graph.microsoft.com/.default"]
        self.headers = None

        self.updated_files = []  # downloaded this run, waiting for commit
        self.removed = []  # [{"item_id": str | None, "path": str}]
        self._pending = {}  # str(local_path) -> (item_id, remote_mod)
        self._seen = set()
        self.sync_state = self._load_state()

    # --- Hooks ---
    def is_valid_file(self, name):
        return True

    # --- State ---
    def _load_state(self):
        if not self.state_file.exists():
            return {}
        with open(self.state_file, "r") as f:
            raw = json.load(f)
        # Legacy format was {item_id: modified}; the path is filled in when the file is seen again
        return {
            item_id: (
                entry
                if isinstance(entry, dict)
                else {"modified": entry, "path": None}
            )
            for item_id, entry in raw.items()
        }

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self.sync_state, f, indent=4)

    def commit(self, local_path):
        """Record a file as indexed. Call only after its chunks were stored."""
        item_id, remote_mod = self._pending.pop(str(local_path))
        self.sync_state[item_id] = {
            "modified": remote_mod,
            "path": str(local_path),
            "version": self.version,
        }
        self._save_state()

    def discard(self, entry):
        """Forget a removed file after its chunks were deleted."""
        Path(entry["path"]).unlink(missing_ok=True)
        if entry["item_id"]:
            self.sync_state.pop(entry["item_id"], None)
            self._save_state()

    def apply_removals(self, delete_file):
        """`delete_file(path) -> bool` drops the chunks; only successes are forgotten."""
        deleted = 0
        for entry in self.removed:
            if delete_file(entry["path"]):
                self.discard(entry)
                deleted += 1
        return deleted

    # --- Graph ---
    def authenticate(self):
        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
        )
        result = app.acquire_token_for_client(scopes=self.scopes)
        if "access_token" in result:
            self.headers = {"Authorization": f'Bearer {result["access_token"]}'}
        else:
            raise Exception(f"Auth failed: {result.get('error_description')}")

    def _get(self, url):
        """GET with token refresh on 401 and backoff on throttling / 5xx."""
        for attempt in range(MAX_RETRIES):
            resp = requests.get(url, headers=self.headers, timeout=60)
            if resp.status_code == 401 and attempt == 0:
                self.authenticate()
                continue
            if resp.status_code in RETRY_STATUS and attempt < MAX_RETRIES - 1:
                time.sleep(int(resp.headers.get("Retry-After", 2**attempt)))
                continue
            resp.raise_for_status()
            return resp

    def get_site_and_drive(self):
        # A site path must look like "/sites/Name"; anything else (e.g. a display name) falls back to the root site
        if self.site_path and self.site_path.startswith("/"):
            site_url = f"{GRAPH_URL}/sites/{self.host_name}:{self.site_path}"
        else:
            if self.site_path:
                print(
                    f"Ignoring site name '{self.site_path}' (not a path like /sites/Name); using root site."
                )
            site_url = f"{GRAPH_URL}/sites/{self.host_name}"
        site_id = self._get(site_url).json()["id"]

        drives = self._get(f"{GRAPH_URL}/sites/{site_id}/drives").json()["value"]
        drive_id = next((d["id"] for d in drives if d["name"] == self.doc_lib_name), None)
        if not drive_id:
            raise Exception("Drive not found")
        return site_id, drive_id

    def resolve_root(self, site_id, drive_id):
        """Returns (folder_id, local_path) where the walk starts.

        The local path keeps the folder name (downloads_audio/3CX Recordings/...) so the
        stored `filepath` and the category are the same as when walking the whole library.
        """
        if not self.root_folder:
            return "root", self.download_dir

        url = f"{GRAPH_URL}/sites/{site_id}/drives/{drive_id}/root:/{quote(self.root_folder)}"
        try:
            folder = self._get(url).json()
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 404:
                raise
            root = self._get(f"{GRAPH_URL}/sites/{site_id}/drives/{drive_id}/root/children")
            names = [i["name"] for i in root.json().get("value", []) if "folder" in i]
            raise Exception(
                f"Folder '{self.root_folder}' not found in '{self.doc_lib_name}'. "
                f"Folders at the library root: {names}"
            ) from e
        if "folder" not in folder:
            raise Exception(f"'{self.root_folder}' is not a folder")
        return folder["id"], self.download_dir / self.root_folder

    def walk_root(self, site_id, drive_id):
        folder_id, local_path = self.resolve_root(site_id, drive_id)
        return self.walk(site_id, drive_id, folder_id, local_path)

    def walk(self, site_id, drive_id, folder_id="root", current_path=None):
        """Yields (item, local_path) for every valid file under the folder."""
        if current_path is None:
            current_path = self.download_dir

        url = f"{GRAPH_URL}/sites/{site_id}/drives/{drive_id}/items/{folder_id}/children"
        while url:
            data = self._get(url).json()
            for item in data.get("value", []):
                local_path = current_path / item["name"]
                if "folder" in item:
                    yield from self.walk(site_id, drive_id, item["id"], local_path)
                elif "file" in item and self.is_valid_file(item["name"]):
                    yield item, local_path
            url = data.get("@odata.nextLink")

    def list_remote_paths(self):
        """Local paths of every valid remote file, without downloading anything."""
        self.authenticate()
        site_id, drive_id = self.get_site_and_drive()
        return {db_key(path) for _, path in self.walk_root(site_id, drive_id)}

    def download_file(self, url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading: {path}")
        partial = path.with_name(path.name + ".part")
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(partial, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
        partial.replace(path)

    def _needs_sync(self, item, local_path):
        if self.force or not local_path.exists():
            return True
        entry = self.sync_state.get(item["id"])
        if entry is None:
            return True
        if entry.get("version") != self.version:
            return True
        saved_time = dateutil.parser.isoparse(entry["modified"])
        remote_time = dateutil.parser.isoparse(item["lastModifiedDateTime"])
        return remote_time > saved_time

    def run(self):
        self.authenticate()
        site_id, drive_id = self.get_site_and_drive()
        print(f"Starting SharePoint Sync ({self.state_file.name})...")

        truncated = False
        for item, local_path in self.walk_root(site_id, drive_id):
            item_id = item["id"]
            self._seen.add(item_id)
            entry = self.sync_state.get(item_id)

            if entry and entry["path"] and entry["path"] != str(local_path):
                # Renamed or moved: chunks stored under the old path are stale
                self.removed.append({"item_id": None, "path": entry["path"]})
                needs_sync = True
            else:
                needs_sync = self._needs_sync(item, local_path)

            if needs_sync:
                self.download_file(item["@microsoft.graph.downloadUrl"], local_path)
                self._pending[str(local_path)] = (item_id, item["lastModifiedDateTime"])
                self.updated_files.append(local_path)
                if self.limit and len(self.updated_files) >= self.limit:
                    truncated = True
                    break
            elif entry and not entry["path"]:
                entry["path"] = str(local_path)  # backfill legacy state entry
                self._save_state()

        if truncated:
            # The walk stopped early, so unseen files can't be judged as deleted
            print(f"Limit of {self.limit} files reached; skipping deletion detection.")
        else:
            self._detect_removed()
        return self.updated_files

    def _detect_removed(self):
        """Only reached when the whole walk succeeded, so unseen means deleted remotely."""
        # With a root folder, files outside it were never listed, so they can't be judged as deleted
        root = self.download_dir / self.root_folder
        missing = [
            i
            for i, entry in self.sync_state.items()
            if i not in self._seen
            and (not self.root_folder or not entry["path"] or Path(entry["path"]).is_relative_to(root))
        ]
        if missing and not self._seen:
            print("Remote listing is empty; skipping deletion detection.")
            return
        for item_id in missing:
            path = self.sync_state[item_id]["path"]
            if path:
                self.removed.append({"item_id": item_id, "path": path})
            else:
                # Legacy entry with unknown path: its chunks can't be located here
                print(f"Dropping legacy state entry {item_id} (path unknown).")
                self.sync_state.pop(item_id)
                self._save_state()
