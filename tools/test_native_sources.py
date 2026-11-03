#!/usr/bin/env python3
"""Pinned source downloads fall back across sites and keep only pinned bytes.

No network: urlopen is replaced by a table of answers per URL.
"""
import contextlib
import hashlib
import http.client
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import urllib.error
from urllib.parse import urlsplit

import check_native_platform_env as native

NAME = "pkg-1.0.tar.bz2"
PRIMARY = "https://primary.invalid/pkg-1.0.tar.bz2"
MIRROR = "https://mirror.invalid/pkg/pkg-1.0.tar.bz2"


def tarball():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        data = b"all:\n"
        info = tarfile.TarInfo("pkg-1.0/Makefile")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


ARCHIVE = tarball()


class CutShort(io.BytesIO):
    def read(self, *args):
        raise http.client.IncompleteRead(b"BZh")


class SourceDownloads(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        pin = {NAME: ((PRIMARY, MIRROR), hashlib.sha256(ARCHIVE).hexdigest())}
        patcher = mock.patch.dict(native.SOURCES, pin)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, answers, cache=None):
        """Run source() with each URL answered by bytes, a stream or an exception."""
        cache = cache or Path(tempfile.mkdtemp(dir=self.root))
        work = Path(tempfile.mkdtemp(dir=self.root))
        requested = []

        def urlopen(url, timeout):
            requested.append(url)
            answer = answers[url]
            if isinstance(answer, BaseException):
                raise answer
            return answer if isinstance(answer, io.IOBase) else io.BytesIO(answer)

        log = io.StringIO()
        with mock.patch.object(native.urllib.request, "urlopen", urlopen), contextlib.redirect_stderr(log):
            directory = native.source(cache, work, NAME)
        return cache, directory, requested, log.getvalue()

    def test_falls_back_to_the_next_site(self):
        failures = {
            "unreachable": urllib.error.URLError(OSError(101, "Network is unreachable")),
            "timeout": TimeoutError("timed out"),
            "not found": urllib.error.HTTPError(PRIMARY, 404, "Not Found", {}, None),
            "cut short": CutShort(),
            "wrong bytes": b"not the pinned archive",
        }
        for label, failure in failures.items():
            with self.subTest(label):
                cache, directory, requested, log = self.fetch({PRIMARY: failure, MIRROR: ARCHIVE})
                self.assertEqual(requested, [PRIMARY, MIRROR])
                self.assertEqual((directory / "Makefile").read_bytes(), b"all:\n")
                self.assertEqual([path.name for path in cache.iterdir()], [NAME])
                self.assertIn(PRIMARY, log)

    def test_fails_when_no_site_has_the_pinned_bytes(self):
        cache = Path(tempfile.mkdtemp(dir=self.root))
        with self.assertRaises(RuntimeError) as raised:
            self.fetch({PRIMARY: TimeoutError("timed out"), MIRROR: b"tampered"}, cache)
        self.assertIn(PRIMARY, str(raised.exception))
        self.assertIn(MIRROR, str(raised.exception))
        self.assertEqual(list(cache.iterdir()), [])

    def test_uses_a_verified_cached_copy_offline(self):
        cache = Path(tempfile.mkdtemp(dir=self.root))
        (cache / NAME).write_bytes(ARCHIVE)
        _, directory, requested, _ = self.fetch({}, cache)
        self.assertEqual(requested, [])
        self.assertTrue((directory / "Makefile").is_file())


class PinnedSources(unittest.TestCase):
    def test_every_source_has_a_second_site(self):
        for name, (urls, _) in native.SOURCES.items():
            with self.subTest(name):
                hosts = {urlsplit(url).hostname for url in urls}
                self.assertGreaterEqual(len(hosts), 2)
                self.assertTrue(all(url.startswith("https://") for url in urls))


if __name__ == "__main__":
    unittest.main()
