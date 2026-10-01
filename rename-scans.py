#!/opt/homebrew/bin/python3
"""Rename freshly scanned PDFs using Claude.

Finds raw scanner files (YYYY_MM_DD_HH_MM_SS.pdf) in the given folders,
sends each PDF to the Anthropic API, and renames it in place to match the
existing convention:

  medical / pharmacy receipt -> YYYYMMDD_HHMMSS_<issuer_romaji>_<yen>.pdf
                                 e.g. 20260905_104747_sakura_naika_clinic_1380.pdf
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

A folder may contain {year}, replaced by the current year.

Usage:
  rename-scans.py [--config FILE] [--dry-run] [FOLDER ...]
"""

import argparse
import base64
import datetime
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
}
RAW_NAME = re.compile(r"^(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})\.pdf$")
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

For other documents:
- description: 3-8 lowercase words separated by underscores, issuer first, \
then what it is; romaji for Japanese names, English for the document type, and \
the person's first name if it is about one specific person \
(e.g. sample_mansion_holiday_work_notice, \
chuo_ku_kodomo_influenza_vaccine_yoshinhyo_taro).

Leave fields that don't apply as null."""


class ScanInfo(BaseModel):
    doc_type: Literal["medical_receipt", "other"]
    issuer_kanji: Optional[str] = Field(description="発行者 as printed, receipts only")
    issuer_romaji: Optional[str] = Field(description="romaji_with_underscores, receipts only")
    amount_yen: Optional[int] = Field(description="total paid in yen, receipts only")
    description: Optional[str] = Field(description="short_description_with_underscores, non-receipts only")


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
    return response.parsed_output, response.usage


def new_name(path, info):
    m = RAW_NAME.match(path.name)
    stamp = "{}{}{}_{}{}{}".format(*m.groups())
    if info.doc_type == "medical_receipt" and info.issuer_romaji and info.amount_yen is not None:
        desc = f"{slug(info.issuer_romaji)}_{info.amount_yen}"
    else:
        desc = slug(info.description) or slug(info.issuer_romaji) or "unknown_document"
    name = f"{stamp}_{desc}.pdf"
    n = 2
    while (path.parent / name).exists():
        name = f"{stamp}_{desc}_copy{n}.pdf"
        n += 1
    return name


def raw_scans(folder, min_age_secs):
    if not folder.is_dir():
        log(f"skip {folder}: not a directory")
        return
    now = time.time()
    for path in sorted(folder.iterdir()):
        if not RAW_NAME.match(path.name):
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

    try:
        lockdir.mkdir()
    except FileExistsError:
        log(f"another run holds {lockdir}; exiting")
        return 0

    try:
        scans = [p for folder in folders for p in raw_scans(folder, config["min_age_secs"])]
        if not scans:
            return 0
        client = anthropic.Anthropic(api_key=load_api_key(expand(config["api_key_file"])))
        failures = 0
        for path in scans:
            try:
                pdf = path.read_bytes()
                if len(pdf) > MAX_PDF_BYTES:
                    log(f"skip {path.name}: {len(pdf) // 2**20} MB is too large for one request")
                    continue
                info, usage = classify(client, config["model"], pdf)
                name = new_name(path, info)
                extra = f" [{info.issuer_kanji} ¥{info.amount_yen}]" if info.doc_type == "medical_receipt" else ""
                tokens = f"({usage.input_tokens} in / {usage.output_tokens} out)"
                if args.dry_run:
                    log(f"would rename {path.name} -> {name}{extra} {tokens}")
                else:
                    path.rename(path.parent / name)
                    log(f"renamed {path.name} -> {name}{extra} {tokens}")
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
