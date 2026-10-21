#!/usr/bin/env python3
"""AvianVisitors - merge a community illustration set into this station.

Regional sets that are not yet in the reviewed catalog at
avianvisitors.com/bundles live in contributors' GitHub repos as folders of
cut-out RGBA PNGs named <genus>-<species>.png (perched) and
<genus>-<species>-2.png (flight). This fetches one, validates every PNG,
merges it into avian/assets/illustrations/ through the same live-art
transaction as cutout.py, and rebuilds the collage masks. The station's
content revision rotates, so browsers pick up the new art without any
apt.js edit.

Nothing is committed: third-party art stays out of the repo, and
update_birdnet.sh preserves it as custom bird art. Imported sets are
unreviewed; only fetch sources you trust.

    import_bundle.py https://github.com/<owner>/<repo>            # git
    import_bundle.py https://github.com/<owner>/<repo>/tree/<branch>
    import_bundle.py https://example.org/birds.zip                 # zip
    import_bundle.py ~/Downloads/birds.zip   or   ~/some/folder    # local
    import_bundle.py --list
    import_bundle.py --remove <name>

By default, birds that already have art are left alone (--replace swaps
them, keeping a backup for --remove). PNGs without transparency are skipped
unless --cutout, which runs cutout.py on them afterwards (needs plenty of
RAM). Run the same command again to pick up a bundle's updates.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import build_masks

HERE = Path(__file__).resolve().parent
ILLUSTRATIONS = HERE.parent / "assets" / "illustrations"
SLUG_PNG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+\.png")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
GITHUB_TREE = re.compile(r"^(https://github\.com/[^/]+/[^/]+?)(?:\.git)?/tree/([^/]+)(?:/(.+))?/?$")
MAX_PNG_BYTES = 20 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024 * 1024
ACTIVE_BUNDLE_STATE = Path("/var/lib/avian-visitors/bundles/active.json")


class BundleImportError(RuntimeError):
    """A problem worth one clear line to the user."""


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "avian-visitors" / "imports"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_name(source: str) -> str:
    match = GITHUB_TREE.match(source)
    base = match.group(1) if match else source.rstrip("/")
    stem = re.sub(r"\.(git|zip)$", "", base.replace("\\", "/").rsplit("/", 1)[-1], flags=re.I)
    name = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:64]
    return name or "bundle"


# --- fetching ---------------------------------------------------------------

def download(url: str, destination: Path) -> None:
    if urllib.parse.urlsplit(url).scheme not in ("https", "http"):
        raise BundleImportError(f"unsupported URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "AvianVisitors-import"})
    received = 0
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as out:
        while True:
            block = response.read(1 << 20)
            if not block:
                return
            received += len(block)
            if received > MAX_DOWNLOAD_BYTES:
                raise BundleImportError("download is larger than 4 GiB")
            out.write(block)


def git_clone(source: str, destination: Path, branch: str | None) -> Path:
    match = GITHUB_TREE.match(source)
    subdir = None
    if match:
        source, branch, subdir = match.group(1), branch or match.group(2), match.group(3)
    command = ["git", "-c", "core.symlinks=false", "clone", "--quiet", "--depth", "1"]
    if branch:
        command += ["--branch", branch]
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    print(f"cloning {source}" + (f" ({branch})" if branch else "") + " ...")
    result = subprocess.run(command + ["--", source, str(destination)], env=environment)
    if result.returncode:
        raise BundleImportError(f"git clone failed for {source}")
    return destination / subdir if subdir else destination


def directory_entries(root: Path):
    """Yield (relative path, size, reader) for regular files under root."""
    for path in root.rglob("*.png"):
        if path.is_symlink() or not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        yield path.relative_to(root).as_posix(), path.stat().st_size, path.read_bytes


def zip_entries(archive: zipfile.ZipFile):
    for info in archive.infolist():
        mode = info.external_attr >> 16
        if info.is_dir() or (mode & 0o170000) == 0o120000:
            continue
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            continue
        yield name, info.file_size, (lambda info=info: archive.read(info))


def pick_folder(entries, subdir: str | None):
    """Keep the PNGs of the one folder that holds the illustration set.

    Full forks also carry photo cutouts with the same names, so a folder
    named "illustrations" wins; otherwise the folder with the most birds.
    """
    folders: dict[str, list] = {}
    for name, size, read in entries:
        folder, _, base = name.rpartition("/")
        if SLUG_PNG.fullmatch(base):
            folders.setdefault(folder, []).append((base, size, read))
    if subdir is not None:
        folders = {k: v for k, v in folders.items() if k.strip("/") == subdir.strip("/")}
    if not folders:
        raise BundleImportError("no <genus>-<species>.png illustrations found in the source")
    best = max(folders, key=lambda k: (k.rsplit("/", 1)[-1] == "illustrations", len(folders[k])))
    print(f"using {len(folders[best])} PNGs from {best or '(top level)'}/")
    return folders[best]


# --- validation and merge ---------------------------------------------------

def inspect_png(data: bytes) -> bool:
    """Validate the PNG; return True when it is already cut out."""
    from image_files import open_source_image
    with open_source_image(data) as im:
        im.load()
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        return im.getchannel("A").getextrema()[0] == 0


def write_png(data: bytes, destination: Path) -> str:
    from image_files import open_source_image, save_png_atomic
    identity = build_masks.regular_file_identity(destination)
    with open_source_image(data) as im:
        im.load()
        with build_masks.included_art_write_transaction(destination.parent, {destination: identity}):
            save_png_atomic(im, destination)
            build_masks.fsync_directory(destination.parent)
    return sha256(destination.read_bytes())


def remove_png(destination: Path, backup: Path | None) -> None:
    identity = build_masks.regular_file_identity(destination)
    with build_masks.included_art_write_transaction(destination.parent, {destination: identity}):
        if backup is not None and backup.is_file():
            from image_files import open_source_image, save_png_atomic
            with open_source_image(backup.read_bytes()) as im:
                im.load()
                save_png_atomic(im, destination)
        else:
            destination.unlink()
        build_masks.fsync_directory(destination.parent)


def load_manifest(name: str) -> dict:
    path = state_dir() / f"{name}.json"
    if not path.exists():
        return {"name": name, "files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(manifest: dict) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{manifest['name']}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def merge_one(base: str, data: bytes, manifest: dict, options, stats: dict) -> None:
    slug = base[:-4]
    destination = ILLUSTRATIONS / base
    record = manifest["files"].get(slug)
    source_hash = sha256(data)
    if record and record["source"] == source_hash and destination.exists():
        stats["unchanged"] += 1
        return
    if destination.exists() and not record and not options.replace:
        stats["kept"] += 1
        return
    try:
        transparent = inspect_png(data)
    except Exception as error:  # Pillow raises many types for bad input
        print(f"  [skip] {base}: invalid PNG ({error})")
        stats["invalid"] += 1
        return
    if not transparent and not options.cutout:
        stats["opaque"].append(slug)
        return
    backup = None
    if destination.exists() and not record:
        backup = state_dir() / f"{manifest['name']}.backup" / base
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination, backup)
    written = write_png(data, destination)
    manifest["files"][slug] = {
        "source": source_hash, "written": written,
        "backup": str(backup) if backup else (record or {}).get("backup"),
    }
    stats["written"].append(slug)
    if not transparent:
        stats["to_cut"].append(slug)


def run_script(script: str, *args: str) -> None:
    result = subprocess.run([sys.executable, str(HERE / script), *args])
    if result.returncode:
        raise BundleImportError(f"{script} failed (exit {result.returncode})")


def warn_if_downloaded_bundle_active() -> None:
    try:
        active = json.loads(ACTIVE_BUNDLE_STATE.read_text(encoding="utf-8"))["active"]
    except (OSError, ValueError, KeyError, TypeError):
        return
    if isinstance(active, dict) and active.get("included") is False:
        print(f"\nnote: the downloaded bundle '{active.get('name', active.get('id'))}' is active, so the\n"
              "collage shows its art, not this station's own illustrations. Switch back\n"
              "in Settings -> Bird bundle to see the imported birds.")


def import_source(options) -> int:
    name = options.name or default_name(options.source)
    if not NAME_RE.fullmatch(name):
        raise BundleImportError(f"invalid --name: {name} (use a-z, 0-9 and -)")
    manifest = load_manifest(name)
    manifest.update(source=options.source)
    stats = {"unchanged": 0, "kept": 0, "invalid": 0, "opaque": [], "written": [], "to_cut": []}
    try:
        with tempfile.TemporaryDirectory(prefix="avian-import-") as scratch:
            fetch_and_merge(options, Path(scratch), manifest, stats)
    finally:
        # Record whatever landed, even after a failure, so --remove can undo it.
        if manifest["files"]:
            manifest["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            save_manifest(manifest)
    report(name, stats)
    if stats["to_cut"]:
        run_script("cutout.py", *stats["to_cut"])
    if stats["written"]:
        run_script("build_masks.py", "--add", *stats["written"])
    warn_if_downloaded_bundle_active()
    return 0


def fetch_and_merge(options, work: Path, manifest: dict, stats: dict) -> None:
    local = Path(os.path.expanduser(options.source))
    if local.is_dir():
        merge_files(pick_folder(directory_entries(local), options.subdir), manifest, options, stats)
    elif local.is_file() or options.source.lower().split("?")[0].endswith(".zip"):
        archive_path = local if local.is_file() else work / "bundle.zip"
        if not local.is_file():
            print(f"downloading {options.source} ...")
            download(options.source, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            merge_files(pick_folder(zip_entries(archive), options.subdir), manifest, options, stats)
    else:
        root = git_clone(options.source, work / "repo", options.branch)
        merge_files(pick_folder(directory_entries(root), options.subdir), manifest, options, stats)


def merge_files(files, manifest: dict, options, stats: dict) -> None:
    for base, size, read in sorted(files, key=lambda item: item[0]):
        if size > MAX_PNG_BYTES:
            print(f"  [skip] {base}: larger than {MAX_PNG_BYTES >> 20} MiB")
            stats["invalid"] += 1
            continue
        merge_one(base, read(), manifest, options, stats)


def report(name: str, stats: dict) -> None:
    print(f"\n{name}: added or updated {len(stats['written'])}, unchanged {stats['unchanged']}, "
          f"kept existing {stats['kept']}, invalid {stats['invalid']}")
    if stats["kept"]:
        print("  birds that already had art were kept; --replace swaps them in")
    if stats["opaque"]:
        print(f"  skipped {len(stats['opaque'])} PNGs without transparency (e.g. {stats['opaque'][0]}); "
              "--cutout cuts them, but needs a lot of RAM")


def remove_import(name: str) -> int:
    if not NAME_RE.fullmatch(name) or not (state_dir() / f"{name}.json").exists():
        raise BundleImportError(f"no import named {name} (see --list)")
    manifest = load_manifest(name)
    removed = changed = 0
    for slug, record in sorted(manifest["files"].items()):
        destination = ILLUSTRATIONS / f"{slug}.png"
        if not destination.exists():
            continue
        if sha256(destination.read_bytes()) != record["written"]:
            changed += 1
            continue
        backup = Path(record["backup"]) if record.get("backup") else None
        remove_png(destination, backup)
        removed += 1
    run_script("build_masks.py")
    shutil.rmtree(state_dir() / f"{name}.backup", ignore_errors=True)
    (state_dir() / f"{name}.json").unlink()
    print(f"removed {removed} illustrations from {name}"
          + (f"; left {changed} that changed since import" if changed else ""))
    return 0


def list_imports() -> int:
    manifests = sorted(state_dir().glob("*.json")) if state_dir().is_dir() else []
    if not manifests:
        print("no imported illustration sets")
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        print(f"{manifest['name']}: {len(manifest['files'])} PNGs from {manifest.get('source')} "
              f"(updated {manifest.get('updated', '?')})")
    return 0


def main() -> int:
    # Piped through the installer's tee, block buffering would print our lines
    # after git's and build_masks.py's own output.
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="git URL, GitHub tree URL, .zip URL, or local zip/folder")
    ap.add_argument("--name", help="name for this import (default: from the URL)")
    ap.add_argument("--branch", help="git branch or tag to fetch")
    ap.add_argument("--subdir", help="folder inside the source that holds the PNGs")
    ap.add_argument("--replace", action="store_true", help="replace birds that already have art")
    ap.add_argument("--cutout", action="store_true", help="import PNGs without transparency and cut them out")
    ap.add_argument("--list", action="store_true", help="list imported sets")
    ap.add_argument("--remove", metavar="NAME", help="remove an imported set")
    options = ap.parse_args()
    try:
        if options.list:
            return list_imports()
        if options.remove:
            return remove_import(options.remove)
        if not options.source:
            ap.error("give a source URL or path, --list, or --remove NAME")
        return import_source(options)
    except (BundleImportError, RuntimeError, OSError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
