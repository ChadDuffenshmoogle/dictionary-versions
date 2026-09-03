"""
Generates site/stats.json for the UNICYCLIST DICTIONARY stats page.

Reads:
  - Full commit history of the repo (for the "new words added over time"
    frequency chart -- one commit == one new file == one new word, per
    the existing bot logic in dictionary_manager.py).
  - The single most recent "UNICYCLIST DICTIONARY v*.txt" file (for
    total entries, entries-per-letter, shortest/longest words, and
    shortest/longest definitions).

Requires no secrets for a public repo: GITHUB_TOKEN provided
automatically by GitHub Actions is enough to raise the anonymous rate
limit from 60/hr to 1000/hr.
"""

import json
import os
import re
import sys
from collections import defaultdict, Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

CENTRAL = ZoneInfo("America/Chicago")


def to_central_date(iso_timestamp):
    """GitHub commit timestamps come back in UTC (Z-suffixed). Convert to
    Central time before taking the calendar date, otherwise a commit made
    in the evening Central time lands on the wrong (next) UTC day."""
    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return dt.astimezone(CENTRAL).strftime("%Y-%m-%d")


def to_central_datetime_str(iso_timestamp):
    """Human-readable Central time, e.g. 'Sep 2, 2025, 3:45 PM CT'."""
    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    local = dt.astimezone(CENTRAL)
    return local.strftime("%b %-d, %Y, %-I:%M %p") + " CT"

GITHUB_OWNER = "ChadDuffenshmoogle"
GITHUB_REPO = "dictionary-versions"
GITHUB_BRANCH = "main"
FILE_PREFIX = "UNICYCLIST DICTIONARY"
FILE_EXTENSION = ".txt"
ENTRY_PATTERN = r'^(.+?) \((.+?)\) - (.+)$'

# Everything up through this date is noise (bulk backfill + a week of
# test/delete/reset churn) and gets excluded from both charts entirely.
# Only commits strictly after this date represent real new words. The
# dictionary already had BASELINE_COUNT words as of this date.
BASELINE_DATE = "2025-08-13"
BASELINE_COUNT = 513

API_ROOT = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
RAW_ROOT = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}"

TOKEN = os.environ.get("GITHUB_TOKEN")
HEADERS = {"Accept": "application/vnd.github+json"}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"


def api_get(url, params=None):
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    return resp


def get_all_commits():
    """Paginate through every commit on the branch."""
    commits = []
    page = 1
    while True:
        resp = api_get(
            f"{API_ROOT}/commits",
            params={"sha": GITHUB_BRANCH, "per_page": 100, "page": page},
        )
        batch = resp.json()
        if not batch:
            break
        commits.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return commits


def get_dictionary_filenames():
    """List every dictionary version file in the repo root."""
    resp = api_get(f"{API_ROOT}/contents/", params={"ref": GITHUB_BRANCH})
    files = resp.json()
    names = [
        f["name"] for f in files
        if f["name"].startswith(FILE_PREFIX) and f["name"].endswith(FILE_EXTENSION)
    ]
    return names


def parse_version_tuple(filename):
    m = re.search(r"v\.?(\d+)\.(\d+)\.(\d+)", filename, re.IGNORECASE)
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def get_latest_filename(filenames):
    return max(filenames, key=parse_version_tuple)


def sort_key_ignore_punct(s):
    term = s.split(" (")[0] if " (" in s else s
    term = term.lstrip(" '-\"")
    if term.lower().startswith("the "):
        term = term[4:] + ", the"
    return term.lower()


def fetch_raw(filename):
    url = f"{RAW_ROOT}/{requests.utils.quote(filename)}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def extract_corpus_by_letter(content):
    """Parse the '-----CORPUS-----' block into {letter: [terms]}."""
    m = re.search(
        r"-----CORPUS-----\s*\n(.*?)\n-----DICTIONARY PROPER-----",
        content, re.DOTALL,
    )
    if not m:
        return {}
    corpus_text = m.group(1)

    # Normalise "\nX: " markers into a consistent split point, keep the
    # leading "A: " label on the first group.
    groups = re.split(r"\n([A-Z]):\s+", corpus_text)
    by_letter = defaultdict(list)

    # groups[0] is the leading "A: term, term, ..." chunk (or similar)
    first_chunk = groups[0]
    lm = re.match(r"^([A-Z]):\s*(.*)$", first_chunk.strip(), re.DOTALL)
    if lm:
        letter, rest = lm.group(1), lm.group(2)
        by_letter[letter].extend([t.strip() for t in rest.split(",") if t.strip()])

    # Remaining groups come in (letter, text) pairs
    for i in range(1, len(groups) - 1, 2):
        letter = groups[i]
        text = groups[i + 1]
        by_letter[letter].extend([t.strip() for t in text.split(",") if t.strip()])

    return by_letter


def extract_definitions(content):
    """Return list of (term, definition) pulled from the dictionary body,
    covering both simple lines and hyphen-delimited complex blocks."""
    if "-----DICTIONARY PROPER-----" not in content:
        return []
    body = content.split("-----DICTIONARY PROPER-----", 1)[1]

    results = []
    for line in body.split("\n"):
        line = line.strip()
        if not line or line.startswith("-----") or line.startswith(
            ("Etymology:", "Ex:", "Example:", "Derived Terms:", "Notes:")
        ):
            continue
        m = re.match(ENTRY_PATTERN, line)
        if m:
            raw_term, _pos, definition = m.groups()
            term = re.sub(r"/[^/]+/", "", raw_term)
            term = re.sub(r"\(pronounced:\s*[^)]+\)", "", term, flags=re.IGNORECASE)
            term = re.sub(r"\[[^\]]+\]", "", term).strip()
            results.append((term, definition.strip()))
    return results


def main():
    commits = get_all_commits()

    # --- New-word-added frequency (== new-file frequency) ---
    additions_by_day = Counter()
    additions_by_day_all = Counter()  # unfiltered, used for cumulative growth
    added_terms_timeline = []
    add_re = re.compile(r"with new term '(.+?)'")
    for c in commits:
        msg = c["commit"]["message"]
        date = to_central_date(c["commit"]["author"]["date"])
        m = add_re.search(msg)
        if m:
            additions_by_day_all[date] += 1
            if date > BASELINE_DATE:
                additions_by_day[date] += 1
                added_terms_timeline.append({"date": date, "term": m.group(1)})

    # --- Most recent word added (commits come back newest-first) ---
    latest_word_term = None
    latest_word_timestamp = None
    for c in commits:
        m0 = add_re.search(c["commit"]["message"])
        if m0:
            latest_word_term = m0.group(1)
            latest_word_timestamp = to_central_datetime_str(c["commit"]["author"]["date"])
            break

    additions_series = [
        {"date": d, "count": n} for d, n in sorted(additions_by_day.items())
    ]

    # --- Seed cumulative total with the known baseline, then add only
    # commits strictly after that date (everything up to and including
    # the baseline date is bulk backfill / test noise, already baked
    # into BASELINE_COUNT). ---
    running_total = BASELINE_COUNT
    cumulative_series = [{"date": BASELINE_DATE, "total": BASELINE_COUNT}]
    for d, n in sorted(additions_by_day_all.items()):
        if d <= BASELINE_DATE:
            continue
        running_total += n
        cumulative_series.append({"date": d, "total": running_total})

    # --- Latest file: entries, letter breakdown, word/definition extremes ---
    filenames = get_dictionary_filenames()
    latest_name = get_latest_filename(filenames)
    content = fetch_raw(latest_name)

    by_letter = extract_corpus_by_letter(content)
    letter_counts = {letter: len(terms) for letter, terms in sorted(by_letter.items())}
    all_terms = sorted(
        {t for terms in by_letter.values() for t in terms}, key=sort_key_ignore_punct
    )
    total_entries = len(all_terms)

    def word_len(t):
        return len(sort_key_ignore_punct(t).replace(", the", ""))

    words_by_length_asc = sorted(all_terms, key=word_len)

    definitions = extract_definitions(content)
    definitions_by_length_asc = sorted(
        [{"term": t, "definition": d} for t, d in definitions],
        key=lambda td: len(td["definition"]),
    )

    stats = {
        "latest_version": latest_name,
        "latest_word_term": latest_word_term,
        "latest_word_timestamp": latest_word_timestamp,
        "latest_file_content": content,
        "total_entries": total_entries,
        "letter_counts": letter_counts,
        "additions_series": additions_series,
        "cumulative_series": cumulative_series,
        "added_terms_timeline": added_terms_timeline,
        "words_by_length_asc": words_by_length_asc,
        "definitions_by_length_asc": definitions_by_length_asc,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "site", "stats.json")
    with open(out_path, "w") as f:
        json.dump(stats, f, indent=2)

    print(f"Wrote {out_path}: {total_entries} entries, latest={latest_name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
