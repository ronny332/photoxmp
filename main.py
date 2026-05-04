#!/usr/bin/env python3
"""
Generate XMP sidecar metadata for images using an Ollama vision model.

Designed for portable DAM workflows (e.g. digiKam, Lightroom-compatible XMP readers).

Features
- Accepts a single file or a source directory via --src
- Recursive scan for directories
- Command-line options like --src and --ext
- Case-insensitive extension matching
- Defaults for CR2/HEIC/JPG/JPEG/PNG
- Creates XMP sidecars if missing
- Skips already-processed images unless --force is used
- Single model call per image with structured JSON output
- Optional HEIC support via pillow-heif if installed
- CR2 RAW support via rawpy if installed
- Sets owner/group/mode on created XMP files
- Verbose logging via -v / -vv
- Cron-friendly output mode via --no-progress
- Resizes images so the longest side is at most the configured maximum
- Writes:
    - XMP:Title
    - XMP:Description
    - XMP:Subject (keywords + structured prefixed tags)

Recommendations
- For large archives, use the default --resize-max 1024 first for speed.
- Re-run selected folders later with --resize-max 1344 or higher if you want more detail.
- Use --no-progress for cron jobs.
- Use -v or -vv when testing interactively.
"""

import argparse
import base64
import grp
import json
import logging
import os
import pwd
import subprocess
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from tqdm import tqdm

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

try:
    import rawpy
except Exception:
    rawpy = None


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3-vl:32b"
DEFAULT_EXTENSIONS = ["cr2", "heic", "jpg", "jpeg", "png"]
DEFAULT_RESIZE_MAX = 1024
DEFAULT_LOCATION_THRESHOLD = 0.85
DEFAULT_XMP_OWNER = "vol.pictures"
DEFAULT_XMP_GROUP = "vol.pictures"
DEFAULT_XMP_MODE = "660"

DEFAULT_PROMPT = """Analyze this image and return JSON only.

Use this exact schema:
{
  "title": "short descriptive title",
  "description": "1-2 concise factual sentences",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5", "keyword6", "keyword7", "keyword8", "keyword9", "keyword10"],
  "main_subjects": ["subject1", "subject2"],
  "scene_type": ["scene1", "scene2"],
  "people_count": 0,
  "contains_faces": false,
  "indoor_outdoor": "indoor|outdoor|unclear",
  "time_of_day": "daytime|night|sunrise|sunset|unclear",
  "lighting": ["lighting descriptor"],
  "weather": ["weather descriptor"],
  "season": ["season descriptor"],
  "identifiable_location": "",
  "location_confidence": 0.0,
  "uncertain_elements": []
}

Rules:
- Return valid JSON only. No markdown, no explanation.
- Describe only what is visually evident.
- Do not guess names of people, places, brands, or events unless clearly visible and identifiable from the image alone.
- Title should be short and specific.
- Description should be factual, concise, and non-repetitive.
- Provide 10 to 20 precise keywords.
- Prefer searchable keywords: objects, scene type, colors, lighting, mood, composition, weather, season, actions.
- Use singular nouns where possible.
- Avoid vague filler words like "nice", "beautiful", "image", "photo".
- Avoid duplicates.
- If no specific location is clearly identifiable, set "identifiable_location" to "" and "location_confidence" to 0.
- "people_count" should be a non-negative integer estimate.
- "contains_faces" should be true only if one or more faces are visible.
"""

logger = logging.getLogger("photoxmp")


def setup_logging(verbosity, no_progress):
    if no_progress:
        level = logging.ERROR
    else:
        if verbosity <= 0:
            level = logging.INFO
        else:
            level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s" if verbosity >= 2 else "%(levelname)s %(message)s",
    )


def ts_now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def cron_log(status, src, dst=None, extra=None):
    line = f"[{ts_now()}] {status:<5} {src}"
    if dst:
        line += f" -> {dst}"
    if extra:
        line += f" :: {extra}"
    print(line, flush=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate XMP sidecar metadata for images using an Ollama vision model.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Recommendations:\n"
            "  - Use --resize-max 1024 for large first-pass archive processing.\n"
            "  - Use --resize-max 1344 for selected folders if you want more detail later.\n"
            "  - Use --no-progress for cron jobs.\n"
            "  - Use -v or -vv for troubleshooting and prompt tuning."
        ),
    )
    parser.add_argument(
        "--src",
        required=True,
        help="Source image file or directory containing images."
    )
    parser.add_argument(
        "--ext",
        nargs="+",
        default=DEFAULT_EXTENSIONS,
        help="Extensions to include for directory scans, e.g. --ext cr2 heic jpg png"
    )
    parser.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
        help="Ollama base URL."
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Ollama vision model name."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess files even if XMP metadata already exists."
    )
    parser.add_argument(
        "--resize-max",
        type=int,
        default=DEFAULT_RESIZE_MAX,
        help="Resize images so the longest side is at most this many pixels. Recommended default is 1024. Use 0 to disable."
    )
    parser.add_argument(
        "--prompt-file",
        help="Optional text file containing a custom prompt."
    )
    parser.add_argument(
        "--location-threshold",
        type=float,
        default=DEFAULT_LOCATION_THRESHOLD,
        help="Write identifiable_location as a keyword only if location_confidence >= this value."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze images and print metadata, but do not write XMP files."
    )
    parser.add_argument(
        "--xmp-owner",
        default=DEFAULT_XMP_OWNER,
        help="Owner to apply to written XMP files."
    )
    parser.add_argument(
        "--xmp-group",
        default=DEFAULT_XMP_GROUP,
        help="Group to apply to written XMP files."
    )
    parser.add_argument(
        "--xmp-mode",
        default=DEFAULT_XMP_MODE,
        help="File mode to apply to written XMP files, e.g. 660 or 640."
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable tqdm progress bar and print one simple log line per file, useful for cron jobs."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity. Use -v for more info, -vv for debug."
    )
    return parser.parse_args()


def normalize_extensions(ext_list):
    result = set()
    for ext in ext_list:
        ext = ext.strip().lower()
        if not ext:
            continue
        if ext.startswith("*."):
            ext = ext[2:]
        elif ext.startswith("."):
            ext = ext[1:]
        result.add(ext)
    normalized = sorted(result)
    logger.debug("Normalized extensions: %s", normalized)
    return normalized


def file_matches_extension(path, extensions):
    match = path.suffix.lower().lstrip(".") in set(e.lower() for e in extensions)
    logger.debug("Extension match for %s: %s", path, match)
    return match


def collect_files(source_path, extensions):
    source_path = Path(source_path)
    logger.info("Collecting files from: %s", source_path)

    if source_path.is_file():
        if not file_matches_extension(source_path, extensions):
            raise SystemExit(
                f"File extension not allowed: {source_path}\n"
                f"Allowed extensions: {', '.join(extensions)}"
            )
        logger.info("Single file input detected")
        return [str(source_path)]

    if source_path.is_dir():
        matches = []
        extset = set(e.lower() for e in extensions)
        for root, _, files in os.walk(source_path):
            for name in files:
                suffix = Path(name).suffix.lower().lstrip(".")
                if suffix in extset:
                    matches.append(str(Path(root) / name))
        logger.info("Collected %d matching file(s)", len(matches))
        if logger.isEnabledFor(logging.DEBUG):
            for match in matches:
                logger.debug("Matched file: %s", match)
        return matches

    raise SystemExit(f"Source path does not exist: {source_path}")


def ensure_xmp_exists(image_path):
    xmp_path = Path(image_path).with_suffix(".xmp")
    if not xmp_path.exists():
        logger.debug("Creating missing XMP file: %s", xmp_path)
        xmp_path.touch()
    else:
        logger.debug("XMP file already exists: %s", xmp_path)
    return xmp_path


def apply_file_permissions(path, owner, group, mode):
    logger.debug("Applying permissions to %s: owner=%s group=%s mode=%s", path, owner, group, mode)

    try:
        uid = pwd.getpwnam(owner).pw_uid if owner else -1
    except KeyError:
        raise RuntimeError(f"Owner user not found: {owner}")

    try:
        gid = grp.getgrnam(group).gr_gid if group else -1
    except KeyError:
        raise RuntimeError(f"Group not found: {group}")

    try:
        os.chown(path, uid, gid)
    except PermissionError as e:
        raise RuntimeError(f"Failed to chown '{path}' to {owner}:{group}: {e}")

    try:
        os.chmod(path, int(str(mode), 8))
    except Exception as e:
        raise RuntimeError(f"Failed to chmod '{path}' to {mode}: {e}")


def convert_to_base64(pil_image):
    logger.debug("Converting image to base64")
    buffered = BytesIO()
    rgb_im = pil_image.convert("RGB")
    rgb_im.save(buffered, format="JPEG", quality=92)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def open_image_generic(image_path):
    suffix = Path(image_path).suffix.lower()
    logger.debug("Opening image %s with suffix %s", image_path, suffix)

    if suffix == ".cr2":
        if rawpy is None:
            raise RuntimeError(
                "CR2 support requires 'rawpy'. Install it with: pip install rawpy"
            )
        logger.debug("Using rawpy to open CR2 image: %s", image_path)
        with rawpy.imread(str(image_path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False)
        return Image.fromarray(rgb)

    return Image.open(image_path)


def prepare_image(image_path, resize_max):
    pil_image = open_image_generic(image_path)
    logger.debug("Loaded image %s with original size %s", image_path, pil_image.size)

    if resize_max and resize_max > 0:
        width, height = pil_image.size
        longest_side = max(width, height)

        if longest_side > resize_max:
            scale = resize_max / float(longest_side)
            new_width = max(1, int(width * scale))
            new_height = max(1, int(height * scale))
            logger.debug(
                "Resizing image %s from %dx%d to %dx%d",
                image_path, width, height, new_width, new_height
            )
            pil_image = pil_image.resize((new_width, new_height), Image.LANCZOS)
        else:
            logger.debug(
                "No resize needed for %s; longest side already <= %d",
                image_path, resize_max
            )
    else:
        logger.debug("Resize disabled for %s", image_path)

    return pil_image


def ollama_generate(ollama_url, model, prompt, image_b64):
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "options": {
            "temperature": 0
        }
    }

    import urllib.request
    import urllib.error

    url = ollama_url.rstrip("/") + "/api/generate"
    logger.debug("Sending request to Ollama: %s model=%s", url, model)

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            logger.debug("Received response from Ollama")
            return data.get("response", "")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP error {e.code}: {body}")
    except Exception as e:
        raise RuntimeError(f"Failed to call Ollama: {e}")


def extract_json(text):
    logger.debug("Extracting JSON from model output")
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def to_clean_list(value):
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",")]
    elif isinstance(value, list):
        items = [str(v).strip() for v in value]
    else:
        items = []
    return [x for x in items if x]


def dedupe_keep_order(items):
    out = []
    seen = set()
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def sanitize_metadata(data):
    logger.debug("Sanitizing metadata: %s", data)

    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    keywords = dedupe_keep_order(to_clean_list(data.get("keywords", [])))
    main_subjects = dedupe_keep_order(to_clean_list(data.get("main_subjects", [])))
    scene_type = dedupe_keep_order(to_clean_list(data.get("scene_type", [])))
    lighting = dedupe_keep_order(to_clean_list(data.get("lighting", [])))
    weather = dedupe_keep_order(to_clean_list(data.get("weather", [])))
    season = dedupe_keep_order(to_clean_list(data.get("season", [])))
    uncertain_elements = dedupe_keep_order(to_clean_list(data.get("uncertain_elements", [])))

    try:
        people_count = int(data.get("people_count", 0))
        if people_count < 0:
            people_count = 0
    except Exception:
        people_count = 0

    contains_faces = bool(data.get("contains_faces", False))
    indoor_outdoor = str(data.get("indoor_outdoor", "unclear")).strip().lower()
    time_of_day = str(data.get("time_of_day", "unclear")).strip().lower()
    identifiable_location = str(data.get("identifiable_location", "")).strip()

    try:
        location_confidence = float(data.get("location_confidence", 0.0))
    except Exception:
        location_confidence = 0.0

    if indoor_outdoor not in {"indoor", "outdoor", "unclear"}:
        indoor_outdoor = "unclear"

    if time_of_day not in {"daytime", "night", "sunrise", "sunset", "unclear"}:
        time_of_day = "unclear"

    sanitized = {
        "title": title,
        "description": description,
        "keywords": keywords,
        "main_subjects": main_subjects,
        "scene_type": scene_type,
        "people_count": people_count,
        "contains_faces": contains_faces,
        "indoor_outdoor": indoor_outdoor,
        "time_of_day": time_of_day,
        "lighting": lighting,
        "weather": weather,
        "season": season,
        "identifiable_location": identifiable_location,
        "location_confidence": location_confidence,
        "uncertain_elements": uncertain_elements,
    }

    logger.debug("Sanitized metadata result: %s", sanitized)
    return sanitized


def slugify_tag(value):
    return (
        value.strip()
        .lower()
        .replace("/", "-")
        .replace("\\", "-")
        .replace(" ", "-")
    )


def get_image_dimensions(image_path):
    try:
        img = open_image_generic(image_path)
        try:
            return img.size
        finally:
            try:
                img.close()
            except Exception:
                pass
    except Exception:
        return None, None


def build_subject_keywords(metadata, image_path, location_threshold):
    logger.debug("Building subject keywords for %s", image_path)
    subjects = []

    subjects.extend(metadata["keywords"])

    for item in metadata["main_subjects"]:
        subjects.append(f"subject:{slugify_tag(item)}")

    for item in metadata["scene_type"]:
        subjects.append(f"scene:{slugify_tag(item)}")

    if metadata["people_count"] == 0:
        subjects.append("people:0")
    elif metadata["people_count"] == 1:
        subjects.append("people:1")
    elif metadata["people_count"] == 2:
        subjects.append("people:2")
    else:
        subjects.append(f"people:{metadata['people_count']}")
        subjects.append("people:group")

    subjects.append(f"faces:{'yes' if metadata['contains_faces'] else 'no'}")

    if metadata["indoor_outdoor"] != "unclear":
        subjects.append(f"setting:{metadata['indoor_outdoor']}")

    if metadata["time_of_day"] != "unclear":
        subjects.append(f"time:{metadata['time_of_day']}")

    for item in metadata["lighting"]:
        subjects.append(f"lighting:{slugify_tag(item)}")

    for item in metadata["weather"]:
        subjects.append(f"weather:{slugify_tag(item)}")

    for item in metadata["season"]:
        subjects.append(f"season:{slugify_tag(item)}")

    if metadata["identifiable_location"] and metadata["location_confidence"] >= location_threshold:
        subjects.append(f"location:{slugify_tag(metadata['identifiable_location'])}")

    width, height = get_image_dimensions(image_path)
    if width and height:
        if width > height:
            subjects.append("orientation:landscape")
        elif height > width:
            subjects.append("orientation:portrait")
        else:
            subjects.append("orientation:square")

    if metadata["uncertain_elements"]:
        subjects.append("review:uncertain")

    subjects = dedupe_keep_order(subjects)
    logger.debug("Built subject keywords for %s: %s", image_path, subjects)
    return subjects


def is_image_processed(image_path):
    xmp_path = Path(image_path).with_suffix(".xmp")
    if not xmp_path.exists():
        logger.debug("No XMP sidecar found for %s", image_path)
        return False

    cmd = [
        "exiftool",
        "-j",
        "-XMP:Title",
        "-XMP:Description",
        "-XMP:Subject",
        str(xmp_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        if not data:
            logger.debug("No tag data found in %s", xmp_path)
            return False
        item = data[0]
        title = str(item.get("Title", "")).strip()
        description = str(item.get("Description", "")).strip()
        subject = item.get("Subject", [])
        if isinstance(subject, str):
            subject = [subject]
        keywords_ok = any(str(s).strip() for s in subject)
        processed = bool(title and description and keywords_ok)
        logger.debug("Processed check for %s: %s", image_path, processed)
        return processed
    except Exception as e:
        logger.debug("Failed processed-check for %s: %s", image_path, e)
        return False


def write_xmp_metadata(image_path, metadata, subject_keywords, owner, group, mode):
    xmp_path = ensure_xmp_exists(image_path)
    logger.debug("Writing XMP metadata to %s", xmp_path)

    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-XMP:Title={metadata['title']}",
        f"-XMP:Description={metadata['description']}",
        "-XMP:Subject=",
    ]

    for kw in subject_keywords:
        cmd.append(f"-XMP:Subject+={kw}")

    result = subprocess.run(cmd + [str(xmp_path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ExifTool write failed")

    logger.debug("XMP write successful for %s", xmp_path)
    apply_file_permissions(str(xmp_path), owner, group, mode)


def load_prompt(prompt_file):
    if not prompt_file:
        logger.debug("Using built-in default prompt")
        return DEFAULT_PROMPT
    logger.info("Loading prompt from file: %s", prompt_file)
    return Path(prompt_file).read_text(encoding="utf-8")


def process_one_image(image_path, args, prompt):
    logger.info("Processing image: %s", image_path)

    pil_image = prepare_image(image_path, args.resize_max)
    image_b64 = convert_to_base64(pil_image)

    raw = ollama_generate(args.ollama_url, args.model, prompt, image_b64)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Raw model output for %s: %s", image_path, raw)

    parsed = extract_json(raw)
    metadata = sanitize_metadata(parsed)

    if not metadata["title"]:
        raise RuntimeError("Model returned empty title")
    if not metadata["description"]:
        raise RuntimeError("Model returned empty description")
    if not metadata["keywords"]:
        raise RuntimeError("Model returned no keywords")

    subject_keywords = build_subject_keywords(
        metadata,
        image_path=image_path,
        location_threshold=args.location_threshold
    )

    if not args.dry_run:
        write_xmp_metadata(
            image_path,
            metadata,
            subject_keywords,
            owner=args.xmp_owner,
            group=args.xmp_group,
            mode=args.xmp_mode,
        )
    else:
        logger.info("Dry-run enabled, not writing XMP for %s", image_path)

    return metadata, subject_keywords


def check_dependencies():
    logger.debug("Checking dependencies")
    try:
        subprocess.run(["exiftool", "-ver"], capture_output=True, text=True, check=True)
    except Exception:
        raise SystemExit(
            "Missing dependency: exiftool\n"
            "Install it first, e.g.:\n"
            "  macOS: brew install exiftool\n"
            "  Debian/Ubuntu: sudo apt install libimage-exiftool-perl\n"
        )


def main():
    args = parse_args()
    setup_logging(args.verbose, args.no_progress)

    logger.info("Starting photoxmp")
    logger.debug("Arguments: %s", args)

    check_dependencies()

    source_path = Path(args.src)
    extensions = normalize_extensions(args.ext)
    prompt = load_prompt(args.prompt_file)

    files = collect_files(source_path, extensions)

    if not files:
        if args.no_progress:
            cron_log("DONE", "processed=0 skipped=0 failed=0")
        else:
            logger.warning("No matching image files found.")
        return

    logger.info("Found %d image(s)", len(files))

    processed = 0
    skipped = 0
    failed = 0

    iterator = files if args.no_progress else tqdm(files, desc="Processing images")

    for image_path in iterator:
        xmp_path = str(Path(image_path).with_suffix(".xmp"))
        try:
            if not args.force and is_image_processed(image_path):
                skipped += 1
                if args.no_progress:
                    cron_log("SKIP", image_path, xmp_path)
                else:
                    logger.info("Skipping already processed image: %s", image_path)
                continue

            metadata, subject_keywords = process_one_image(image_path, args, prompt)
            processed += 1

            if args.no_progress:
                cron_log("OK", image_path, xmp_path)
            else:
                logger.info("Processed: %s", image_path)
                logger.info("  Title: %s", metadata["title"])
                logger.info("  Description: %s", metadata["description"])
                logger.info("  Keywords: %s", ", ".join(metadata["keywords"]))
                logger.info("  XMP owner/group/mode: %s:%s %s", args.xmp_owner, args.xmp_group, args.xmp_mode)

                if args.verbose >= 2:
                    logger.debug("  Main subjects: %s", ", ".join(metadata["main_subjects"]))
                    logger.debug("  Scene type: %s", ", ".join(metadata["scene_type"]))
                    logger.debug("  Subject tags: %s", ", ".join(subject_keywords))

        except UnidentifiedImageError:
            failed += 1
            if args.no_progress:
                cron_log("ERR", image_path, xmp_path, "Unreadable image")
            else:
                logger.exception("Skipping unreadable image: %s", image_path)
        except Exception as e:
            failed += 1
            if args.no_progress:
                cron_log("ERR", image_path, xmp_path, str(e))
            else:
                logger.exception("Error processing %s: %s", image_path, e)

    if args.no_progress:
        cron_log("DONE", f"processed={processed} skipped={skipped} failed={failed}")
    else:
        logger.info("Done.")
        logger.info("Processed: %d", processed)
        logger.info("Skipped:   %d", skipped)
        logger.info("Failed:    %d", failed)


if __name__ == "__main__":
    main()

