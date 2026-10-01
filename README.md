# rename-scans

Renames freshly scanned PDFs (`YYYY_MM_DD_HH_MM_SS.pdf`, as a ScanSnap-style
scanner names them) by sending each one to Claude and asking what it is.

| Document | New name |
|---|---|
| Medical / pharmacy receipt (領収証) | `YYYYMMDD_HHMMSS_<issuer_romaji>_<yen>.pdf` |
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

Folders on the command line override the config. Uses `claude-opus-5-5` at low
effort; each scan costs roughly a cent.
