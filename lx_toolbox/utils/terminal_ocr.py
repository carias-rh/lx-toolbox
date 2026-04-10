from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps


LOGGER = logging.getLogger(__name__)

_REAL_DURATION_RE = re.compile(
    r"(?im)\b(?:real|rea[l1i|])\s+(?P<minutes>[0-9OoQq]+)\s*m\s*(?P<seconds>[0-9OoQq]+(?:\s*[.,]\s*[0-9OoQq]+)?)\s*(?:s|5)?\b"
)
_DATE_LINE_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+[A-Z]{2,5}\s+\d{4}\b"
)


@dataclass(slots=True)
class ParsedTerminalTiming:
    real_seconds: float | None = None
    started_at: str = ""
    raw_text: str = ""


def parse_terminal_timing_text(text: str) -> ParsedTerminalTiming | None:
    """Extract the shell's `date` output and `time real` duration from OCR text."""
    normalized_text = _normalize_ocr_text(text)
    if not normalized_text:
        return None

    parsed = ParsedTerminalTiming(raw_text=normalized_text)

    real_matches = list(_REAL_DURATION_RE.finditer(normalized_text))
    if real_matches:
        match = real_matches[-1]
        try:
            minutes = int(_normalize_duration_fragment(match.group("minutes")))
            seconds = float(_normalize_duration_fragment(match.group("seconds")))
            parsed.real_seconds = (minutes * 60) + seconds
        except ValueError:
            LOGGER.debug("Could not parse OCR duration from %r", match.group(0))

    date_match = _DATE_LINE_RE.search(normalized_text)
    if date_match:
        parsed.started_at = date_match.group(0)

    if parsed.real_seconds is None and not parsed.started_at:
        return None

    return parsed


def parse_terminal_timing_from_screenshot(path: str | Path) -> ParsedTerminalTiming | None:
    """
    OCR a terminal screenshot and extract the shell's `date` line and `real` duration.

    Returns ``None`` when OCR is unavailable or the screenshot cannot be parsed.
    """
    image_path = Path(path)
    if not image_path.is_file():
        return None

    tesseract_path = _get_tesseract_path()
    if not tesseract_path:
        LOGGER.debug("Skipping terminal OCR because tesseract is not installed.")
        return None

    try:
        with Image.open(image_path) as image:
            ocr_chunks: list[str] = []
            for region in _iter_candidate_regions(image):
                ocr_text = _run_tesseract(region, tesseract_path)
                if ocr_text.strip():
                    ocr_chunks.append(ocr_text)
    except Exception as exc:
        LOGGER.debug("Could not OCR screenshot %s: %s", image_path, exc)
        return None

    if not ocr_chunks:
        return None

    return parse_terminal_timing_text("\n".join(ocr_chunks))


def _normalize_ocr_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.replace(",", ".")
        line = re.sub(r"\s+", " ", line)
        line = re.sub(r"\brea[l1i|]\b", "real", line, flags=re.IGNORECASE)
        timing_prefix = re.match(r"(?i)^(real|user|sys)\b(.*)$", line)
        if timing_prefix:
            prefix = timing_prefix.group(1).lower()
            body = _normalize_timing_line_body(timing_prefix.group(2))
            line = f"{prefix} {body}".strip()
        lines.append(line)
    return "\n".join(lines)


def _normalize_duration_fragment(value: str) -> str:
    normalized = value.replace("O", "0").replace("o", "0").replace("Q", "0").replace("q", "0")
    normalized = normalized.replace(" ", "").replace(",", ".")
    if "." in normalized:
        whole, fraction = normalized.split(".", 1)
        if len(fraction) > 3 and fraction.endswith("5"):
            normalized = f"{whole}.{fraction[:-1]}"
    return normalized


def _normalize_timing_line_body(body: str) -> str:
    normalized = body.replace(",", ".")
    normalized = normalized.replace("O", "0").replace("o", "0").replace("Q", "0").replace("q", "0")
    normalized = re.sub(r"\s*([ms])\s*", r"\1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(?<=\d)\s+\.(?=\d)", ".", normalized)
    normalized = re.sub(r"(?<=\.)\s+(?=\d)", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if "s" not in normalized.lower():
        normalized = re.sub(r"(\.\d{3})5$", r"\1s", normalized)
    return normalized


def _iter_candidate_regions(image: Image.Image) -> list[Image.Image]:
    width, height = image.size
    top_height = max(1, int(height * 0.35))
    bottom_start = min(height - 1, max(0, int(height * 0.55)))

    regions = [
        image.copy(),
        image.crop((0, 0, width, top_height)),
        image.crop((0, bottom_start, width, height)),
    ]
    return [_preprocess_image(region) for region in regions]


def _preprocess_image(image: Image.Image) -> Image.Image:
    processed = image.convert("L")
    processed = ImageOps.autocontrast(processed)
    processed = processed.resize((processed.width * 2, processed.height * 2))
    processed = processed.filter(ImageFilter.SHARPEN)
    return processed.point(lambda pixel: 255 if pixel > 160 else 0)


def _run_tesseract(image: Image.Image, tesseract_path: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        image.save(temp_path, format="PNG")
        result = subprocess.run(
            [
                tesseract_path,
                str(temp_path),
                "stdout",
                "--psm",
                "6",
                "-c",
                "preserve_interword_spaces=1",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        if result.returncode != 0:
            LOGGER.debug("tesseract failed for %s: %s", temp_path, result.stderr.strip())
            return ""
        return result.stdout
    except Exception as exc:
        LOGGER.debug("tesseract invocation failed: %s", exc)
        return ""
    finally:
        temp_path.unlink(missing_ok=True)


@lru_cache(maxsize=1)
def _get_tesseract_path() -> str:
    return shutil.which("tesseract") or ""
