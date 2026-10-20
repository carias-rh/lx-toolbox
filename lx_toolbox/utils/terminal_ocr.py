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

# GNOME Terminal draws a slashed zero. Tesseract reads that glyph as @, ®, or Ø
# rather than 0, which drops bash lines like "real 0m29.904s".
# Letter-shaped zeros. "@" and "®" are handled separately because tesseract
# sometimes emits them *in addition to* a 0 for the same slashed-zero glyph.
_OCR_LETTER_ZERO_TRANSLATION = str.maketrans({
    "Ø": "0",
    "ø": "0",
    "Θ": "0",
    "θ": "0",
    "O": "0",
    "o": "0",
    "Q": "0",
    "q": "0",
})
_OCR_SLASHED_ZERO_CHARS = set("@®©°")
_REAL_DURATION_RE = re.compile(
    r"(?im)^real\s+(?P<minutes>[0-9]+)m(?P<seconds>[0-9]+(?:\.[0-9]+)?)s?\s*$"
)
_TIME_LAB_COMMAND_RE = re.compile(r"(?i)\btime\s+lab\b")
_TIMING_PREFIX_RE = re.compile(r"(?i)^(rea[l1i|][a-z]*|user|sys)\b(.*)$")
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
    time_commands = list(_TIME_LAB_COMMAND_RE.finditer(normalized_text))
    # A later `time lab` with no `real` line under it has not finished.
    # The previous command's timing is still on screen and is not this phase.
    chosen_real = real_matches[-1] if real_matches else None
    if chosen_real and time_commands and time_commands[-1].start() > chosen_real.end():
        chosen_real = None

    if chosen_real:
        try:
            minutes = int(_normalize_duration_fragment(chosen_real.group("minutes")))
            seconds = float(_normalize_duration_fragment(chosen_real.group("seconds")))
            parsed.real_seconds = (minutes * 60) + seconds
        except ValueError:
            LOGGER.debug("Could not parse OCR duration from %r", chosen_real.group(0))

    date_matches = list(_DATE_LINE_RE.finditer(normalized_text))
    if chosen_real:
        date_matches = [match for match in date_matches if match.start() < chosen_real.start()]
    if date_matches:
        parsed.started_at = date_matches[-1].group(0)

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

    # The full frame is first. Its last `real` line is the lab command; cropped
    # regions are only a fallback when that frame does not yield a duration.
    # Concatenating regions lets an earlier `time` block win if a crop re-reads it.
    chosen: ParsedTerminalTiming | None = None
    try:
        with Image.open(image_path) as image:
            for index, region in enumerate(_iter_candidate_regions(image)):
                ocr_text = _run_tesseract(region, tesseract_path)
                if not ocr_text.strip():
                    continue
                # A crop of an earlier `time` block must not override the full
                # frame when the lab command on screen has not printed `real` yet.
                if index == 0 and _lab_command_still_running(ocr_text):
                    return parse_terminal_timing_text(ocr_text)
                parsed = parse_terminal_timing_text(ocr_text)
                if not parsed:
                    continue
                if parsed.real_seconds is not None:
                    if chosen and chosen.started_at and not parsed.started_at:
                        parsed.started_at = chosen.started_at
                    return parsed
                if chosen is None:
                    chosen = parsed
                elif parsed.started_at and not chosen.started_at:
                    chosen.started_at = parsed.started_at
    except Exception as exc:
        LOGGER.debug("Could not OCR screenshot %s: %s", image_path, exc)
        return None

    return chosen


def _normalize_ocr_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.replace(",", ".")
        line = re.sub(r"\s+", " ", line)
        timing_prefix = _TIMING_PREFIX_RE.match(line)
        if timing_prefix:
            prefix_raw = timing_prefix.group(1).lower()
            if prefix_raw.startswith("rea"):
                prefix = "real"
            elif prefix_raw.startswith("user"):
                prefix = "user"
            else:
                prefix = "sys"
            body = _normalize_timing_line_body(timing_prefix.group(2))
            line = f"{prefix} {body}".strip()
        lines.append(line)
    return "\n".join(lines)


def _lab_command_still_running(text: str) -> bool:
    normalized_text = _normalize_ocr_text(text)
    real_matches = list(_REAL_DURATION_RE.finditer(normalized_text))
    time_commands = list(_TIME_LAB_COMMAND_RE.finditer(normalized_text))
    if not time_commands:
        return False
    if not real_matches:
        return True
    return time_commands[-1].start() > real_matches[-1].end()


def _translate_ocr_digits(value: str) -> str:
    """Map slashed-zero OCR noise to 0, dropping a second read of the same glyph."""
    translated = value.translate(_OCR_LETTER_ZERO_TRANSLATION)
    output: list[str] = []
    for char in translated:
        if char in _OCR_SLASHED_ZERO_CHARS:
            if output and output[-1] == "0":
                continue
            output.append("0")
        else:
            output.append(char)
    return "".join(output)


def _normalize_duration_fragment(value: str) -> str:
    normalized = _translate_ocr_digits(value)
    normalized = normalized.replace(" ", "").replace(",", ".")
    if "." in normalized:
        whole, fraction = normalized.split(".", 1)
        if len(fraction) > 3 and fraction.endswith("5"):
            normalized = f"{whole}.{fraction[:-1]}"
    return normalized


def _normalize_timing_line_body(body: str) -> str:
    normalized = _translate_ocr_digits(body.replace(",", "."))
    # "1" is sometimes read as l, I, or | inside the duration.
    normalized = re.sub(r"(?<=\d)[lI|](?=\d|\s*\.)", "1", normalized)
    normalized = re.sub(r"(?<=m)[lI|](?=\d|\s*\.)", "1", normalized)
    # The minutes unit "m" is sometimes read as "n" (0n4.445s).
    normalized = re.sub(r"(?<=\d)\s*n\s*(?=\d)", "m", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*([ms])\s*", r"\1", normalized, flags=re.IGNORECASE)
    # "8 . 438" and "8 .438" — the decimal point is tiny and gains spaces.
    normalized = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", normalized)
    # A dropped decimal point leaves a space: "28 569s" -> "28.569s".
    normalized = re.sub(r"(?<=\d)\s+(?=\d{3}s\b)", ".", normalized, flags=re.IGNORECASE)
    # "m7.261s" lost its leading zero.
    normalized = re.sub(r"(?<!\d)m(?=\d)", "0m", normalized, flags=re.IGNORECASE)
    if "s" not in normalized.lower():
        normalized = re.sub(r"(\.\d{3})5$", r"\1s", normalized)
    # "42.721s5" — the extra 5 is a second guess at the trailing s.
    normalized = re.sub(r"(s)5\b", r"\1", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(s).*$", r"\1", normalized, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", normalized).strip()


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
