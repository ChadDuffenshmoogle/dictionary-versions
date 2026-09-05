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
    term = re.sub(r"\(pronounced:\s*[^)]+\)", "", raw_term, flags=re.IGNORECASE)
    term = re.sub(r"\[[^\]]+\]", "", term)
    return term.strip()


def _parse_entry_line(line):
    """Try progressively looser patterns to pull (term, pos, definition)
    out of one line, so entries with nonstandard punctuation aren't
    silently dropped from the definitions list."""
    # Normalize "(pos)-definition" (missing space before the dash) so
    # every pattern below can assume a space is there.
    line = re.sub(r"\)-", ") -", line)

    # 1. Strict standard pattern: "term (pos) - definition"
    m = re.match(ENTRY_PATTERN, line)
    if m:
        raw_term, pos, definition = m.groups()
        return _clean_term(raw_term), pos.strip(), definition.strip()

    # 2. Flexible: use the LAST "(...)" before " - " as the part-of-speech,
    #    everything before it is the term (handles terms that themselves
    #    contain parentheses, e.g. pronunciation guides).
    if "(" in line and ")" in line and " - " in line:
        left, _, definition = line.partition(" - ")
        paren_matches = list(re.finditer(r"\(([^)]+)\)", left))
        if paren_matches:
            term_part = left[: paren_matches[-1].start()].strip()
            if term_part and definition.strip():
                return _clean_term(term_part), paren_matches[-1].group(1).strip(), definition.strip()

    # 3. Em dash instead of " - "
    for sep in (" — ", " – "):
        if sep in line and "(" in line and ")" in line:
            left, _, definition = line.partition(sep)
            paren_matches = list(re.finditer(r"\(([^)]+)\)", left))
            if paren_matches and definition.strip():
                term_part = left[: paren_matches[-1].start()].strip()
                if term_part:
                    return _clean_term(term_part), paren_matches[-1].group(1).strip(), definition.strip()

    # 4. No parentheses at all, just "term - definition" (em/en dash or
    #    plain hyphen with spaces on both sides -- NOT a bare colon, which
    #    is too easy to false-positive on ordinary sentences). No pos tag
    #    available in this format.
    for sep in (" - ", " — ", " – "):
        if sep in line:
            term_part, _, definition = line.partition(sep)
            if term_part.strip() and definition.strip():
                return _clean_term(term_part), "", definition.strip()

    # 5. "term (pos): definition" -- colon right after the pos tag instead
    #    of " - ".
    m4 = re.match(r"^(.+?)\s*\(([^)]+)\)\s*:\s*(.+)$", line)
    if m4:
        term_part, pos, definition = m4.groups()
        if definition.strip():
            return _clean_term(term_part), pos.strip(), definition.strip()

    # 6. "term (pos) definition" -- no separator at all, just whitespace
    #    right after the pos tag.
    m5 = re.match(r"^(.+?)\s*\(([^)]+)\)\.?\s+(\S.*)$", line)
    if m5:
        term_part, pos, definition = m5.groups()
        if definition.strip():
            return _clean_term(term_part), pos.strip(), definition.strip()

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


def _emit(results, term, pos, definition):
    results.append((term, pos, definition))
    if "/" in term:
        for part in term.split("/"):
            part = part.strip()
            if part and part != term:
                results.append((part, pos, definition))
    if re.match(r"^the\s+", term, re.IGNORECASE):
        results.append((re.sub(r"^the\s+", "", term, flags=re.IGNORECASE), pos, definition))
    if ", " in term:
        for part in term.split(", "):
            part = part.strip()
            if part and part != term:
                results.append((part, pos, definition))


def _process_block(results, block_lines):
    """Extract (term, pos, definition) from one hyphen-delimited block's
    buffered lines, using only its real main line."""
    main_idx = None
    for idx, line in enumerate(block_lines):
        if not line or _is_metadata_line(line):
            continue
        main_idx = idx
        break
    if main_idx is None:
        return

    main_line = block_lines[main_idx]
    parsed = _parse_entry_line(main_line)
    used_fallback = False

    if not parsed:
        # Either "term (pos)" with nothing trailing, or a completely bare
        # term line -- either way, the definition lives in bulleted
        # ("- ...") lines further down the block, possibly interspersed
        # with lone POS subheadings like "(n.)" / "Noun" / "Interjection".
        pos_match = re.search(r"\(([^)]{1,20})\)\.?\s*$", main_line)
        pos = pos_match.group(1).strip() if pos_match else ""
        term_part = re.sub(r"\s*\([^)]{1,20}\)\.?\s*$", "", main_line).strip()
        if term_part:
            bullets = []
            j = main_idx + 1
            while j < len(block_lines):
                nxt = block_lines[j]
                if not nxt:
                    j += 1
                    continue
                # Stop at a genuine metadata label (etymology/example/...)
                if re.match(
                    r"^(etymology|ex|example|synonym|synonyms|antonym|"
                    r"antonyms|derived terms|notes)\b",
                    nxt, re.IGNORECASE,
                ):
                    break
                # Skip (not stop at) a lone POS subheading, e.g. "(n.)" or
                # "Noun" / "Interjection" on its own line.
                if re.match(r"^\([^)]{1,12}\)\.?$", nxt) or nxt.lower() in (
                    "noun", "verb", "adjective", "adverb",
                    "interjection", "pronoun", "preposition",
                ):
                    j += 1
                    continue
                if nxt.startswith("-"):
                    bullets.append(re.sub(r"^-\s*", "", nxt))
                j += 1
            if bullets:
                parsed = (_clean_term(term_part), pos, "; ".join(bullets))
                used_fallback = True

    # Even when the main line parsed fine on its own, a definition can
    # still continue as bulleted sub-items right after it (e.g. "part of
    # a new lineup, along with" followed by "- item one", "- item two").
    # Append any such trailing bullets rather than silently dropping them
    # (skip this if the fallback above already consumed them).
    if parsed and not used_fallback:
        extra_bullets = []
        j = main_idx + 1
        while j < len(block_lines):
            nxt = block_lines[j]
            if not nxt:
                j += 1
                continue
            if re.match(
                r"^(etymology|ex|example|synonym|synonyms|antonym|"
                r"antonyms|derived terms|notes)\b",
                nxt, re.IGNORECASE,
            ):
                break
            if not nxt.startswith("-"):
                break
            extra_bullets.append(re.sub(r"^-\s*", "", nxt))
            j += 1
        if extra_bullets:
            parsed = (parsed[0], parsed[1], parsed[2] + "; " + "; ".join(extra_bullets))

    if parsed:
        _emit(results, parsed[0], parsed[1], parsed[2])


def extract_definitions(content):
    """Return list of (term, definition), reading only each entry's actual
    main line -- ignoring Etymology/Synonym/Example/Derived-Terms sub-lines
    and multi-line continuations so those never get mistaken for entries
    in their own right.

    Uses a simple linear state machine (in-block / not-in-block) rather
    than pre-splitting the whole body on paired delimiters -- a single
    unmatched or stray "-----" line anywhere in the file would otherwise
    misalign every block after it and swallow large swaths of real
    entries into one bad "block"."""
    if "-----DICTIONARY PROPER-----" not in content:
        return []
    body = content.split("-----DICTIONARY PROPER-----", 1)[1]

    results = []
    in_block = False
    block_lines = []

    for raw_line in body.split("\n"):
        line = raw_line.strip()

        if re.match(r"^-{20,}$", line):
            if in_block:
                _process_block(results, block_lines)
                block_lines = []
                in_block = False
            else:
                in_block = True
                block_lines = []
            continue

        if in_block:
            block_lines.append(line)
        else:
            if not line or _is_metadata_line(line):
                continue
            parsed = _parse_entry_line(line)
            if parsed:
                _emit(results, parsed[0], parsed[1], parsed[2])

    # A block left open at end-of-file (unmatched delimiter) still gets
    # its main entry read rather than silently dropped.
    if in_block and block_lines:
        _process_block(results, block_lines)

    return results


# Standard part-of-speech abbreviation variants (as used across Merriam-
# Webster, Oxford, and Wiktionary conventions), mapped to one canonical
# label so "n." / "n" / "noun" all count as the same pie slice. Includes
# a few tags this dictionary uses that aren't in standard style guides
# (expr., ono., acr.) grouped under their closest real category.
POS_NORMALIZATION = {
    # Noun
    "n": "Noun", "noun": "Noun", "nn": "Noun", "s": "Noun", "sb": "Noun",
    # Proper noun
    "pn": "Proper Noun", "propern": "Proper Noun", "propernoun": "Proper Noun", "propn": "Proper Noun",
    # Mass / uncountable noun (kept distinct -- meaningfully different from a plain noun)
    "massn": "Mass Noun", "massnoun": "Mass Noun", "uncountable": "Mass Noun", "uncountablen": "Mass Noun",
    # Verb (transitive/intransitive folded into plain Verb)
    "v": "Verb", "verb": "Verb", "vb": "Verb",
    "vt": "Verb", "vtr": "Verb", "vi": "Verb", "vintr": "Verb",
    "phrasalv": "Verb", "phrasalverb": "Verb",
    # Adjective
    "adj": "Adjective", "adjective": "Adjective", "a": "Adjective",
    # Adverb
    "adv": "Adverb", "adverb": "Adverb",
    # Pronoun
    "pron": "Pronoun", "pronoun": "Pronoun",
    # Preposition
    "prep": "Preposition", "preposition": "Preposition",
    # Conjunction
    "conj": "Conjunction", "conjunction": "Conjunction",
    # Determiner / article
    "det": "Determiner", "determiner": "Determiner", "art": "Determiner", "article": "Determiner",
    # Interjection
    "int": "Interjection", "inter": "Interjection", "interj": "Interjection",
    "interjection": "Interjection", "excl": "Interjection", "exclamation": "Interjection",
    # Expression / idiom / phrase
    "expr": "Expression", "expression": "Expression",
    "idiom": "Expression", "phr": "Expression", "phrase": "Expression", "saying": "Expression",
    # Abbreviation
    "abbr": "Abbreviation", "abbreviation": "Abbreviation", "abbrev": "Abbreviation",
    # Acronym / initialism
    "acr": "Acronym", "acro": "Acronym", "acronym": "Acronym",
    "init": "Acronym", "initialism": "Acronym",
    # Onomatopoeia
    "ono": "Onomatopoeia", "onom": "Onomatopoeia", "onomatopoeia": "Onomatopoeia", "onomatopoeic": "Onomatopoeia",
    # Particle
    "part": "Particle", "particle": "Particle",
    # Suffix / prefix / infix / combining form
    "suffix": "Suffix", "suf": "Suffix", "suff": "Suffix",
    "prefix": "Prefix", "pref": "Prefix",
    "infix": "Infix",
    "combform": "Combining Form", "combiningform": "Combining Form",
    # Alternate/variant form marker
    "alt": "Alternate Form", "alternate": "Alternate Form", "alternateform": "Alternate Form",
    "var": "Alternate Form", "variant": "Alternate Form", "altform": "Alternate Form",
    # Numeral
    "num": "Numeral", "numeral": "Numeral", "number": "Numeral",
    # Auxiliary / modal verb
    "aux": "Auxiliary Verb", "auxiliary": "Auxiliary Verb", "auxiliaryverb": "Auxiliary Verb",
    "modal": "Auxiliary Verb", "modalv": "Auxiliary Verb", "modalverb": "Auxiliary Verb",
    # Contraction / clipping
    "contr": "Contraction", "contraction": "Contraction",
    "clipping": "Clipping", "clip": "Clipping",
    # Symbol / letter
    "sym": "Symbol", "symbol": "Symbol", "letter": "Letter",
    # Gerund / participle
    "ger": "Gerund", "gerund": "Gerund",
    "ptcp": "Participle", "participle": "Participle",
    # Proverb / collocation
    "prov": "Proverb", "proverb": "Proverb",
    "colloc": "Collocation", "collocation": "Collocation",
    # Usage/register labels this dictionary sometimes uses in place of a
    # real POS tag
    "slang": "Slang", "colloq": "Colloquial", "colloquial": "Colloquial",
    "informal": "Informal", "vulgar": "Vulgar", "derog": "Derogatory", "derogatory": "Derogatory",
    "archaic": "Archaic", "obs": "Obsolete", "obsolete": "Obsolete",
    "dial": "Dialectal", "dialect": "Dialectal", "dialectal": "Dialectal",
    # Interrogative / demonstrative / quantifier / classifier
    "interrog": "Interrogative", "interrogative": "Interrogative",
    "dem": "Demonstrative", "demonstrative": "Demonstrative",
    "quant": "Quantifier", "quantifier": "Quantifier",
    "class": "Classifier", "classifier": "Classifier",
    # Honorific / salutation
    "honorific": "Honorific", "salutation": "Salutation",
}


def _normalize_pos(raw_pos):
    """Map a huge range of amateur-written pos tags to one canonical label.
    Rather than enumerate every punctuation/spacing variant, this strips
    ALL periods/commas/spaces before lookup (so "v.t.", "vt", "v t", and
    "v.t" all collapse to the same key), then falls back to a naive
    singular/plural fold ("nouns" / "verbs" / "adjs" -> "noun" / "verb" /
    "adj") before giving up and just showing the tag as its own slice."""
    if not raw_pos or not raw_pos.strip():
        return "(no pos)"
    raw = raw_pos.strip()
    key = re.sub(r"[.,\s]", "", raw.lower())
    if key in POS_NORMALIZATION:
        return POS_NORMALIZATION[key]
    if key.endswith("s") and key[:-1] in POS_NORMALIZATION:
        return POS_NORMALIZATION[key[:-1]]
    if key.endswith("es") and key[:-2] in POS_NORMALIZATION:
        return POS_NORMALIZATION[key[:-2]]
    return raw


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

    # Map lowercase term -> (pos, parsed definition). First match wins if
    # the raw text has duplicate/near-duplicate lines for the same term.
    parsed_by_lower = {}
    for t, pos, d in extract_definitions(content):
        key = t.lower()
        if key not in parsed_by_lower:
            parsed_by_lower[key] = (pos, d)

    # Walk every distinct corpus term individually (not through a
    # lowercase-keyed dict of terms) so two terms that only differ by
    # capitalization each keep their own row instead of one overwriting
    # the other.
    definitions = []
    pos_counts = Counter()
    for t in all_terms:
        pos, d = parsed_by_lower.get(t.lower(), ("", "(definition not parsed -- see raw file)"))
        pos_clean = _normalize_pos(pos)
        pos_counts[pos_clean] += 1
        definitions.append((t, pos_clean, d))

    definitions_by_length_asc = sorted(
        [{"term": t, "pos": p, "definition": d} for t, p, d in definitions],
        key=lambda td: len(td["definition"]),
    )

    stats = {
        "latest_version": latest_name,
        "latest_word_term": latest_word_term,
        "latest_word_timestamp": latest_word_timestamp,
        "latest_file_content": content,
        "total_entries": total_entries,
        "letter_counts": letter_counts,
        "pos_counts": dict(pos_counts),
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
