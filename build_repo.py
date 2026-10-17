"""Build a flat Cydia/APT repository. Never installs or executes package content."""

import argparse
import bz2
import gzip
import hashlib
import html
import io
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from email.utils import format_datetime

ROOT = Path(__file__).resolve().parent
CONTROL_LIMIT = 16 * 1024 * 1024
GENERATED_FIELDS = {"filename", "size", "md5sum", "sha1", "sha256", "sha512"}


def control_archive(path):
    """Read only bounded control metadata from a Debian ar archive."""
    control = None
    debian_version = False
    data_found = False
    total = path.stat().st_size
    with path.open("rb") as source:
        if source.read(8) != b"!<arch>\n":
            raise ValueError(f"{path.name}: not a Debian .deb archive")
        while source.tell() < total:
            header = source.read(60)
            if len(header) != 60 or header[58:] != b"`\n":
                raise ValueError(f"{path.name}: damaged archive header")
            name = header[:16].decode("ascii").strip().rstrip("/")
            size = int(header[48:58].decode("ascii").strip())
            if size < 0 or source.tell() + size > total:
                raise ValueError(f"{path.name}: damaged archive size")
            if name == "debian-binary":
                if size != 4 or source.read(size) != b"2.0\n":
                    raise ValueError(f"{path.name}: unsupported Debian format")
                debian_version = True
            elif name == "control.tar" or name.startswith("control.tar."):
                if control is not None or size > CONTROL_LIMIT:
                    raise ValueError(f"{path.name}: duplicate or oversized control archive")
                if name not in {"control.tar", "control.tar.gz", "control.tar.xz", "control.tar.bz2"}:
                    raise ValueError(f"{path.name}: use gzip, xz, or bzip2 control compression")
                control = source.read(size)
            else:
                data_found |= name == "data.tar" or name.startswith("data.tar.")
                source.seek(size, 1)
            if size % 2:
                if source.read(1) != b"\n":
                    raise ValueError(f"{path.name}: damaged archive padding")
    if not debian_version or control is None or not data_found:
        raise ValueError(f"{path.name}: missing Debian package members")
    return control


def read_fields(path):
    blob = control_archive(path)
    # Bound the decompressed archive before tarfile parses it. No extraction to disk.
    if blob.startswith(b"\x1f\x8b"):
        reader = gzip.GzipFile(fileobj=io.BytesIO(blob))
    elif blob.startswith(b"\xfd7zXZ\x00"):
        reader = lzma.LZMAFile(io.BytesIO(blob))
    elif blob.startswith(b"BZh"):
        reader = bz2.BZ2File(io.BytesIO(blob))
    else:
        reader = io.BytesIO(blob)
    with reader:
        unpacked = reader.read(CONTROL_LIMIT + 1)
    if len(unpacked) > CONTROL_LIMIT:
        raise ValueError(f"{path.name}: decompressed control archive is too large")
    with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as archive:
        members = [m for m in archive if m.name in {"control", "./control"}]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 1024 * 1024:
            raise ValueError(f"{path.name}: missing or invalid DEBIAN/control")
        with archive.extractfile(members[0]) as source:
            raw = source.read().decode("utf-8").replace("\r\n", "\n").strip("\n")
    fields = {}
    names = set()
    key = None
    for line in raw.split("\n"):
        if not line or "\x00" in line or "\r" in line:
            raise ValueError(f"{path.name}: malformed control paragraph")
        if line[0] in " \t":
            if key is None:
                raise ValueError(f"{path.name}: orphan continuation line")
            fields[key] += "\n" + line
            continue
        if ":" not in line:
            raise ValueError(f"{path.name}: invalid control field")
        key, value = line.split(":", 1)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", key) or key.lower() in names:
            raise ValueError(f"{path.name}: duplicate or invalid control field")
        names.add(key.lower())
        fields[key] = value.lstrip(" \t")
    lookup = {key.lower(): value for key, value in fields.items()}
    for key in ("package", "version", "architecture", "maintainer", "description"):
        if not lookup.get(key, "").strip():
            raise ValueError(f"{path.name}: missing {key}")
    for key in ("package", "version", "architecture"):
        if any(c.isspace() for c in lookup[key]):
            raise ValueError(f"{path.name}: invalid {key}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", lookup["package"]):
        raise ValueError(f"{path.name}: invalid package identifier")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", lookup["architecture"]):
        raise ValueError(f"{path.name}: invalid architecture")
    return fields, lookup


def checksums(path):
    hashes = {name: hashlib.new(name) for name in ("md5", "sha1", "sha256", "sha512")}
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            for digest in hashes.values():
                digest.update(chunk)
    return {name: digest.hexdigest() for name, digest in hashes.items()}


def write_atomic(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def build(root=ROOT):
    root = Path(root).resolve()
    config = json.loads((root / "repo.json").read_text(encoding="utf-8"))
    for key in ("name", "codename", "description"):
        if not isinstance(config.get(key), str) or not config[key] or any(c in config[key] for c in "\r\n\x00"):
            raise ValueError(f"repo.json: {key} must be a nonempty single line")
    url = config.get("url", "")
    if not isinstance(url, str) or any(c in url for c in "\r\n\x00") or (url and not url.startswith("https://")):
        raise ValueError("repo.json: URL must be blank or an HTTPS repository URL")
    architectures = set(config["architectures"])
    if not architectures or any(not re.fullmatch(r"[a-z0-9][a-z0-9-]*", a) for a in architectures):
        raise ValueError("repo.json: invalid architectures")
    public = root / "public"
    if public.is_symlink() or (public / "debs").is_symlink():
        raise ValueError("Output directories must not be symbolic links")
    # Never leave signatures from an earlier Release beside changed metadata.
    if any((public / name).exists() for name in ("InRelease", "Release.gpg")):
        raise ValueError("Existing signed output found. Build in a clean folder and sign the new Release.")
    entries, seen, packages = [], set(), []
    for path in sorted((root / "packages").glob("*.deb")):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{path.name}: packages must be regular files")
        fields, lookup = read_fields(path)
        identity = tuple(lookup[k] for k in ("package", "version", "architecture"))
        if identity in seen:
            raise ValueError(f"Duplicate package/version/architecture: {identity}")
        seen.add(identity)
        architectures.add(lookup["architecture"])
        digests = checksums(path)
        # Content-addressed paths avoid stale caches when a package is replaced.
        filename = "debs/" + digests["sha256"] + ".deb"
        stanza = [f"{k}: {v}" for k, v in fields.items() if k.lower() not in GENERATED_FIELDS]
        stanza += [f"Filename: {filename}", f"Size: {path.stat().st_size}"]
        for field, algorithm in (("MD5sum", "md5"), ("SHA1", "sha1"), ("SHA256", "sha256"), ("SHA512", "sha512")):
            stanza.append(f"{field}: {digests[algorithm]}")
        entries.append("\n".join(stanza) + "\n\n")
        packages.append((path, filename, digests["sha256"]))
    # Validate every input before replacing any repository index.
    # "packages/" would collide with "Packages" on Windows and macOS.
    (public / "debs").mkdir(parents=True, exist_ok=True)
    for source, filename, expected in packages:
        target = public / filename
        temporary = target.with_suffix(".tmp")
        shutil.copyfile(source, temporary)
        if checksums(temporary)["sha256"] != expected:
            raise ValueError(f"{source.name} changed during the build; try again")
        temporary.replace(target)
    package_index = "".join(entries).encode("utf-8")
    indices = {
        "Packages": package_index,
        "Packages.gz": gzip.compress(package_index, mtime=0),
        "Packages.bz2": bz2.compress(package_index),
        "Packages.xz": lzma.compress(package_index),
    }
    stamp = datetime.fromtimestamp(int(os.environ["SOURCE_DATE_EPOCH"]), timezone.utc) if "SOURCE_DATE_EPOCH" in os.environ else datetime.now(timezone.utc)
    release = [
        f"Origin: {config['name']}", f"Label: {config['name']}",
        "Suite: stable", "Version: 1.0", f"Codename: {config['codename']}",
        "Architectures: " + " ".join(sorted(architectures)), "Components: main",
        f"Description: {config['description']}", f"Date: {format_datetime(stamp, usegmt=True)}",
    ]
    for field, algorithm in (("MD5Sum", "md5"), ("SHA1", "sha1"), ("SHA256", "sha256"), ("SHA512", "sha512")):
        release.append(field + ":")
        for name, data in indices.items():
            release.append(f" {hashlib.new(algorithm, data).hexdigest()} {len(data)} {name}")
    for name, data in indices.items():
        write_atomic(public / name, data)
    write_atomic(public / "Release", ("\n".join(release) + "\n").encode("utf-8"))
    name, url = html.escape(config["name"]), html.escape(url)
    source_instructions = (f'<p>In Cydia, open Sources, choose Edit, then Add. Paste:</p><p><code>{url}</code></p>'
                           if url else '<p>The source address is not published yet.</p>')
    page = f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}</title>
<style>body{{font:18px system-ui,sans-serif;max-width:680px;margin:12vh auto;padding:24px;background:#161323;color:#f5f1ff}}h1{{font-size:52px}}a{{color:#b8a1ff}}code{{overflow-wrap:anywhere}}p{{line-height:1.6}}</style>
<h1>{name}</h1><p>A home for tweaks and themes.</p>
<p>{len(entries)} packages available. {'The repo is ready for its first package.' if not entries else ''}</p>
<h2>Add this source</h2>{source_instructions}
<p>Packages require a compatible jailbroken device.</p>
<p><a href="Packages">Package list</a> &middot; <a href="Release">Repository information</a></p>
</html>
'''
    write_atomic(public / "index.html", page.encode("utf-8"))
    write_atomic(public / ".nojekyll", b"")
    return len(entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        count = build(args.root)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, EOFError, lzma.LZMAError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"Built {count} package(s) in {args.root.resolve() / 'public'}")
    print("Local build complete. Publishing is a separate step; this build is unsigned.")
