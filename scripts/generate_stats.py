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


def _clean_term(raw_term):
    term = re.sub(r"/[^/]+/", "", raw_term)
    term = re.sub(r"\(pronounced:\s*[^)]+\)", "", term, flags=re.IGNORECASE)
    term = re.sub(r"\[[^\]]+\]", "", term)
    return term.strip()


def _parse_entry_line(line):
    """Try progressively looser patterns to pull (term, definition) out of
    one line, so entries with nonstandard punctuation aren't silently
    dropped from the definitions list."""
    # 1. Strict standard pattern: "term (pos) - definition"
    m = re.match(ENTRY_PATTERN, line)
    if m:
        raw_term, _pos, definition = m.groups()
        return _clean_term(raw_term), definition.strip()

    # 2. Flexible: use the LAST "(...)" before " - " as the part-of-speech,
    #    everything before it is the term (handles terms that themselves
    #    contain parentheses, e.g. pronunciation guides).
    if "(" in line and ")" in line and " - " in line:
        left, _, definition = line.partition(" - ")
        paren_matches = list(re.finditer(r"\(([^)]+)\)", left))
        if paren_matches:
            term_part = left[: paren_matches[-1].start()].strip()
            if term_part and definition.strip():
                return _clean_term(term_part), definition.strip()

    # 3. Em dash instead of " - "
    for sep in (" — ", " – "):
        if sep in line and "(" in line and ")" in line:
            left, _, definition = line.partition(sep)
            paren_matches = list(re.finditer(r"\(([^)]+)\)", left))
            if paren_matches and definition.strip():
                term_part = left[: paren_matches[-1].start()].strip()
                if term_part:
                    return _clean_term(term_part), definition.strip()

    # 4. No parentheses at all, just "term - definition" (em/en dash or
    #    plain hyphen with spaces on both sides -- NOT a bare colon, which
    #    is too easy to false-positive on ordinary sentences).
    for sep in (" - ", " — ", " – "):
        if sep in line:
            term_part, _, definition = line.partition(sep)
            if term_part.strip() and definition.strip():
                return _clean_term(term_part), definition.strip()

    # 5. "term (pos): definition" -- colon right after the pos tag instead
    #    of " - ".
    m4 = re.match(r"^(.+?)\s*\(([^)]+)\)\s*:\s*(.+)$", line)
    if m4:
        term_part, _pos, definition = m4.groups()
        if definition.strip():
            return _clean_term(term_part), definition.strip()

    # 6. "term (pos) definition" -- no separator at all, just whitespace
    #    right after the pos tag.
    m5 = re.match(r"^(.+?)\s*\(([^)]+)\)\.?\s+(\S.*)$", line)
    if m5:
        term_part, _pos, definition = m5.groups()
        if definition.strip():
            return _clean_term(term_part), definition.strip()

    return None


METADATA_LABELS = {
    "etymology", "derived terms", "synonym", "synonyms",
    "ex", "example", "notes", "antonym", "antonyms",
}


def _is_metadata_line(line):
    """Lines that are part of an entry's metadata (etymology, examples,
    synonyms, a lone part-of-speech tag, a lone pronunciation guide, ...)
    rather than the entry's own term/definition line."""
    stripped = line.strip()
    label = stripped.rstrip(":").lower()
    if label in METADATA_LABELS:
        return True
    if stripped.lower().startswith(
        ("etymology:", "derived terms:", "synonym:", "synonyms:",
         "ex:", "example:", "notes:", "antonym:", "antonyms:", "- example:")
    ):
        return True
    # A lone "(adj.)" / "(n.)" style part-of-speech tag with nothing else
    if re.match(r"^\([^)]{1,12}\)\.?$", stripped):
        return True
    # A lone pronunciation guide continuation line, e.g. "aychesseedeepobadenux)"
    if re.match(r"^[a-zA-Z\-']+\)$", stripped):
        return True
    return False


def _emit(results, term, definition):
    results.append((term, definition))
    if "/" in term:
        for part in term.split("/"):
            part = part.strip()
            if part and part != term:
                results.append((part, definition))


def extract_definitions(content):
    """Return list of (term, definition), reading only each entry's actual
    main line -- ignoring Etymology/Synonym/Example/Derived-Terms sub-lines
    and multi-line continuations so those never get mistaken for entries
    in their own right."""
    if "-----DICTIONARY PROPER-----" not in content:
        return []
    body = content.split("-----DICTIONARY PROPER-----", 1)[1]

    results = []
    sections = re.split(r"(\n-{20,}\n)", body)

    i = 0
    while i < len(sections):
        section = sections[i]

        if re.match(r"^\n-{20,}\n$", section) and i + 1 < len(sections):
            # Hyphen-delimited complex block: find its one real main line.
            block_content = sections[i + 1]
            closing_present = i + 2 < len(sections) and re.match(
                r"^\n-{20,}\n$", sections[i + 2]
            )
            block_lines = [l.strip() for l in block_content.split("\n")]
            main_idx = None
            for idx, line in enumerate(block_lines):
                if not line or _is_metadata_line(line):
                    continue
                main_idx = idx
                break

            if main_idx is not None:
                main_line = block_lines[main_idx]
                parsed = _parse_entry_line(main_line)

                if not parsed:
                    # "term (pos)" with nothing trailing -- the definition
                    # is on the following bulleted line(s) instead.
                    m_only = re.match(r"^(.+?)\s*\(([^)]+)\)\.?\s*$", main_line)
                    if m_only:
                        term_part = m_only.group(1)
                        bullets = []
                        j = main_idx + 1
                        while j < len(block_lines):
                            nxt = block_lines[j]
                            if not nxt or _is_metadata_line(nxt):
                                break
                            bullets.append(re.sub(r"^-\s*", "", nxt))
                            j += 1
                        if bullets:
                            parsed = (_clean_term(term_part), "; ".join(bullets))

                if parsed:
                    _emit(results, parsed[0], parsed[1])

            i += 3 if closing_present else 2
            continue

        # Non-block content: simple one-line entries.
        for raw_line in section.split("\n"):
            line = raw_line.strip()
            if not line or line.startswith("-----") or _is_metadata_line(line):
                continue
            parsed = _parse_entry_line(line)
            if parsed:
                _emit(results, parsed[0], parsed[1])
        i += 1

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

    # Map lowercase term -> parsed definition (first match wins if the raw
    # text has duplicate/near-duplicate lines for the same term).
    parsed_by_lower = {}
    for t, d in extract_definitions(content):
        key = t.lower()
        if key not in parsed_by_lower:
            parsed_by_lower[key] = d

    # Walk every distinct corpus term individually (not through a
    # lowercase-keyed dict of terms) so two terms that only differ by
    # capitalization each keep their own row instead of one overwriting
    # the other.
    definitions = []
    for t in all_terms:
        d = parsed_by_lower.get(t.lower(), "(definition not parsed -- see raw file)")
        definitions.append((t, d))

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
