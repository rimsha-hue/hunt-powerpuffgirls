"""Upload validation by magic bytes, not by file name or client-declared content type."""

import io
import zipfile
from typing import Literal

FileKind = Literal["pdf", "docx"]


class UploadRejectedError(ValueError):
    """The upload is not an accepted resume file. The message is safe to show the user."""


def sniff_resume(data: bytes, max_bytes: int) -> FileKind:
    if not data:
        raise UploadRejectedError("The file is empty.")
    if len(data) > max_bytes:
        raise UploadRejectedError(f"The file is larger than {max_bytes // (1024 * 1024)} MB.")
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile as exc:
            raise UploadRejectedError("The file is not a valid DOCX document.") from exc
        if "word/document.xml" in names and "[Content_Types].xml" in names:
            return "docx"
        raise UploadRejectedError("The ZIP file is not a Word document.")
    raise UploadRejectedError("Only PDF and DOCX resumes are accepted.")
