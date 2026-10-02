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

## Why this exists

For decades in the US I had an FSA, and I always maxed it out. Every so often
the administrator asks for the receipt behind a charge to prove it was a valid
expense. My method was simple: scan the receipt the same day I used the FSA
card, and when they asked, go back to that day and find it.

That was just one of many times I had to *find* a file. Scanning stopped being
the hard part around 2009, when Dropbox and scanners that saved straight to a
folder meant I didn't even have to unlock a computer. Renaming was the hard
part. Opening each file, working out what it was and typing a sensible name was
too much friction, so it didn't happen, and I ended up with years of folders
full of timestamps. When the paperwork is in Japanese, multiply that by ten.

When Claude Cowork came along, I had it open each new scan, work out what it
is, and give it a consistent, sensible name. When I explained this to someone,
I said "use Cowork, my use case is different from yours", and it still is.
Mine: in Japan, medical expenses over ¥100,000 a year are tax deductible
(医療費控除), I always go over that, and Japan is paperwork heavy. I even avoid
going to new pharmacies or doctors, because each one adds another line to my
spreadsheet and tax records. That is why receipts get the issuer and amount in
the filename, and a row in `to-paste.tsv`.

This script does the same job as that Cowork task, but on your own Anthropic
API key instead of a Claude subscription. It's heavier to set up, but it runs
anywhere and you can hand it to someone else. Modify it as needed: the prompt
near the top of `rename-scans.py` is where your own rules go.

## What you need

- One way to run Python scripts with their dependencies. Any of these works:
  - **[uv](https://docs.astral.sh/uv/)** (recommended; `brew install uv`). It
    reads the dependency list at the top of the script, builds a private,
    cached environment for it, and can fetch a suitable Python itself.
  - **pipx** 1.4 or newer (`brew install pipx`), which reads the same list.
  - Python 3.11 or newer with `pip`. Apple's built-in `/usr/bin/python3` is too
    old; on macOS use `brew install python`.
- An Anthropic API key from <https://console.anthropic.com> (Settings → API
  Keys). The API is billed per use, separately from a Claude.ai subscription;
  add a few dollars of credit, which lasts a long time at this volume.

## Cost

You pay per scan, on your API key:

| | Tokens | Cost on `claude-opus-5-5` |
|---|---|---|
| A one-page scan | ~3,300 in, ~100 out | about **1.5¢** |
| Each extra page | ~1,500–3,000 more in | about 1¢ more |
| A run with nothing new to rename | none (no API call) | **free** |

The receipt date used for `to-paste.tsv` is always requested, even with
`paste_file = ""`. It adds about 190 input and 20 output tokens, roughly
$0.001 a scan.

At about 24 scans a month (my average) that's around **$0.50 a month**, or
$6–9 a year. Each log line shows the tokens used, so you can check your own
numbers. Setting `model = "claude-sonnet-5-5"` in the config roughly halves the
cost.

**Compared with Cowork:** the daily Cowork task I used before started a full
agent every run. Its local logs show roughly half a million tokens a day even
when there was nothing to rename, and 1–3 million on a busy day, about
$1–3 a day at API prices. It doesn't cost that in money, because a Claude
subscription covers it within its usage limits. So if you already have a
subscription and only need this for yourself, Cowork may well be the simpler
choice. The script is for when you want it portable, cheap per use, or running
for someone without a subscription.

## Setup

1. **Get the code**

   ```sh
   git clone https://github.com/mrizzo/rename-scans.git ~/rename-scans
   ```

   The script declares its own dependencies in a comment block at the top
   ([PEP 723](https://peps.python.org/pep-0723/)), so with uv or pipx there is
   nothing to install and no venv to manage. The first run downloads the
   Anthropic SDK into a cache; later runs reuse it.

   | How you run it | Command (`RS=~/rename-scans/rename-scans.py`) |
   |---|---|
   | uv | `uv run --script $RS` |
   | pipx | `pipx run $RS` |
   | plain Python | `python3 -m pip install --user --break-system-packages anthropic`, then `python3 $RS` |

   The plain-Python route installs the SDK into your user site-packages. It
   works, but a Python upgrade (3.14 → 3.15) leaves the SDK behind and you'll
   need to run the install again. The steps below use uv; swap in your
   runner.

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
   uv run --script ~/rename-scans/rename-scans.py --dry-run
   ```

   You should see one `would rename ... -> ...` line per unrenamed scan. No
   output means it found no files matching `raw_format`.

5. **Run it for real** once you like the names: the same command without
   `--dry-run`.

## Running it automatically (cron)

`crontab -e` and add a line like this to run every 15 minutes. Use full paths,
since cron's `PATH` is minimal (`which uv`, `which pipx` or `which python3`
shows yours; Homebrew on Apple Silicon puts them in `/opt/homebrew/bin`):

```
# uv
*/15 * * * * /opt/homebrew/bin/uv run --script /Users/YOU/rename-scans/rename-scans.py >> /Users/YOU/rename-scans.log 2>&1
# or pipx
*/15 * * * * /opt/homebrew/bin/pipx run /Users/YOU/rename-scans/rename-scans.py >> /Users/YOU/rename-scans.log 2>&1
# or plain Python with the SDK installed
*/15 * * * * /opt/homebrew/bin/python3 /Users/YOU/rename-scans/rename-scans.py >> /Users/YOU/rename-scans.log 2>&1
```

A lock stops two runs overlapping, and runs with nothing to do print nothing,
so the log only grows when something happens.

## Google Drive on macOS

If the scans folder is inside Google Drive (`~/Library/CloudStorage/...`), two
macOS details matter. Both are one-time steps.

- **Allow your runner to read Google Drive.** The first time the cron job
  reads a Drive file, macOS asks whether the program that started it may
  access files in Google Drive. The dialog names that program: **uv**,
  **pipx** or **python3**, depending on your cron line. Until someone clicks
  **Allow**, the read just waits: the run never finishes, the log stays
  quiet, and later runs log `another run holds ...lock; exiting`. Running the
  script from Terminal doesn't settle it, because there Terminal's own
  permission applies. So be at the Mac for the first cron run that has a scan
  to read, and click **Allow** when the dialog appears. Switching runners, or
  an upgrade that installs a new Python, can bring the dialog back.
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
*/15 * * * * /bin/bash /Users/YOU/bin/move-scans.sh >> /Users/YOU/move-scans.log 2>&1; /opt/homebrew/bin/uv run --script /Users/YOU/rename-scans/rename-scans.py >> /Users/YOU/rename-scans.log 2>&1
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
| A run that never finishes, then `another run holds ...lock; exiting` | macOS is waiting for you to allow Google Drive access. Look for the dialog naming uv, pipx or python3 and click **Allow**. |
| `failed ... will retry next run` on a Drive file | Google Drive access was denied. Allow it in System Settings → Privacy & Security, then wait for the next run. |
| `failed ... authentication_error` | The API key is wrong or revoked. |
| `failed ... credit balance is too low` | Add credit in the Anthropic console. |
| `another run holds ...lock; exiting` every run | A run was killed mid-way; delete the lock directory. |
| Nothing at all | No files match `raw_format`, or they were modified in the last minute. |
