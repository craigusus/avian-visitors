from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "avian/scripts"))
from PIL import Image  # noqa: E402

# These tests never take a lock. Off Linux, stub fcntl for this import only,
# so the stub never leaks into other test modules.
_STUB = {} if sys.platform != "win32" else {"fcntl": types.ModuleType("fcntl")}
with mock.patch.dict(sys.modules, _STUB):
    import build_masks  # noqa: E402
    import import_bundle  # noqa: E402


def png(color=(10, 20, 30, 0), mode="RGBA") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (6, 4), color if mode == "RGBA" else color[:3]).save(out, format="PNG")
    return out.getvalue()


class ImportBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="avian-import-test-")
        root = Path(self.temporary.name)
        self.live = root / "live"
        self.live.mkdir()
        self.source = root / "source"
        (self.source / "illustrations").mkdir(parents=True)
        self.scripts = []
        patches = [
            mock.patch.object(import_bundle, "ILLUSTRATIONS", self.live),
            # A path other than the included set: no station lock is needed.
            mock.patch.object(build_masks, "INCLUDED_ILLUSTRATIONS", root / "not-included"),
            mock.patch.object(import_bundle, "run_script", lambda *a: self.scripts.append(a)),
            mock.patch.object(import_bundle, "ACTIVE_BUNDLE_STATE", root / "absent.json"),
            mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(root / "state")}),
        ]
        if os.name == "nt":  # developer convenience only; CI and stations are Linux
            patches += [
                mock.patch.object(os, "fchmod", lambda *a: None, create=True),
                mock.patch.object(build_masks, "fsync_directory", lambda path: None),
            ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.temporary.cleanup)

    def run_main(self, *args: str) -> int:
        with mock.patch.object(sys, "argv", ["import_bundle.py", *args]):
            return import_bundle.main()

    def write_source(self, name: str, data: bytes, folder: str = "illustrations") -> None:
        path = self.source / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_merges_cut_out_birds_and_leaves_existing_art(self) -> None:
        self.write_source("erithacus-rubecula.png", png())
        self.write_source("erithacus-rubecula-2.png", png())
        self.write_source("parus-major.png", png(mode="RGB"))  # not cut out
        self.write_source("turdus-merula.png", png())
        self.write_source("notes.png", png())  # not a <genus>-<species> name
        (self.live / "turdus-merula.png").write_bytes(b"existing")

        self.assertEqual(self.run_main(str(self.source), "--name", "gb"), 0)
        self.assertTrue((self.live / "erithacus-rubecula.png").exists())
        self.assertTrue((self.live / "erithacus-rubecula-2.png").exists())
        self.assertFalse((self.live / "parus-major.png").exists())
        self.assertFalse((self.live / "notes.png").exists())
        self.assertEqual((self.live / "turdus-merula.png").read_bytes(), b"existing")
        self.assertEqual(len(self.scripts), 1)
        self.assertEqual(self.scripts[0][:2], ("build_masks.py", "--add"))
        self.assertEqual(sorted(self.scripts[0][2:]), ["erithacus-rubecula", "erithacus-rubecula-2"])

        # Re-running is a no-op.
        self.scripts.clear()
        self.assertEqual(self.run_main(str(self.source), "--name", "gb"), 0)
        self.assertEqual(self.scripts, [])

    def test_replace_keeps_a_backup_that_remove_restores(self) -> None:
        original = png((1, 2, 3, 0))
        (self.live / "turdus-merula.png").write_bytes(original)
        self.write_source("turdus-merula.png", png((200, 0, 0, 0)))
        self.write_source("sitta-europaea.png", png())

        self.assertEqual(self.run_main(str(self.source), "--name", "gb", "--replace"), 0)
        self.assertNotEqual((self.live / "turdus-merula.png").read_bytes(), original)

        self.assertEqual(self.run_main("--remove", "gb"), 0)
        self.assertFalse((self.live / "sitta-europaea.png").exists())
        with Image.open(self.live / "turdus-merula.png") as restored:
            self.assertEqual(restored.getpixel((0, 0)), (1, 2, 3, 0))
        self.assertEqual(self.scripts[-1], ("build_masks.py",))
        self.assertEqual(self.run_main("--remove", "gb"), 1)

    def test_zip_prefers_illustrations_over_photo_cutouts_and_ignores_traversal(self) -> None:
        archive = self.source / "fork.zip"
        with zipfile.ZipFile(archive, "w") as out:
            out.writestr("fork-main/avian/assets/illustrations/sitta-europaea.png", png())
            for slug in ("a-b", "c-d", "e-f"):
                out.writestr(f"fork-main/avian/assets/cutouts/{slug}.png", png())
            out.writestr("../evil-bird.png", png())
        self.assertEqual(self.run_main(str(archive)), 0)
        self.assertEqual(sorted(p.name for p in self.live.iterdir()), ["sitta-europaea.png"])
        manifest = json.loads((Path(os.environ["XDG_STATE_HOME"]) / "avian-visitors/imports/fork.json").read_text())
        self.assertEqual(list(manifest["files"]), ["sitta-europaea"])

    def test_invalid_png_is_skipped(self) -> None:
        self.write_source("sitta-europaea.png", b"\x89PNG\r\n\x1a\nbroken")
        self.assertEqual(self.run_main(str(self.source)), 0)
        self.assertEqual(list(self.live.iterdir()), [])

    def test_names_and_github_tree_urls(self) -> None:
        self.assertEqual(import_bundle.default_name("https://github.com/o/AvianAssets_GB-ENG"), "avianassets-gb-eng")
        self.assertEqual(import_bundle.default_name("https://github.com/o/AvianVisitors/tree/europe-illustrations"), "avianvisitors")
        self.assertEqual(import_bundle.default_name("https://x.org/dl/Birds.zip"), "birds")
        match = import_bundle.GITHUB_TREE.match("https://github.com/o/r/tree/main/avian/assets")
        self.assertEqual(match.groups(), ("https://github.com/o/r", "main", "avian/assets"))


if __name__ == "__main__":
    unittest.main()
