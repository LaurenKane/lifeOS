"""pdf_text.py — PDF bytes to text lines, via poppler's `pdftotext -layout`.

**Why a subprocess and not a Python PDF library.** The row parsers in this
package were written and validated against `pdftotext -layout` output
(`tools/bankparse/`, the reference implementation, and the recorded self-test
runs under `data/extracted/`). `-layout` reproduces the *physical* page layout —
column alignment, and therefore the character offsets the per-page column
classifiers depend on. A pure-Python library gives different whitespace, so
swapping one for the other means re-validating every parser against statements
nobody in this repo holds. The parser and the extractor are a matched pair.

**Consequences, stated rather than hidden.**

* `pdftotext` is a *system* binary from `poppler-utils`, not a pip package. The
  backend image installs it (`backend/Dockerfile`), and
  `pdftotext_available()` plus `PdfTextUnavailableError` make its absence an
  explicit, reportable condition rather than an `OSError` from deep inside a
  request handler.
* The adapters take `bytes` and poppler wants a file. `pdftotext -` on stdin also
  works and was checked to preserve `-layout` byte-for-byte (poppler 24.02), but
  the temp-file form is what was validated against the real statements and it
  works on every poppler build, including ones that cannot seek stdin. So:
  temp file, and stdin is noted rather than used.

**The seam.** `extract_pdf_text(payload: bytes) -> list[str]` is the injectable
seam every PDF adapter depends on. Substituting a plain `Callable[[bytes],
list[str]]` keeps the row parsers testable with a list of strings, keeps
`tests/egress/` able to prove the offline path without a real binary, and keeps
the subprocess out of every test that is not about extraction.

**Offline by construction.** `subprocess.run` with a fixed argument vector, no
`shell=True`, no network, no environment passthrough of interest. The only I/O
is writing the payload to a private temporary directory and reading it straight
back. `tests/egress/test_zero_egress.py` traces this path for `connect()`
syscalls once the extractor it injects is this one.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from finance.ingestion.adapters.base import AdapterParseError

__all__ = [
    "DEFAULT_EXTRACTION_TIMEOUT_SECONDS",
    "PDFTOTEXT_BINARY",
    "PdfTextExtractionError",
    "PdfTextUnavailableError",
    "extract_lines",
    "extract_pdf_text",
    "pdftotext_available",
]

#: poppler-utils' text extractor. Named so a test and an operator error message
#: can both talk about the same thing.
PDFTOTEXT_BINARY = "pdftotext"

#: Wall-clock ceiling for one extraction. A 7-year card statement is a few MB and
#: a few seconds; a malformed or hostile file should fail rather than hold a
#: request worker open indefinitely. `subprocess.TimeoutExpired` is translated
#: into `PdfTextExtractionError` by the caller-visible exception below.
DEFAULT_EXTRACTION_TIMEOUT_SECONDS = 120.0

# The filename used inside the temporary directory. It never reaches poppler as
# a user-controlled name; only the *contents* are user-controlled, and poppler
# parses contents either way.
_TEMP_FILENAME = "statement.pdf"

# stderr is diagnostic context for a failure, not something to log in full: a
# pathological PDF can emit thousands of lines and the request must not carry
# them into a response or a log line.
_MAX_STDERR_CHARS = 500


class PdfTextUnavailableError(RuntimeError):
    """`pdftotext` is not installed, so no PDF can be read.

    Separate from `PdfTextExtractionError` because the remedy differs: this one
    is fixed by installing `poppler-utils` in the runtime image, that one is
    fixed by the user uploading a different file.
    """


class PdfTextExtractionError(RuntimeError):
    """`pdftotext` ran and the payload did not yield usable text.

    Covers a non-PDF payload, a corrupt or encrypted PDF, and a timeout.
    """


def pdftotext_available() -> bool:
    """Whether `pdftotext` is on PATH.

    A capability probe, for a health check or a startup log line. Calling it does
    not guarantee a later extraction succeeds; it only says the binary is there.
    """
    return shutil.which(PDFTOTEXT_BINARY) is not None


def extract_pdf_text(
    payload: bytes,
    *,
    binary: str = PDFTOTEXT_BINARY,
    timeout: float = DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
) -> list[str]:
    """Extract the text layer of a PDF, preserving physical layout.

    Args:
        payload: The PDF bytes, exactly as uploaded.
        binary: The extractor to run. Overridable so a test can point at a stub.
        timeout: Seconds before the extraction is abandoned.

    Returns:
        Text lines, in page order, with `pdftotext`'s form-feed page separators
        preserved. Line endings are normalised to `\n` by the split, which is what
        the row parsers index against.

    Raises:
        PdfTextUnavailableError: `binary` is not installed.
        PdfTextExtractionError: The binary ran and failed, produced no text, or
            exceeded `timeout`.
    """
    if not payload:
        raise PdfTextExtractionError("empty payload: there is no PDF to extract")

    # A private directory (0700) rather than a bare mkstemp: poppler is handed a
    # path inside it, and the directory is removed by the context manager on every
    # exit path including the timeout one. Untrusted bytes exist on disk for the
    # lifetime of one call and no longer.
    with tempfile.TemporaryDirectory(prefix="lifeos-pdf-") as workdir:
        pdf_path = Path(workdir) / _TEMP_FILENAME
        pdf_path.write_bytes(payload)
        argv = [binary, "-layout", "-enc", "UTF-8", str(pdf_path), "-"]
        try:
            # Fixed argument vector, no shell, no user-controlled argument
            # positions: the only untrusted input is the file's contents, which
            # poppler has to parse either way.
            completed = subprocess.run(
                argv,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise PdfTextUnavailableError(
                f"{binary} is not installed. Install poppler-utils "
                "(Debian/Ubuntu: `apt-get install -y poppler-utils`); PDF "
                "import cannot read a file without it."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise PdfTextExtractionError(
                f"{binary} did not finish within {timeout:g}s"
            ) from exc

    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    if completed.returncode != 0:
        detail = (
            stderr[:_MAX_STDERR_CHARS] if stderr else f"exit {completed.returncode}"
        )
        raise PdfTextExtractionError(f"{binary} could not read the PDF: {detail}")

    # poppler is chatty about recoverable problems ("May not be a PDF file
    # (continuing anyway)") and still exits 0 with usable text, so stderr is
    # ignored on success rather than turned into a spurious failure.
    text = completed.stdout.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if not any(line.strip() for line in lines):
        raise PdfTextExtractionError(
            f"{binary} produced no text: the PDF has no text layer, or it is "
            "a scan that needs OCR"
        )
    return lines


def extract_lines(
    extractor: Callable[[bytes], list[str]],
    payload: bytes,
    *,
    provider: str,
) -> list[str] | AdapterParseError:
    """Run an extractor and turn an extraction failure into a parse error.

    An adapter's `parse` must never raise out of a request handler for a bad
    *file* — that is data, and `ImportResult.failed` exists precisely so the rest
    of a statement still lands. This is the one place that translation happens,
    so all three PDF adapters report an absent or broken extractor identically.

    Only this module's own exceptions are caught. An injected extractor that
    raises anything else is a bug in the caller, and hiding it here would make
    that bug look like a bad upload.
    """
    try:
        return extractor(payload)
    except (PdfTextUnavailableError, PdfTextExtractionError) as exc:
        return AdapterParseError(str(exc), None, provider)
