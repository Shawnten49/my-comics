from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageOps


class ThumbnailCache:
    def __init__(self, cache_dir: Path, width: int = 360) -> None:
        self.cache_dir = cache_dir
        self.width = width

    def _ensure_cache_dir(self) -> None:
        if not self.cache_dir.is_dir():
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, source: Path, width: Optional[int] = None) -> Path:
        target_width = max(80, min(960, int(width or self.width)))
        stat = source.stat()
        key = hashlib.sha1(
            (
                str(source)
                + f"|{stat.st_mtime_ns}|{stat.st_size}|{target_width}"
            ).encode("utf-8")
        ).hexdigest()
        target = self.cache_dir / f"{key}.webp"
        if target.is_file() and target.stat().st_size > 0:
            return target
        self._ensure_cache_dir()

        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            ratio = target_width / max(1, image.width)
            height = max(1, round(image.height * ratio))
            thumbnail = image.resize(
                (target_width, height),
                Image.Resampling.LANCZOS,
            )
            file_handle, temporary_name = tempfile.mkstemp(
                prefix="thumbnail-",
                suffix=".webp.part",
                dir=str(self.cache_dir),
            )
            os.close(file_handle)
            temporary = Path(temporary_name)
            try:
                thumbnail.save(
                    temporary,
                    format="WEBP",
                    quality=82,
                    method=4,
                )
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return target

    def cleanup(self, max_age_days: int = 7) -> int:
        if not self.cache_dir.is_dir():
            return 0
        cutoff = time.time() - max_age_days * 86400
        removed = 0
        for path in self.cache_dir.glob("*.webp"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def clear_all(self) -> int:
        if not self.cache_dir.is_dir():
            return 0
        removed = 0
        for path in self.cache_dir.glob("*.webp"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def warm(self, source: Path, width: Optional[int] = None) -> Path:
        return self.get(source, width=width)
