"""Local storage for inspection photos."""

import uuid
from pathlib import Path


class InvalidImage(ValueError):
    """The uploaded file is not an accepted image or is too large."""


# Magic-byte signatures of the accepted formats.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
)

MEDIA_TYPES: dict[str, str] = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def detect_image_type(data: bytes) -> str | None:
    """Return the file extension for JPEG/PNG/WEBP data, or None if not recognised."""
    for signature, ext in _SIGNATURES:
        if data.startswith(signature):
            return ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class ImageStorage:
    """Saves and deletes photos under a base directory using random file names."""

    def __init__(self, base_dir: Path, max_bytes: int) -> None:
        self.base_dir = base_dir
        self.max_bytes = max_bytes

    def save(self, data: bytes) -> str:
        """Validate and store an image; return its path relative to the base dir."""
        if not data:
            raise InvalidImage("El archivo está vacío.")
        if len(data) > self.max_bytes:
            raise InvalidImage(f"La foto supera el tamaño máximo ({self.max_bytes // (1024 * 1024)} MB).")
        ext = detect_image_type(data)
        if ext is None:
            raise InvalidImage("Formato no admitido. Usa una foto JPG, PNG o WEBP.")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.{ext}"
        (self.base_dir / name).write_bytes(data)
        return name

    def path(self, name: str) -> Path:
        """Absolute path of a stored image; rejects names that escape the base dir."""
        candidate = (self.base_dir / name).resolve()
        if candidate.parent != self.base_dir.resolve():
            raise InvalidImage("Ruta de imagen no válida.")
        return candidate

    def read(self, name: str) -> bytes:
        """Return the bytes of a stored image."""
        return self.path(name).read_bytes()

    def delete(self, name: str) -> None:
        """Delete a stored image if it exists."""
        self.path(name).unlink(missing_ok=True)

    @staticmethod
    def media_type(name: str) -> str:
        """HTTP media type for a stored image name."""
        return MEDIA_TYPES.get(name.rsplit(".", 1)[-1], "application/octet-stream")
