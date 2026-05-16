from __future__ import annotations

import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from ._helpers import load_enclave_keyops


ek = load_enclave_keyops()
safe_extract_tar = ek.safe_extract_tar


def make_archive(builder) -> Path:
    """Build a .tgz in a temp dir and return its path. `builder` mutates a
    `tarfile.TarFile` opened in `w:gz` mode.
    """
    fd, path = tempfile.mkstemp(suffix=".tgz")
    os.close(fd)
    with tarfile.open(path, "w:gz") as tf:
        builder(tf)
    return Path(path)


def add_file(tf: tarfile.TarFile, name: str, content: bytes = b"hello\n") -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    tf.addfile(info, io.BytesIO(content))


class SafeExtractTarTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dest_ctx = tempfile.TemporaryDirectory()
        self.dest = Path(self._dest_ctx.name) / "out"

    def tearDown(self) -> None:
        self._dest_ctx.cleanup()

    def test_extract_normal_member(self) -> None:
        archive = make_archive(lambda tf: add_file(tf, "good/inside.txt", b"ok\n"))
        try:
            safe_extract_tar(archive, self.dest)
            self.assertTrue((self.dest / "good" / "inside.txt").is_file())
        finally:
            archive.unlink(missing_ok=True)

    def test_rejects_path_traversal(self) -> None:
        archive = make_archive(lambda tf: add_file(tf, "../escape.txt"))
        try:
            with self.assertRaises(SystemExit) as cm:
                safe_extract_tar(archive, self.dest)
            self.assertEqual(cm.exception.code, 2)
        finally:
            archive.unlink(missing_ok=True)

    def test_absolute_path_member_lands_under_dest(self) -> None:
        # PEP 706 data filter normalizes absolute member paths by stripping
        # the leading slash so they land *under* `dest` instead of escaping
        # to `/etc/...`. Verify the file ends up inside the dest tree, never
        # at the absolute location named in the archive.
        archive = make_archive(lambda tf: add_file(tf, "/etc/escape.txt"))
        try:
            safe_extract_tar(archive, self.dest)
            self.assertTrue((self.dest / "etc" / "escape.txt").is_file())
            self.assertFalse(Path("/etc/escape.txt").exists() and (self.dest / "etc" / "escape.txt").samefile("/etc/escape.txt"))
        finally:
            archive.unlink(missing_ok=True)

    def test_rejects_symlink_member(self) -> None:
        def builder(tf: tarfile.TarFile) -> None:
            info = tarfile.TarInfo(name="link.txt")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tf.addfile(info)

        archive = make_archive(builder)
        try:
            with self.assertRaises(SystemExit) as cm:
                safe_extract_tar(archive, self.dest)
            self.assertEqual(cm.exception.code, 2)
        finally:
            archive.unlink(missing_ok=True)

    def test_rejects_hardlink_escaping_dest(self) -> None:
        # In-archive hardlinks are allowed by PEP 706's data filter; only
        # hardlinks pointing *outside* the destination are rejected. This
        # mirrors the symlink-outside-dest case for hardlinks.
        def builder(tf: tarfile.TarFile) -> None:
            info = tarfile.TarInfo(name="link.txt")
            info.type = tarfile.LNKTYPE
            info.linkname = "/etc/passwd"
            tf.addfile(info)

        archive = make_archive(builder)
        try:
            with self.assertRaises(SystemExit) as cm:
                safe_extract_tar(archive, self.dest)
            self.assertEqual(cm.exception.code, 2)
        finally:
            archive.unlink(missing_ok=True)

    def test_rejects_device_member(self) -> None:
        def builder(tf: tarfile.TarFile) -> None:
            info = tarfile.TarInfo(name="chr.dev")
            info.type = tarfile.CHRTYPE
            info.devmajor = 1
            info.devminor = 3
            tf.addfile(info)

        archive = make_archive(builder)
        try:
            with self.assertRaises(SystemExit) as cm:
                safe_extract_tar(archive, self.dest)
            self.assertEqual(cm.exception.code, 2)
        finally:
            archive.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
