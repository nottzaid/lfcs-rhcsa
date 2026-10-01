from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from sysadmin_lab.application.image_acquisition import ImageAcquirer
from sysadmin_lab.domain.images import ImageVerificationError
from tests.unit.test_images import image_manifest


@dataclass
class FakeDownloader:
    payload: bytes
    calls: list[tuple[str, Path]]

    def download(self, url: str, destination: Path) -> None:
        self.calls.append((url, destination))
        destination.write_bytes(self.payload)


def test_acquirer_downloads_verifies_and_atomically_publishes(tmp_path: Path) -> None:
    payload = b"verified cloud image"
    downloader = FakeDownloader(payload, [])
    manifest = image_manifest(payload)
    result = ImageAcquirer(downloader).acquire(manifest, tmp_path / "cache")

    assert result.name == manifest.source.filename
    assert result.read_bytes() == payload
    assert len(downloader.calls) == 1
    assert not list(result.parent.glob("*.partial-*"))


def test_acquirer_reuses_verified_existing_artifact(tmp_path: Path) -> None:
    payload = b"verified cloud image"
    manifest = image_manifest(payload)
    cache = tmp_path / "cache"
    cache.mkdir()
    target = cache / manifest.source.filename
    target.write_bytes(payload)
    downloader = FakeDownloader(b"must not be used", [])

    assert ImageAcquirer(downloader).acquire(manifest, cache) == target
    assert not downloader.calls


def test_acquirer_never_overwrites_invalid_existing_artifact(tmp_path: Path) -> None:
    payload = b"verified cloud image"
    manifest = image_manifest(payload)
    cache = tmp_path / "cache"
    cache.mkdir()
    target = cache / manifest.source.filename
    target.write_bytes(b"invalid")

    with pytest.raises(ImageVerificationError):
        ImageAcquirer(FakeDownloader(payload, [])).acquire(manifest, cache)
    assert target.read_bytes() == b"invalid"


def test_acquirer_removes_failed_partial_download(tmp_path: Path) -> None:
    manifest = image_manifest(b"expected")
    cache = tmp_path / "cache"
    with pytest.raises(ImageVerificationError):
        ImageAcquirer(FakeDownloader(b"bad", [])).acquire(manifest, cache)
    assert not list(cache.iterdir())


def test_the_downloader_streams_exact_bytes_and_never_overwrites(tmp_path: Path) -> None:
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    from sysadmin_lab.application.image_acquisition import HttpsDownloader

    served = tmp_path / "served"
    served.mkdir()
    payload = os.urandom(3 * 1024 * 1024 + 17)  # several read chunks and a partial one
    (served / "media.iso").write_bytes(payload)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(served))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/media.iso"
        destination = tmp_path / "download.iso"
        HttpsDownloader(timeout_seconds=10).download(url, destination)
        assert destination.read_bytes() == payload
        with pytest.raises(FileExistsError):
            HttpsDownloader(timeout_seconds=10).download(url, destination)
        assert destination.read_bytes() == payload
    finally:
        server.shutdown()
        server.server_close()
