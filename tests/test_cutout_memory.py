from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "avian/scripts"))
try:
    import fcntl  # noqa: F401
except ImportError:  # these tests never take a lock; let them run off-Linux
    sys.modules["fcntl"] = types.ModuleType("fcntl")

from PIL import Image  # noqa: E402

import cutout  # noqa: E402


class CutoutMemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="avian-cutout-")
        self.dir = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_main(self, *args: str) -> int:
        with mock.patch.object(sys, "argv", ["cutout.py", "--dir", str(self.dir), *args]):
            return cutout.main()

    def test_reads_mem_available(self) -> None:
        meminfo = self.dir / "meminfo"
        meminfo.write_text("MemTotal: 1000000 kB\nMemAvailable:  512000 kB\n", encoding="ascii")
        self.assertEqual(cutout.available_memory_mib(meminfo), 500)
        self.assertIsNone(cutout.available_memory_mib(self.dir / "absent"))

    def test_memory_problem_names_the_way_out(self) -> None:
        problem = cutout.memory_problem("birefnet-general", 800)
        self.assertIn("800 MiB", problem)
        self.assertIn("desktop", problem)
        self.assertIn("--model u2net", problem)
        self.assertIsNone(cutout.memory_problem("birefnet-general", 8000))
        self.assertIsNone(cutout.memory_problem("u2net", None))

    def test_transparent_set_never_loads_a_model(self) -> None:
        Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(self.dir / "erithacus-rubecula.png")
        with mock.patch.dict(sys.modules, {"rembg": None}), \
                mock.patch.object(cutout, "available_memory_mib", return_value=10):
            self.assertEqual(self.run_main(), 0)

    def test_low_memory_stops_before_loading(self) -> None:
        Image.new("RGB", (8, 8), (240, 230, 210)).save(self.dir / "parus-major.png")
        with mock.patch.dict(sys.modules, {"rembg": None}), \
                mock.patch.object(cutout, "available_memory_mib", return_value=900):
            self.assertEqual(self.run_main(), 3)
            # The override goes on to need rembg, which this test hides.
            self.assertEqual(self.run_main("--skip-memory-check"), 2)


if __name__ == "__main__":
    unittest.main()
