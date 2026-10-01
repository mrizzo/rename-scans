# rename-scans

Renames freshly scanned PDFs (by default `YYYY_MM_DD_HH_MM_SS.pdf`) by sending
each one to Claude and asking what it is.

| Document | New name |
|---|---|
| Medical / pharmacy receipt (領収証) | `YYYYMMDD_HHMMSS_<issuer_romaji>_<yen>.pdf` |
| Bill, invoice or payment notice | `YYYYMMDD_HHMMSS_<short_description>_<yen>.pdf` |
| Anything else | `YYYYMMDD_HHMMSS_<short_description>.pdf` |

Files are renamed in place. Nothing is overwritten (clashes get `_copy2`,
`_copy3`, ...), files modified in the last minute are left alone, and a
failed or refused API call leaves the file for the next run.

## Setup

```sh
python3 -m pip install --user --break-system-packages anthropic   # or use a venv
mkdir -p ~/.config/rename-scans
cp config.example.toml ~/.config/rename-scans/config.toml          # edit folders
pbpaste > ~/.config/rename-scans/api_key && chmod 600 ~/.config/rename-scans/api_key
./rename-scans.py --dry-run
```

`ANTHROPIC_API_KEY` in the environment takes precedence over the key file.

## Cron

```
*/15 * * * * /path/to/rename-scans/rename-scans.py >> ~/rename-scans.log 2>&1
```

On macOS, cron needs Full Disk Access to read folders under
`~/Library/CloudStorage`.

## Options

```
rename-scans.py [--config FILE] [--dry-run] [FOLDER ...]
```

Folders on the command line override the config.

Both the scanner's naming and the new naming are configurable with strftime
codes. The timestamp parsed from `raw_format` fills `name_format`, and `{name}`
is the description Claude produced:

```toml
raw_format = "%Y_%m_%d_%H_%M_%S.pdf"       # default
name_format = "%Y%m%d_%H%M%S_{name}.pdf"   # default
```

Only files whose names parse with `raw_format` are touched, so already-renamed
files are never picked up again. Uses `claude-opus-5-5` at low
effort; each scan costs roughly a cent.
