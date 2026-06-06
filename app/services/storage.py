import logging
from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings
from app.core.exceptions import BadGatewayError

logger = logging.getLogger(__name__)


async def upload_file(
	data: bytes,
	filename: str,
	content_type: str,
	public_url_base: str,
	subdir: str = "",
) -> dict:
	"""Write a file to local storage and return its metadata.

	The file is stored under ``MEDIA_DIR / subdir / {uuid}.{ext}``.
	``subdir`` must be a single directory name without path separators
	and must not contain ``..``.  An empty string places the file directly
	in the media root.
	"""
	if subdir:
		subdir = subdir.strip("/\\")
		if not subdir or subdir == "." or ".." in subdir or "/" in subdir or "\\" in subdir:
			raise ValueError(f"Invalid subdirectory name: {subdir!r}")

	settings = get_settings()

	media_root = Path(settings.MEDIA_DIR)
	dest = (media_root / subdir) if subdir else media_root
	dest.mkdir(parents=True, exist_ok=True)

	ext = filename.split(".")[-1] if "." in filename else ""
	file_name = f"{uuid4()}.{ext}" if ext else str(uuid4())
	local_path = dest / file_name
	media_prefix = f"{subdir}/" if subdir else ""
	file_url = f"{public_url_base.rstrip('/')}/media/{media_prefix}{file_name}"

	try:
		local_path.write_bytes(data)
	except OSError as e:
		logger.error("Failed to write %s to %s: %s", filename, dest, e)
		raise BadGatewayError("File failed to upload. Please try again after some time.") from e

	return {
		"filename": filename,
		"mime_type": content_type or "application/octet-stream",
		"file_size": len(data),
		"file_url": file_url,
	}


async def upload_medical_file(
	data: bytes,
	filename: str,
	content_type: str,
	public_url_base: str,
) -> dict:
	"""Write a medical file to the media root (legacy wrapper)."""
	return await upload_file(data, filename, content_type, public_url_base, subdir="")


def delete_medical_file_by_url(file_url: str) -> None:
	"""Delete a file previously stored under MEDIA_DIR (best-effort)."""
	if "/media/" not in file_url:
		return
	name = file_url.split("/media/", 1)[-1].split("?")[0]
	if not name or ".." in name:
		return
	settings = get_settings()
	path = Path(settings.MEDIA_DIR) / name
	try:
		path.unlink(missing_ok=True)
	except OSError as exc:
		logger.warning("Failed to delete media file %s: %s", path, exc)
