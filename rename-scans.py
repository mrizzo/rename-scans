#!/opt/homebrew/bin/python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["anthropic>=1.11"]
# ///
"""Rename freshly scanned PDFs using Claude.

Finds raw scanner files (by default YYYY_MM_DD_HH_MM_SS.pdf) in the given folders,
sends each PDF to the Anthropic API, and renames it in place to match the
existing convention:

  medical / pharmacy receipt -> YYYYMMDD_HHMMSS_<issuer_romaji>_<yen>.pdf
                                 e.g. 20260905_104747_sakura_naika_clinic_1380.pdf
  bill / invoice / statement -> YYYYMMDD_HHMMSS_<short_description>_<yen>.pdf
  anything else              -> YYYYMMDD_HHMMSS_<short_description>.pdf
                                 e.g. 20260909_084821_sample_mansion_temporary_garbage_area_notice.pdf

Safe by design:
  - only touches files that still have the raw scanner name
  - skips files modified in the last min_age_secs (file still syncing)
  - never overwrites: on a name clash it appends _copy2, _copy3, ...
  - a failed or refused API call leaves the file untouched for the next run
  - --dry-run prints what it would do and renames nothing
  - a lock directory stops overlapping cron runs

Configuration lives in ~/.config/rename-scans/config.toml (or --config):

  folders = ["/path/to/scans", "/another/folder"]   # required unless given on the command line
  api_key_file = "~/.config/rename-scans/api_key"   # optional; ANTHROPIC_API_KEY wins if set
  model = "claude-opus-5-5"                         # optional
  min_age_secs = 60                                 # optional
  lock_dir = "~/.rename-scans.lock"                 # optional
  raw_format = "%Y_%m_%d_%H_%M_%S.pdf"              # optional; how the scanner names files
  name_format = "%Y%m%d_%H%M%S_{name}.pdf"          # optional; what to rename them to
  paste_file = "to-paste.tsv"                       # optional; "" turns it off
  paste_date_format = "%m/%d/%Y"                    # optional
  issuers_file = "~/.config/rename-scans/issuers.tsv"  # optional; "" turns it off
  download_wait_secs = 120                          # optional; 0 turns off waiting

A folder may contain {year}, replaced by the current year. raw_format and
name_format use strftime codes; the timestamp parsed from the raw name fills
name_format, and {name} is the description Claude produced. Only files whose
name parses with raw_format are touched.

Each medical receipt also gets a row appended to paste_file (date printed on the
receipt, issuer as printed, amount), ready to paste into a spreadsheet. A
relative paste_file lives next to the scans.

Usage:
  rename-scans.py [--config FILE] [--dry-run] [FOLDER ...]
"""

import argparse
import base64
import datetime
import errno
import os
import re
import sys
import time
import tomllib
from pathlib import Path
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field

CONFIG_FILE = Path.home() / ".config/rename-scans/config.toml"
DEFAULTS = {
    "folders": [],
    "api_key_file": "~/.config/rename-scans/api_key",
    "model": "claude-opus-5-5",
    "min_age_secs": 60,
    "lock_dir": "~/.rename-scans.lock",
    "raw_format": "%Y_%m_%d_%H_%M_%S.pdf",
    "name_format": "%Y%m%d_%H%M%S_{name}.pdf",
    "paste_file": "to-paste.tsv",
    "paste_date_format": "%m/%d/%Y",
    "issuers_file": "~/.config/rename-scans/issuers.tsv",
    "download_wait_secs": 120,
}
MAX_PDF_BYTES = 24 * 1024 * 1024  # API request limit is 32 MB, base64 adds a third

PROMPT = """This is a scanned document from a household in Japan. Classify it and \
produce a short filename description.

doc_type:
- "medical_receipt": a 領収証/領収書 issued by a clinic, hospital, dentist or \
pharmacy (薬局, 調剤). Appointment cards, test results, prescriptions without a \
payment, and vaccination forms are NOT receipts.
- "other": everything else.

For a medical_receipt:
- issuer_kanji: the 発行者 (clinic/hospital/pharmacy name) exactly as printed.
- issuer_romaji: that name in lowercase Hepburn romaji, words separated by \
underscores, no corporate prefixes like 医療法人/株式会社 \
(e.g. さくら内科クリニック -> sakura_naika_clinic, \
ひまわり薬局 中央店 -> himawari_yakkyoku_chuo).
- amount_yen: the total paid, digits only, from 領収金額 or 合計 \
(the amount the patient paid, not 保険点数 or the pre-insurance total).
- receipt_date: the date printed on the receipt as YYYY-MM-DD: the 領収日 or \
発行日 if shown, otherwise the 診療日 (date of treatment). Convert Japanese era \
years (令和8年 = 2026).

For other documents:
- description: 3-8 lowercase words separated by underscores, issuer first, \
then what it is; romaji for Japanese names, English for the document type, and \
the person's first name if it is about one specific person \
(e.g. sample_mansion_holiday_work_notice, \
chuo_ku_kodomo_influenza_vaccine_yoshinhyo_taro).
- amount_yen: only for a bill, invoice, payment notice or receipt with a \
single total to pay or paid (e.g. 請求金額, ご請求額, お支払い金額, 合計): that total \
in yen, digits only. Null for everything else, including statements of points \
or balances. If the total is in another currency, leave amount_yen null and end \
the description with the amount and currency (e.g. _120usd).

Leave fields that don't apply as null."""


class ScanInfo(BaseModel):
    doc_type: Literal["medical_receipt", "other"]
    issuer_kanji: Optional[str] = Field(description="発行者 as printed, receipts only")
    issuer_romaji: Optional[str] = Field(description="romaji_with_underscores, receipts only")
    amount_yen: Optional[int] = Field(description="total paid or due in yen, receipts and bills only")
    description: Optional[str] = Field(description="short_description_with_underscores, non-receipts only")
    receipt_date: Optional[str] = Field(description="date printed on the receipt, YYYY-MM-DD, receipts only")


def log(msg):
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def slug(text):
    """Lowercase a-z0-9 words joined by underscores, at most 80 chars."""
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:80].rstrip("_")


def load_config(path, explicit):
    config = dict(DEFAULTS)
    if path.exists():
        with open(path, "rb") as f:
            config.update(tomllib.load(f))
    elif explicit:
        sys.exit(f"config file not found: {path}")
    return config


def expand(path_str):
    return Path(os.path.expanduser(path_str.replace("{year}", str(datetime.date.today().year))))


def load_api_key(key_file):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key and key_file.exists():
        key = key_file.read_text().splitlines()[0].strip()
    if not key:
        sys.exit(f"No API key: set ANTHROPIC_API_KEY or put it in {key_file}")
    return key


def classify(client, model, pdf_bytes):
    response = client.beta.messages.parse(
        model=model,
        max_tokens=2048,
        output_config={"effort": "low"},
        # Retry on another model if a safety classifier declines.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(pdf_bytes).decode(),
                }},
                {"type": "text", "text": PROMPT},
            ],
        }],
        output_format=ScanInfo,
    )
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        raise RuntimeError(f"stop_reason={response.stop_reason}")
    info = response.parsed_output
    if info.issuer_kanji:
        info.issuer_kanji = " ".join(info.issuer_kanji.split())  # no tabs or newlines in logs or TSV cells
    return info, response.usage


def scan_time(filename, raw_format):
    """The timestamp in a raw scanner filename, or None if it isn't one."""
    if not filename.lower().endswith(".pdf"):
        return None
    try:
        return datetime.datetime.strptime(filename, raw_format)
    except ValueError:
        return None


def new_name(path, info, raw_format, name_format):
    stamp = scan_time(path.name, raw_format)
    template = stamp.strftime(name_format)  # {name} passes through strftime untouched
    if info.doc_type == "medical_receipt" and info.issuer_romaji and info.amount_yen is not None:
        desc = f"{slug(info.issuer_romaji)}_{info.amount_yen}"
    else:
        desc = slug(info.description) or slug(info.issuer_romaji) or "unknown_document"
        if info.amount_yen is not None:
            desc = f"{desc}_{info.amount_yen}"
    name = template.replace("{name}", desc)
    n = 2
    while (path.parent / name).exists():
        name = template.replace("{name}", f"{desc}_copy{n}")
        n += 1
    return name


def load_issuers(path):
    """kanji -> romaji pairs from a tab-separated file; '#' lines are comments."""
    issuers = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "\t" not in line:
                continue
            kanji, romaji = line.split("\t", 1)
            kanji, romaji = " ".join(kanji.split()), slug(romaji)
            if kanji and romaji:
                issuers[kanji] = romaji
    return issuers


def read_pdf(path, wait_secs, step=20):
    """Read a PDF, waiting while a cloud-synced copy is still downloading.

    Reading a Google Drive placeholder that is mid-download fails on macOS with
    EDEADLK ("Resource deadlock avoided"). Retry for up to wait_secs before
    giving up, so a scan moved into the folder moments ago is still renamed in
    this run instead of the next one.
    """
    waited = 0
    while True:
        try:
            return path.read_bytes()
        except OSError as e:
            if e.errno != errno.EDEADLK or waited >= wait_secs:
                raise
            if waited == 0:
                log(f"waiting for {path.name} to finish downloading")
            time.sleep(step)
            waited += step


def paste_row(path, info, raw_format, date_format):
    """A tab-separated date / issuer / amount row for a medical receipt, or None."""
    if info.doc_type != "medical_receipt" or not info.issuer_kanji or info.amount_yen is None:
        return None
    try:
        date = datetime.date.fromisoformat(info.receipt_date or "")
    except ValueError:
        log(f"note {path.name}: no readable date on the receipt, using the scan date")
        date = scan_time(path.name, raw_format).date()
    return f"{date.strftime(date_format)}\t{info.issuer_kanji}\t{info.amount_yen}"


def append_row(paste_file, row):
    """Append one line, adding a newline first if the file doesn't end with one."""
    needs_newline = paste_file.exists() and paste_file.stat().st_size > 0 and not paste_file.read_bytes().endswith(b"\n")
    with open(paste_file, "a", encoding="utf-8") as f:
        f.write(("\n" if needs_newline else "") + row + "\n")


def raw_scans(folder, min_age_secs, raw_format):
    if not folder.is_dir():
        log(f"skip {folder}: not a directory")
        return
    now = time.time()
    for path in sorted(folder.iterdir()):
        if not path.is_file() or scan_time(path.name, raw_format) is None:
            continue
        age = now - path.stat().st_mtime
        if age < min_age_secs:
            log(f"skip {path.name}: modified {age:.0f}s ago, still settling")
            continue
        yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folders", nargs="*", help="overrides folders from the config")
    parser.add_argument("--config", type=Path, help=f"default {CONFIG_FILE}")
    parser.add_argument("--dry-run", action="store_true", help="classify but don't rename")
    args = parser.parse_args()
    config = load_config(args.config or CONFIG_FILE, explicit=args.config is not None)
    folders = [expand(f) for f in (args.folders or config["folders"])]
    if not folders:
        sys.exit(f"no folders: list them in {args.config or CONFIG_FILE} or on the command line")
    lockdir = expand(config["lock_dir"])
    if "{name}" not in config["name_format"]:
        sys.exit("name_format must contain {name}")

    try:
        lockdir.mkdir()
    except FileExistsError:
        log(f"another run holds {lockdir}; exiting")
        return 0

    try:
        scans = [p for folder in folders for p in raw_scans(folder, config["min_age_secs"], config["raw_format"])]
        if not scans:
            return 0
        client = anthropic.Anthropic(api_key=load_api_key(expand(config["api_key_file"])))
        issuers_file = expand(config["issuers_file"]) if config["issuers_file"] else None
        issuers = load_issuers(issuers_file) if issuers_file else {}
        failures = 0
        for path in scans:
            try:
                pdf = read_pdf(path, config["download_wait_secs"])
                if len(pdf) > MAX_PDF_BYTES:
                    log(f"skip {path.name}: {len(pdf) // 2**20} MB is too large for one request")
                    continue
                info, usage = classify(client, config["model"], pdf)
                # Reuse the romaji chosen the first time this issuer was seen, so a
                # clinic keeps one spelling across runs; the model's own romaji varies.
                new_issuer = None
                if info.doc_type == "medical_receipt" and info.issuer_kanji and issuers_file:
                    if info.issuer_kanji in issuers:
                        info.issuer_romaji = issuers[info.issuer_kanji]
                    elif slug(info.issuer_romaji):
                        new_issuer = (info.issuer_kanji, slug(info.issuer_romaji))
                name = new_name(path, info, config["raw_format"], config["name_format"])
                extra = " ".join(x for x in (info.issuer_kanji, info.amount_yen is not None and f"¥{info.amount_yen}") if x)
                extra = f" [{extra}]" if extra else ""
                tokens = f"({usage.input_tokens} in / {usage.output_tokens} out)"
                row = paste_row(path, info, config["raw_format"], config["paste_date_format"])
                paste_file = path.parent / expand(config["paste_file"]) if config["paste_file"] else None
                if args.dry_run:
                    log(f"would rename {path.name} -> {name}{extra} {tokens}")
                    if row and paste_file:
                        log(f"would add to {paste_file.name}: {row.replace(chr(9), ' | ')}")
                    if new_issuer:
                        issuers[new_issuer[0]] = new_issuer[1]  # in memory only, so the preview matches a real run
                        log(f"would remember {new_issuer[0]} -> {new_issuer[1]} in {issuers_file.name}")
                else:
                    path.rename(path.parent / name)
                    log(f"renamed {path.name} -> {name}{extra} {tokens}")
                    if row and paste_file:
                        append_row(paste_file, row)
                        log(f"added to {paste_file.name}: {row.replace(chr(9), ' | ')}")
                    if new_issuer:
                        issuers_file.parent.mkdir(parents=True, exist_ok=True)
                        append_row(issuers_file, "\t".join(new_issuer))
                        issuers[new_issuer[0]] = new_issuer[1]
                        log(f"remembered {new_issuer[0]} -> {new_issuer[1]} in {issuers_file.name}")
            except anthropic.APIConnectionError as e:
                log(f"network error, stopping this run: {e}")
                return 1
            except (anthropic.APIStatusError, RuntimeError, OSError) as e:
                failures += 1
                log(f"failed {path.name}, will retry next run: {e}")
        return 1 if failures else 0
    finally:
        lockdir.rmdir()


if __name__ == "__main__":
    sys.exit(main())
