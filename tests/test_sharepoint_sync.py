import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from sharepoint_sync import SharePointSyncBase, db_key, normalize_folder  # noqa: E402


def file_item(item_id, name, modified="2026-01-01T00:00:00Z"):
    return {
        "id": item_id,
        "name": name,
        "file": {},
        "lastModifiedDateTime": modified,
        "@microsoft.graph.downloadUrl": f"https://download/{item_id}",
    }


class FakeSync(SharePointSyncBase):
    """Base class with the Graph calls replaced by an in-memory tree: {folder_id: [items]}."""

    folder_ids = {"3CX Recordings": "f3", "3CX Recordings/sub": "f4"}

    def __init__(self, tree, root, **kwargs):
        super().__init__(
            download_dir=root / "downloads",
            state_file=root / "state.json",
            site_path="/sites/X",
            doc_lib_name="Lib",
            **kwargs,
        )
        self.tree = tree

    def is_valid_file(self, name):
        return name.endswith(".pdf")

    def authenticate(self):
        pass

    def get_site_and_drive(self):
        return "site", "drive"

    def _get(self, url):
        if "/root:/" in url:  # folder lookup by path
            name = unquote(url.split("/root:/")[1])
            if name not in self.folder_ids:
                raise Exception(f"Folder '{name}' not found")
            return type("Resp", (), {"json": lambda _: {"id": self.folder_ids[name], "folder": {}}})()
        folder_id = url.split("/items/")[1].split("/children")[0]
        items = self.tree[folder_id]

        class Resp:
            def json(self_inner):
                return {"value": items}

        return Resp()

    def download_file(self, url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(url)


class SharePointSyncTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def sync(self, tree, **kwargs):
        s = FakeSync(tree, self.root, **kwargs)
        s.run()
        return s

    def test_invalid_extensions_are_not_downloaded(self):
        s = self.sync({"root": [file_item("1", "a.pdf"), file_item("2", "b.xyz")]})
        self.assertEqual([p.name for p in s.updated_files], ["a.pdf"])

    def test_state_only_written_on_commit(self):
        s = self.sync({"root": [file_item("1", "a.pdf")]})
        self.assertFalse((self.root / "state.json").exists())

        # Not committed (indexing failed) -> next run downloads it again
        again = self.sync({"root": [file_item("1", "a.pdf")]})
        self.assertEqual(len(again.updated_files), 1)

        s.commit(s.updated_files[0])
        saved = json.loads((self.root / "state.json").read_text())
        self.assertEqual(saved["1"]["modified"], "2026-01-01T00:00:00Z")

        unchanged = self.sync({"root": [file_item("1", "a.pdf")]})
        self.assertEqual(unchanged.updated_files, [])

    def test_modified_file_is_resynced(self):
        tree = {"root": [file_item("1", "a.pdf")]}
        s = self.sync(tree)
        s.commit(s.updated_files[0])

        newer = self.sync({"root": [file_item("1", "a.pdf", "2026-02-01T00:00:00Z")]})
        self.assertEqual(len(newer.updated_files), 1)

    def test_force_ignores_state(self):
        s = self.sync({"root": [file_item("1", "a.pdf")]})
        s.commit(s.updated_files[0])
        forced = self.sync({"root": [file_item("1", "a.pdf")]}, force=True)
        self.assertEqual(len(forced.updated_files), 1)

    def test_deleted_remote_file_is_reported_and_forgotten(self):
        s = self.sync({"root": [file_item("1", "a.pdf"), file_item("2", "b.pdf")]})
        for p in s.updated_files:
            s.commit(p)

        after = self.sync({"root": [file_item("1", "a.pdf")]})
        gone = str(self.root / "downloads" / "b.pdf")
        self.assertEqual(after.removed, [{"item_id": "2", "path": gone}])

        # A failed chunk deletion keeps the entry so it is retried
        self.assertEqual(after.apply_removals(lambda path: False), 0)
        self.assertIn("2", after.sync_state)

        self.assertEqual(after.apply_removals(lambda path: True), 1)
        self.assertNotIn("2", after.sync_state)
        self.assertFalse(Path(gone).exists())

    def test_empty_listing_does_not_wipe_everything(self):
        s = self.sync({"root": [file_item("1", "a.pdf")]})
        s.commit(s.updated_files[0])
        after = self.sync({"root": []})
        self.assertEqual(after.removed, [])

    def test_moved_file_reports_old_path(self):
        s = self.sync({"root": [file_item("1", "a.pdf")]})
        s.commit(s.updated_files[0])

        tree = {"root": [{"id": "d", "name": "sub", "folder": {}}], "d": [file_item("1", "a.pdf")]}
        moved = self.sync(tree)
        old = str(self.root / "downloads" / "a.pdf")
        self.assertEqual(moved.removed, [{"item_id": None, "path": old}])
        self.assertEqual(len(moved.updated_files), 1)

    def test_legacy_state_is_migrated_without_resync(self):
        tree = {"root": [file_item("1", "a.pdf")]}
        first = self.sync(tree)  # creates the local file
        (self.root / "state.json").write_text(json.dumps({"1": "2026-01-01T00:00:00Z"}))

        s = self.sync(tree)
        self.assertEqual(s.updated_files, [])
        self.assertEqual(s.sync_state["1"]["path"], str(first.updated_files[0]))

    def test_root_folder_keeps_folder_in_local_path_and_ignores_the_rest(self):
        tree = {
            "root": [file_item("9", "outside.pdf")],
            "f3": [file_item("1", "a.pdf")],
        }
        s = self.sync(tree, root_folder="3CX Recordings")
        self.assertEqual(s.updated_files, [self.root / "downloads" / "3CX Recordings" / "a.pdf"])

    def test_deletion_detection_only_applies_inside_root_folder(self):
        outside = str(self.root / "downloads" / "other" / "x.pdf")
        inside = str(self.root / "downloads" / "3CX Recordings" / "gone.pdf")
        (self.root / "state.json").write_text(
            json.dumps(
                {
                    "x": {"modified": "2026-01-01T00:00:00Z", "path": outside},
                    "g": {"modified": "2026-01-01T00:00:00Z", "path": inside},
                }
            )
        )
        s = self.sync({"f3": [file_item("1", "a.pdf")]}, root_folder="3CX Recordings")
        self.assertEqual(s.removed, [{"item_id": "g", "path": inside}])

    def test_missing_root_folder_fails_loudly(self):
        with self.assertRaises(Exception):
            self.sync({"root": []}, root_folder="Nope")

    def test_normalize_folder(self):
        self.assertEqual(normalize_folder("3CX Recordings"), "3CX Recordings")
        self.assertEqual(normalize_folder("/3CX Recordings/"), "3CX Recordings")
        self.assertEqual(normalize_folder(None), "")
        url = (
            "Shared%20Documents/Forms/AllItems.aspx?id=%2FShared%20Documents%2F3CX%20Recordings"
            "%2Frecordings%2F1055&viewid=8bb7ec79"
        )
        self.assertEqual(normalize_folder(url), "3CX Recordings/recordings/1055")
        self.assertEqual(
            normalize_folder("https://x.sharepoint.com/sites/y/Shared%20Documents/Forms/AllItems.aspx?id=%2Fsites%2Fy%2FShared%20Documents%2FA"),
            "A",
        )

    def test_limit_caps_downloads_and_skips_deletion_detection(self):
        s = self.sync({"root": [file_item("1", "a.pdf"), file_item("2", "b.pdf"), file_item("3", "c.pdf")]})
        for p in s.updated_files:
            s.commit(p)

        # Walk stops after 1 new file, so b.pdf/c.pdf (unseen) must not be reported as deleted
        tree = {"root": [file_item("4", "d.pdf"), file_item("5", "e.pdf")]}
        capped = self.sync(tree, limit=1)
        self.assertEqual([p.name for p in capped.updated_files], ["d.pdf"])
        self.assertEqual(capped.removed, [])


    def test_db_key_is_independent_of_data_dir(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INDEXING_DATA_DIR", None)
            self.assertEqual(db_key(Path("downloads/HR/a.pdf")), "downloads/HR/a.pdf")
        with mock.patch.dict(os.environ, {"INDEXING_DATA_DIR": "/data"}):
            self.assertEqual(db_key(Path("/data/downloads/HR/a.pdf")), "downloads/HR/a.pdf")
            self.assertEqual(db_key(Path("/elsewhere/a.pdf")), "/elsewhere/a.pdf")


if __name__ == "__main__":
    unittest.main()
