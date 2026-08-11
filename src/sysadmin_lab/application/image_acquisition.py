from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from sysadmin_lab.domain.images import ImageManifest, verify_installation_source


class Downloader(Protocol):
    def download(self, url: str, destination: Path) -> None: ...


class HttpsDownloader:
    """Streaming downloader used only for manifest-pinned HTTPS artifacts."""

    def __init__(self, *, timeout_seconds: float = 60.0) -> None:
        self._timeout_seconds = timeout_seconds

    def download(self, url: str, destination: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "linux-admin-lab/0.1"})
        with (
            urllib.request.urlopen(request, timeout=self._timeout_seconds) as response,
            destination.open("xb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())


class ImageAcquirer:
    def __init__(self, downloader: Downloader) -> None:
        self._downloader = downloader

    def acquire(self, manifest: ImageManifest, cache_directory: Path) -> Path:
        cache_directory.mkdir(parents=True, exist_ok=True)
        target = cache_directory / manifest.source.filename
        if target.exists():
            verify_installation_source(manifest, target)
            return target

        temporary = cache_directory / f".{manifest.source.filename}.partial-{uuid4().hex}"
        try:
            self._downloader.download(str(manifest.source.url), temporary)
            verify_installation_source(manifest, temporary, require_filename=False)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target
