#!/usr/bin/env python3
"""AvianVisitors - cut the cream ground off the generated illustrations.

Step 2 of the illustration pipeline (after pregen.py, before build_masks.py).

pregen.py renders each bird on a flat cream ground because the image model
can't cut a clean transparent background on its own (it leaves holes and
fringes). A flat known ground, by contrast, removes cleanly. This runs each
illustration through the BiRefNet matting model (via rembg), then crops to
the bird's bounding box with a small even margin, and saves an RGBA cutout
back in place.

Idempotent: an illustration that already has a transparent background is
skipped unless you pass --force.

Requires rembg + onnxruntime (see requirements.txt). The first run downloads
the BiRefNet model (~1 GB) to ~/.u2net/. Before loading a model it checks free
memory and stops with advice on a Pi that would be OOM-killed; nothing is
loaded at all when every illustration is already transparent.

Usage:
    python3 cutout.py                      # process every illustration
    python3 cutout.py calypte-anna         # one slug (both poses)
    python3 cutout.py calypte-anna-2 --force
    python3 cutout.py --model u2net        # smaller model for low-RAM Pis
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import build_masks


# Approximate peak memory (MiB) while a model matts one illustration. BiRefNet
# is the one that gets OOM-killed on 1-4 GB Pis.
MODEL_MEMORY_MIB = (("birefnet", 3500), ("u2netp", 500), ("silueta", 500))
DEFAULT_MODEL_MEMORY_MIB = 1200


def model_memory_mib(model: str) -> int:
    for prefix, mib in MODEL_MEMORY_MIB:
        if model.startswith(prefix):
            return mib
    return DEFAULT_MODEL_MEMORY_MIB


def available_memory_mib(meminfo: Path = Path("/proc/meminfo")):
    """MemAvailable in MiB, or None where the kernel doesn't report it."""
    try:
        for line in meminfo.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def memory_problem(model: str, available) -> str | None:
    needed = model_memory_mib(model)
    if available is None or available >= needed:
        return None
    return (f"error: the {model} model needs about {needed} MiB of free memory, "
            f"but only {available} MiB is available.\n"
            "Run cutout.py on a desktop or laptop and copy the finished PNGs back,\n"
            "or try a smaller model: --model u2net (lower quality). Pre-cut bundles\n"
            "need no cutout. --skip-memory-check runs anyway.")


def needs_cutout(path: Path, force: bool) -> bool:
    from image_files import open_source_image
    if force:
        return True
    with open_source_image(path.read_bytes()) as im:
        return not (im.mode == "RGBA" and im.getchannel("A").getextrema()[0] == 0)


def selected_paths(directory: Path, slugs) -> list[Path]:
    if not slugs:
        return sorted(directory.glob("*.png"))
    paths = []
    for slug in slugs:
        paths.append(directory / f"{slug}.png")
        flight = directory / f"{slug}-2.png"
        if not slug.endswith("-2") and flight.exists():
            paths.append(flight)
    return paths


def cut_one(path: Path, directory: Path, session, margin: float):
    from rembg import remove
    from image_files import open_source_image, save_png_atomic
    original_identity = build_masks.regular_file_identity(path)
    with open_source_image(path.read_bytes()) as im:
        cut = remove(im.convert("RGB"), session=session)  # RGBA, ground -> transparent
    bbox = cut.getchannel("A").getbbox()
    if bbox:
        pad = round(margin * max(bbox[2] - bbox[0], bbox[3] - bbox[1]))
        x0, y0 = max(0, bbox[0] - pad), max(0, bbox[1] - pad)
        x1, y1 = min(cut.width, bbox[2] + pad), min(cut.height, bbox[3] + pad)
        cut = cut.crop((x0, y0, x1, y1))
    with build_masks.included_art_write_transaction(directory, {path: original_identity}):
        save_png_atomic(cut, path)
        build_masks.fsync_directory(path.parent)
    return cut.size


def main() -> int:
    here = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slugs", nargs="*",
                    help="Slugs to process (e.g. calypte-anna). Default: all.")
    ap.add_argument("--dir", type=Path, default=here / "assets" / "illustrations",
                    help="Illustration directory (default: avian/assets/illustrations/)")
    ap.add_argument("--model", default="birefnet-general",
                    help="rembg model name (default: birefnet-general)")
    ap.add_argument("--margin", type=float, default=0.02,
                    help="Even margin around the bird, as a fraction of its "
                         "long side (default: 0.02)")
    ap.add_argument("--force", action="store_true",
                    help="Re-cut illustrations that already have transparency")
    ap.add_argument("--skip-memory-check", action="store_true",
                    help="Load the model even if free memory looks too low")
    args = ap.parse_args()

    try:
        import image_files  # noqa: F401  (Pillow)
    except ImportError:
        print("error: needs Pillow + rembg (avian/scripts/setup_venv.sh --cutout)",
              file=sys.stderr)
        return 2

    paths = selected_paths(args.dir, args.slugs)
    missing = [p for p in paths if not p.exists()]
    if missing:
        print("error: not found: " + ", ".join(p.name for p in missing), file=sys.stderr)
        return 2
    if not paths:
        print("error: no illustrations found", file=sys.stderr)
        return 1

    # Decide what needs work before loading a model: the session alone can
    # exhaust a small Pi, and pre-cut sets need none.
    todo = [p for p in paths if needs_cutout(p, args.force)]
    skipped = len(paths) - len(todo)
    if not todo:
        print(f"cut 0 · skipped {skipped} (already transparent)")
        return 0
    problem = None if args.skip_memory_check else memory_problem(args.model, available_memory_mib())
    if problem:
        print(problem, file=sys.stderr)
        return 3

    try:
        from rembg import new_session
    except ImportError:
        print("error: needs rembg (avian/scripts/setup_venv.sh --cutout)", file=sys.stderr)
        return 2
    session = new_session(args.model)
    done = 0
    for p in todo:
        try:
            width, height = cut_one(p, args.dir, session, args.margin)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        done += 1
        print(f"  [cut]  {p.name}  -> {width}x{height}")

    print(f"\ncut {done} · skipped {skipped} (already transparent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
