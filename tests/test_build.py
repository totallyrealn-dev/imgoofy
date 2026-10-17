import bz2
import gzip
import hashlib
import io
import json
import lzma
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_repo import build


def archive_member(name, content):
    header = f'{name + "/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(content):<10}`\n'.encode("ascii")
    assert len(header) == 60
    return header + content + (b"\n" if len(content) % 2 else b"")


def make_package(path, compression="gz", extra="", architecture="iphoneos-arm"):
    control = (f"Package: com.imgoofy.test\nName: Test only\nVersion: 1.0-1\nArchitecture: {architecture}\n"
               "Maintainer: Test\nDescription: A temporary fixture\n Second line\n .\n Fourth line\n"
               "Depends: firmware (>= 12.0)\nDepiction: https://example.org/test\n" + extra).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("./control")
        member.size = len(control)
        archive.addfile(member, io.BytesIO(control))
    compress = {"gz": gzip.compress, "bz2": bz2.compress, "xz": lzma.compress, "": lambda b: b}[compression]
    suffix = "." + compression if compression else ""
    package = (b"!<arch>\n" + archive_member("debian-binary", b"2.0\n")
               + archive_member("control.tar" + suffix, compress(output.getvalue()))
               + archive_member("data.tar.gz", gzip.compress(b"\x00" * 10240)))
    path.write_bytes(package)
    return package


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "packages").mkdir()
        (self.root / "repo.json").write_text(json.dumps({
            "name": "imgoofy:)", "description": "Test", "codename": "imgoofy",
            "url": "", "architectures": ["iphoneos-arm", "iphoneos-arm64", "all"]
        }), encoding="utf-8")

    def test_empty_indices_and_release_hashes(self):
        self.assertEqual(build(self.root), 0)
        public = self.root / "public"
        self.assertEqual((public / "Packages").read_bytes(), b"")
        for suffix, decompress in (("gz", gzip.decompress), ("bz2", bz2.decompress), ("xz", lzma.decompress)):
            self.assertEqual(decompress((public / f"Packages.{suffix}").read_bytes()), b"")
        algorithm = None
        for line in (public / "Release").read_text().splitlines():
            if line in ("MD5Sum:", "SHA1:", "SHA256:", "SHA512:"):
                algorithm = {"MD5Sum:": "md5", "SHA1:": "sha1", "SHA256:": "sha256", "SHA512:": "sha512"}[line]
            elif line.startswith(" "):
                digest, size, name = line.split()
                data = (public / name).read_bytes()
                self.assertEqual(int(size), len(data))
                self.assertEqual(digest, hashlib.new(algorithm, data).hexdigest())
        self.assertIn("not published yet", (public / "index.html").read_text())

    def test_compressions_metadata_and_download_checksums(self):
        for compression in ("gz", "bz2", "xz", ""):
            with self.subTest(compression=compression):
                package = make_package(self.root / "packages" / "test.deb", compression,
                                       "Filename: fake.deb\nSHA256: fake\n", "iphoneos-arm64")
                self.assertEqual(build(self.root), 1)
                text = (self.root / "public" / "Packages").read_text()
                self.assertIn(" Second line\n .\n Fourth line\n", text)
                self.assertIn("Depends: firmware (>= 12.0)\n", text)
                self.assertIn("Depiction: https://example.org/test\n", text)
                digest = hashlib.sha256(package).hexdigest()
                self.assertIn(f"SHA256: {digest}\n", text)
                self.assertEqual(text.count("SHA256:"), 1)
                self.assertIn(f"Size: {len(package)}\n", text)
                self.assertEqual((self.root / "public" / "debs" / f"{digest}.deb").read_bytes(), package)

    def test_bad_package_does_not_replace_good_index(self):
        build(self.root)
        before = (self.root / "public" / "Release").read_bytes()
        (self.root / "packages" / "bad.deb").write_bytes(b"not a deb")
        with self.assertRaises(ValueError):
            build(self.root)
        self.assertEqual((self.root / "public" / "Release").read_bytes(), before)

    def test_rejects_duplicate_package_and_duplicate_control_fields(self):
        make_package(self.root / "packages" / "a.deb")
        make_package(self.root / "packages" / "b.deb")
        with self.assertRaisesRegex(ValueError, "Duplicate package"):
            build(self.root)
        (self.root / "packages" / "b.deb").unlink()
        make_package(self.root / "packages" / "a.deb", extra="package: other\n")
        with self.assertRaisesRegex(ValueError, "duplicate or invalid control field"):
            build(self.root)

    def test_removed_package_disappears_from_index(self):
        make_package(self.root / "packages" / "a.deb")
        build(self.root)
        (self.root / "packages" / "a.deb").unlink()
        self.assertEqual(build(self.root), 0)
        self.assertEqual((self.root / "public" / "Packages").read_bytes(), b"")

    def test_does_not_leave_stale_signature(self):
        build(self.root)
        (self.root / "public" / "InRelease").write_text("test signature")
        with self.assertRaisesRegex(ValueError, "signed output"):
            build(self.root)


if __name__ == "__main__":
    unittest.main()
