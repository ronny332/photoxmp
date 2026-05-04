# PhotoXMP

A part of my script collection for downloading images from iCloud with [kei](https://github.com/rhoopr/kei) 
and later adding them to [Nextcloud](https://github.com/nextcloud).
In between XMP sidecar metadata gets created for the original files, that's what this script does:

Generate XMP sidecar metadata for images using a local Ollama vision model.

This tool is designed for portable photo archive workflows with software like:

- digiKam
- Lightroom / Adobe Camera Raw compatible tools
- other DAM tools that read `.xmp` sidecars

It analyzes images locally with an Ollama vision model and writes metadata like:

- title
- description
- keywords
- structured keyword tags

into an `.xmp` sidecar next to the image.

---

## Features

- processes a **single image** or a **whole directory**
- recursive directory scan
- supports:
  - `CR2`
  - `HEIC`
  - `JPG`
  - `JPEG`
  - `PNG`
- writes `.xmp` sidecar files
- skips already processed files unless `--force` is used
- sets owner / group / permissions on created XMP files
- supports verbose logging
- supports a cron-friendly `--no-progress` mode
- resizes images before inference for much faster processing
- uses local Ollama models, no cloud required

---

## Why this exists

This script was built for a large personal photo archive with mixed formats and long-term portability in mind.

Goals:

- avoid subscription lock-in
- keep metadata portable
- work well with digiKam and similar tools
- generate metadata locally with AI
- keep original images untouched
- use `.xmp` sidecars for compatibility and non-destructive workflows

---

## Requirements

### System packages

#### Debian / Ubuntu
```bash
sudo apt install libimage-exiftool-perl
```

#### macOS
```bash
brew install exiftool
```

---

## Python packages

Recommended packages:

```bash
pip install pillow tqdm rawpy pillow-heif
```

If you use a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pillow tqdm rawpy pillow-heif
```

---

## Ollama

You need a running Ollama server with a vision model.

Example model used successfully:

- `qwen3-vl:32b`

Example server:
- `http://127.0.0.1:11434`
- or a remote/local network Ollama instance like `http://192.168.1.17:11434`

---

## Recommended model

A very good model for this use case is:

- `qwen3-vl:32b`

It provides:
- strong image understanding
- good structured JSON output
- useful title / description / keyword generation

A Nvidia RTX5090 takes about 30 seconds per image.

---

## Supported formats

Default extensions:

- `cr2`
- `heic`
- `jpg`
- `jpeg`
- `png`

Extension matching is case-insensitive.

So these work too:

- `.CR2`
- `.HEIC`
- `.JPG`
- `.JPEG`
- `.PNG`

---

## Usage

### Process a whole directory

```bash
python3 main.py --src /path/to/photos
```

### Process a single file

```bash
python3 main.py --src /path/to/image.CR2
```

### Use a remote Ollama server

```bash
python3 main.py --src /path/to/photos --ollama-url http://10.0.0.73:11434
```

### Specify the model

```bash
python3 main.py --src /path/to/photos --model qwen3-vl:32b
```

### Force reprocessing

```bash
python3 main.py --src /path/to/photos --force
```

### Dry run

Analyze images but do not write XMP files:

```bash
python3 main.py --src /path/to/photos --dry-run
```

### Verbose mode

```bash
python3 main.py --src /path/to/photos -v
```

### Very verbose / debug mode

```bash
python3 main.py --src /path/to/photos -vv
```

### Cron-friendly mode

```bash
python3 main.py --src /path/to/photos --no-progress
```

---

## Resize behavior

The script resizes images before sending them to the model.

It uses:

- `--resize-max`

This means:

> the longest side of the image is resized to at most this value, while keeping aspect ratio.

### Default

```bash
--resize-max 1024
```

### Why 1024?

This is a good speed/quality balance for large archives.

- much faster than full-resolution images
- still very good for:
  - titles
  - descriptions
  - keywords
  - people count
  - scene type
  - indoor/outdoor
  - general archive metadata

### Recommendation

For a large archive:
- first pass: use the default `1024`

For selected folders later:
- rerun with `1344` or higher if you want more detail

Example:

```bash
python3 main.py --src /path/to/special-folder --resize-max 1344 --force
```

---

## What metadata is written

The model returns structured JSON with fields like:

- `title`
- `description`
- `keywords`
- `main_subjects`
- `scene_type`
- `people_count`
- `contains_faces`
- `indoor_outdoor`
- `time_of_day`
- `lighting`
- `weather`
- `season`
- `identifiable_location`
- `location_confidence`
- `uncertain_elements`

The script writes:

- `XMP:Title`
- `XMP:Description`
- `XMP:Subject`

### `XMP:Subject` contains:
- normal keywords
- prefixed structured tags such as:
  - `subject:dog`
  - `scene:landscape`
  - `people:2`
  - `faces:yes`
  - `setting:outdoor`
  - `time:sunset`
  - `lighting:golden-hour`
  - `weather:fog`
  - `season:autumn`
  - `orientation:portrait`

This makes filtering/searching in DAM tools easier.

---

## XMP sidecars

For each image, the script creates a sidecar file like:

- `IMG_1234.JPG`
- `IMG_1234.xmp`

The sidecar stores metadata separately from the original image.

Advantages:
- original files remain untouched
- good compatibility with DAM tools
- useful for RAW workflows
- portable across software

---

## Owner, group and permissions

The script can set ownership and permissions on generated XMP files.

Defaults:

- owner: `vol.pictures`
- group: `vol.pictures`
- mode: `660`

These can be changed via CLI:

```bash
python3 main.py --src /path/to/photos --xmp-owner myuser --xmp-group mygroup --xmp-mode 664
```

This is especially useful when running via cron as `root`.

---

## Cron mode

For cron jobs, use:

```bash
python3 main.py --src /path/to/photos --no-progress
```

This disables the progress bar and prints one compact line per file.

Example output:

```text
[2026-05-04 14:45:19] OK    /photos/IMG_0993.HEIC -> /photos/IMG_0993.xmp
[2026-05-04 14:45:20] SKIP  /photos/IMG_0997.HEIC -> /photos/IMG_0997.xmp
[2026-05-04 14:45:21] ERR   /photos/IMG_0999.CR2 -> /photos/IMG_0999.xmp :: Unreadable image
[2026-05-04 14:45:22] DONE  processed=10 skipped=5 failed=1
```

This format is ideal for cron log files.

---

## Wrapper script

A wrapper script can activate the local virtual environment and forward all arguments.

Example `photoxmp.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Virtual environment not found: ${VENV_DIR}" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

exec python3 "${SCRIPT_DIR}/main.py" "$@"
```

Make it executable:

```bash
chmod +x photoxmp.sh
```

Then use it like:

```bash
./photoxmp.sh --src /path/to/photos --no-progress
```

---

## Example cron job

```cron
15 * * * * /path/to/photoxmp.sh --src /volume2/Pictures --no-progress >> /var/log/photoxmp.log 2>&1
```

This runs every hour at minute 15.

---

## Recommended workflow

### First pass over a large archive
Use speed-focused defaults:

```bash
./photoxmp.sh --src /path/to/archive --no-progress
```

### Re-run selected folders later
Use a larger resize value and force reprocessing:

```bash
./photoxmp.sh --src /path/to/best-photos --resize-max 1344 --force
```

### Interactive testing
Use verbose mode:

```bash
./photoxmp.sh --src /path/to/test-folder -v
```

or:

```bash
./photoxmp.sh --src /path/to/test-folder -vv
```

---

## digiKam notes

This script is a good fit for digiKam-based workflows.

Recommended:
- enable reading XMP sidecars
- keep image and `.xmp` files together
- let digiKam rescan or refresh metadata after generation

If moving or renaming files manually:
- always keep the `.xmp` sidecar with the image

---

## RAW / HEIC notes

### CR2
CR2 support uses `rawpy`.

If CR2 processing fails, check that `rawpy` is installed:

```bash
pip install rawpy
```

### HEIC
HEIC support uses `pillow-heif`.

If HEIC processing fails, check that `pillow-heif` is installed:

```bash
pip install pillow-heif
```

---

## Troubleshooting

### exiftool missing

Install `exiftool`.

Debian / Ubuntu:
```bash
sudo apt install libimage-exiftool-perl
```

macOS:
```bash
brew install exiftool
```

### CR2 files fail
Install:

```bash
pip install rawpy
```

### HEIC files fail
Install:

```bash
pip install pillow-heif
```

### Model returns invalid JSON
Try:
- rerunning the file
- using `-vv` to inspect raw output
- switching model version
- using a stricter custom prompt via `--prompt-file`

### XMP files owned by root
Use:
- `--xmp-owner`
- `--xmp-group`
- `--xmp-mode`

or keep the default values if they match your setup.

---

## Freeze dependencies

Once your environment works well, it is a good idea to freeze it:

```bash
source .venv/bin/activate
pip freeze > requirements.txt
```

This makes future rebuilds easier.

---

## Example command lines

### Large archive, cron-friendly
```bash
./photoxmp.sh --src /volume2/Pictures --no-progress
```

### Single RAW file
```bash
./photoxmp.sh --src /volume2/Pictures/IMG_1234.CR2
```

### Re-run selected folder with more detail
```bash
./photoxmp.sh --src /volume2/Pictures/BestOf --resize-max 1344 --force
```

### Remote Ollama
```bash
./photoxmp.sh --src /volume2/Pictures --ollama-url http://192,168.17:11434 --model qwen3-vl:32b
```

---

## Notes

This tool is intentionally practical and speed-focused.

For big historical archives, it is usually better to:
1. process everything quickly with good defaults
2. reprocess selected folders later with higher detail or improved prompts

That gives much better throughput than trying to make the first pass perfect.

---

## License

Use and adapt freely for your own workflow.
```

If you want, I can also create:
1. a **shorter README** version, or  
2. a matching **requirements.txt** based on the script.

