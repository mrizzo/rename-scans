# rename-scans

Renames freshly scanned PDFs by sending each one to Claude and asking what it
is. Built for a household in Japan: it reads Japanese receipts, bills and
notices and writes romaji/English filenames.

| Document | New name | Example |
|---|---|---|
| Medical / pharmacy receipt (領収証) | `YYYYMMDD_HHMMSS_<issuer>_<yen>.pdf` | `20260905_104747_sakura_naika_clinic_1380.pdf` |
| Bill, invoice or payment notice | `YYYYMMDD_HHMMSS_<description>_<yen>.pdf` | `20260917_220249_tokyo_suidokyoku_water_sewage_bill_1280.pdf` |
| Anything else | `YYYYMMDD_HHMMSS_<description>.pdf` | `20260909_084821_sample_mansion_garbage_area_notice.pdf` |

Each medical receipt also gets a row added to `to-paste.tsv` next to the scans,
ready to paste into a spreadsheet (for example, for a medical-expense
deduction):

```
09/16/2026	さくら内科クリニック	29210
```

The date is the one printed on the receipt, not the scan date, so receipts
scanned late still land in the right month.

The timestamp comes from the scanner's filename (by default
`YYYY_MM_DD_HH_MM_SS.pdf`), so files sort by when they were scanned.

It is careful by design:

- Only files that still have the scanner's raw name are touched, so renamed
  files are never processed twice.
- Files are renamed in place and nothing is ever overwritten; a clash gets
  `_copy2`, `_copy3`, ...
- Files modified in the last minute are skipped (still being written).
- A failed or refused API call leaves the file alone; it is retried next run.
- `--dry-run` shows what would happen without renaming anything.

## What you need

- Python 3.11 or newer (macOS: `brew install python`). Apple's built-in
  `/usr/bin/python3` is too old.
- An Anthropic API key from <https://console.anthropic.com> (Settings → API
  Keys). The API is billed per use, separately from a Claude.ai subscription;
  add a few dollars of credit, which lasts a long time at this volume.

## Cost

Each scan is one API call. A one-page document is about 3,000 input and 80
output tokens on `claude-opus-5-5`, roughly **1.5¢**; each extra page adds
about a cent. Thirty scans a month comes to around $0.50. Each log line shows
the tokens used, so you can check real numbers. Setting
`model = "claude-sonnet-5-5"` in the config roughly halves the cost.

## Setup

1. **Get the code and install the SDK**

   ```sh
   git clone https://github.com/mrizzo/rename-scans.git ~/rename-scans
   python3 -m pip install --user --break-system-packages anthropic
   ```

   (Or install `anthropic` into a venv and run the script with that venv's
   `python3`.)

2. **Save your API key** where only you can read it. On macOS, copy the key,
   then:

   ```sh
   mkdir -p ~/.config/rename-scans
   pbpaste > ~/.config/rename-scans/api_key
   chmod 600 ~/.config/rename-scans/api_key
   ```

   Alternatively set `ANTHROPIC_API_KEY` in the environment; it takes
   precedence over the file.

3. **Point it at your scans folder**

   ```sh
   cp ~/rename-scans/config.example.toml ~/.config/rename-scans/config.toml
   ```

   Edit `folders` in that file. `{year}` is replaced by the current year, so
   `".../{year}-scans"` follows you into next year. If your scanner names files
   differently, set `raw_format` too (see [Settings](#settings)).

4. **Try it** without renaming anything:

   ```sh
   python3 ~/rename-scans/rename-scans.py --dry-run
   ```

   You should see one `would rename ... -> ...` line per unrenamed scan. No
   output means it found no files matching `raw_format`.

5. **Run it for real** once you like the names: the same command without
   `--dry-run`.

## Running it automatically (cron)

`crontab -e` and add a line like this to run every 15 minutes. Use the full
path to your Python, since cron's `PATH` is minimal (`which python3` shows it;
Homebrew on Apple Silicon is `/opt/homebrew/bin/python3`):

```
*/15 * * * * /opt/homebrew/bin/python3 /Users/YOU/rename-scans/rename-scans.py >> /Users/YOU/rename-scans.log 2>&1
```

A lock stops two runs overlapping, and runs with nothing to do print nothing,
so the log only grows when something happens.

## Google Drive on macOS

If the scans folder is inside Google Drive (`~/Library/CloudStorage/...`), two
macOS details matter. Both are one-time steps.

- **Allow Python to read Google Drive.** The first time Python reads a Drive
  file, macOS asks whether `python3` may access files in Google Drive. A cron
  job can't show that prompt, so its reads just fail. Run the script once by
  hand from Terminal (step 4) and click **Allow**. If a Homebrew upgrade
  installs a new Python version you may be asked again; until you allow it,
  the log shows `failed ... will retry next run`.
- **Make the scans folder available offline.** In Finder, right-click the
  folder → *Offline access* → *Available offline*. Drive then downloads new
  files as they arrive instead of leaving online-only placeholders.

If your scanner uploads into the top of My Drive, keep that top level online
only and move new scans into the offline folder first. Moving a file within
Drive is just a rename and works from cron. For example, a `move-scans.sh`:

```sh
#!/bin/bash
# Move raw scans from the top of My Drive into <year>-scans.
BASE="$HOME/Library/CloudStorage/GoogleDrive-YOU@gmail.com/My Drive"
shopt -s nullglob
for f in "$BASE"/[0-9][0-9][0-9][0-9]_[0-9][0-9]_[0-9][0-9]_[0-9][0-9]_[0-9][0-9]_[0-9][0-9].pdf; do
  name=$(basename "$f")
  mkdir -p "$BASE/${name:0:4}-scans"
  mv "$f" "$BASE/${name:0:4}-scans/" && echo "Moved $name"
done
```

and run both from one cron line, so a scan is moved and renamed in the same
run:

```
*/15 * * * * /bin/bash /Users/YOU/bin/move-scans.sh >> /Users/YOU/move-scans.log 2>&1; /opt/homebrew/bin/python3 /Users/YOU/rename-scans/rename-scans.py >> /Users/YOU/rename-scans.log 2>&1
```

## Settings

All optional except `folders`; see `config.example.toml`.

| Setting | Default | Meaning |
|---|---|---|
| `folders` | (none) | Folders to look in. `{year}` = current year, `~` is expanded. |
| `raw_format` | `%Y_%m_%d_%H_%M_%S.pdf` | How the scanner names files (strftime codes). |
| `name_format` | `%Y%m%d_%H%M%S_{name}.pdf` | What to rename to. `{name}` is Claude's description. |
| `api_key_file` | `~/.config/rename-scans/api_key` | Where the key is read from. |
| `model` | `claude-opus-5-5` | Claude model to use. |
| `min_age_secs` | `60` | Skip files modified more recently than this. |
| `lock_dir` | `~/.rename-scans.lock` | Prevents overlapping runs. |
| `paste_file` | `to-paste.tsv` | Where receipt rows go. Relative paths are next to the scans; `""` turns it off. |
| `paste_date_format` | `%m/%d/%Y` | Date format in those rows. |

Command line:

```
rename-scans.py [--config FILE] [--dry-run] [FOLDER ...]
```

Folders given on the command line replace the ones in the config.

To change what counts as a receipt or how descriptions are worded, edit
`PROMPT` near the top of `rename-scans.py`.

## Troubleshooting

| Log says | Fix |
|---|---|
| `No API key: ...` | Step 2. |
| `no folders: ...` | Set `folders` in the config (step 3). |
| `skip ...: not a directory` | A folder in the config doesn't exist; check the path and year. |
| `failed ... will retry next run` on a Drive file | Run the script once from Terminal and allow Python to access Google Drive. |
| `failed ... authentication_error` | The API key is wrong or revoked. |
| `failed ... credit balance is too low` | Add credit in the Anthropic console. |
| `another run holds ...lock; exiting` every run | A run was killed mid-way; delete the lock directory. |
| Nothing at all | No files match `raw_format`, or they were modified in the last minute. |
