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
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import unicodedata

CENTRAL = ZoneInfo("America/Chicago")
SCRABBLE_FILE = "178,691 Scrabble Legal Words.txt"
SCRABBLE_SOURCE_URL = "https://github.com/redbo/scrabble/blob/master/dictionary.txt"
SCRABBLE_SOURCE_LABEL = "Scrabble word list source"

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
# word count as of this date is solved dynamically in main() (see
# "effective_baseline") rather than hardcoded, so it can never drift out
# of sync with the live file's actual total_entries.
BASELINE_DATE = "2025-08-13"

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
    resp = api_get(f"{API_ROOT}/git/trees/{GITHUB_BRANCH}")
    files = resp.json()["tree"]
    names = [
        f["path"] for f in files
        if f["type"] == "blob"
        and f["path"].startswith(FILE_PREFIX) and f["path"].endswith(FILE_EXTENSION)
    ]
    return names


def parse_version_tuple(filename):
    m = re.search(r"v\.?(\d+)\.(\d+)\.(\d+)", filename, re.IGNORECASE)
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def get_latest_filename(filenames):
    return max(filenames, key=parse_version_tuple)


def fetch_dictionary_file(filename):
    """Read the dictionary file through the API instead of raw.githubusercontent.com,
    whose cache can serve a version that is several minutes old."""
    resp = requests.get(
        f"{API_ROOT}/contents/{requests.utils.quote(filename)}",
        headers={**HEADERS, "Accept": "application/vnd.github.raw+json"},
        params={"ref": GITHUB_BRANCH},
        timeout=30,
    )
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


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
    #    right after the pos tag. Skip if the "definition" captured is
    #    actually just a pronunciation guide (e.g. "/aenline/") with
    #    nothing else -- that's not real definition text.
    m5 = re.match(r"^(.+?)\s*\(([^)]+)\)\.?\s+(\S.*)$", line)
    if m5:
        term_part, pos, definition = m5.groups()
        if definition.strip() and not re.match(r"^/[^/]+/$", definition.strip()):
            return _clean_term(term_part), pos.strip(), definition.strip()

    return None


METADATA_LABELS = {
    "etymology", "derived terms", "synonym", "synonyms",
    "ex", "example", "notes", "antonym", "antonyms", "pronunciation",
}

POS_WORDS = ("noun", "verb", "adjective", "adverb", "interjection", "pronoun", "preposition")


def _is_metadata_line(line):
    """Lines that are part of an entry's metadata (etymology, examples,
    pronunciation, synonyms, numbered extra senses, a lone part-of-speech
    tag, ...) rather than the entry's own term/definition line."""
    stripped = line.strip()
    label = stripped.rstrip(":").lower()
    if label in METADATA_LABELS:
        return True
    if stripped.lower().startswith(
        ("etymology:", "derived terms:", "synonym:", "synonyms:",
         "ex:", "example:", "notes:", "antonym:", "antonyms:", "- example:",
         "pronunciation:")
    ):
        return True
    if re.match(r"^\d+\.\s", stripped):
        return True
    if re.match(r"^\([^)]{1,12}\)\.?$", stripped):
        return True
    if re.match(r"^[a-zA-Z\-']+\)$", stripped):
        return True
    return False


def _make_entry(term, pos, definition):
    return {"term": term, "etymology": "", "pronunciation": "",
            "sections": [{"pos": pos, "defs": [{"text": definition, "examples": []}]}]}


def _move_leading_ipa(entry):
    """Old entries sometimes start their definition with /ipa/. Treat that as
    the pronunciation so the form and the pages show it in the right place."""
    if entry["pronunciation"] or not entry["sections"] or not entry["sections"][0]["defs"]:
        return entry
    first = entry["sections"][0]["defs"][0]
    m = re.match(r"^(/[^/]+/)\s*(\S.*)$", first["text"])
    if m:
        entry["pronunciation"] = m.group(1)
        first["text"] = m.group(2)
    return entry


def _emit(results, entry):
    entry = _move_leading_ipa(entry)
    term = entry["term"]
    results.append(dict(entry, primary=True))
    if "/" in term:
        for part in term.split("/"):
            part = part.strip()
            if part and part != term:
                results.append(dict(entry, term=part, primary=False))
    if re.match(r"^the\s+", term, re.IGNORECASE):
        results.append(dict(entry, term=re.sub(r"^the\s+", "", term, flags=re.IGNORECASE), primary=False))


def _parse_block_structured(block_lines):
    """Read one hyphen block: several parts of speech, numbered senses,
    examples, Etymology and Pronunciation. Old-style blocks (definition on
    later lines, quote lines, Etymology above the definition) still work."""
    term = None
    sections, cur, sense = [], None, None
    last = None
    ety = []
    pron = ""

    for line in block_lines:
        if not line:
            continue
        m = re.match(r"^etymology:\s*(.*)$", line, re.IGNORECASE)
        if m:
            ety.append(m.group(1).strip()); last = "ety"; continue
        m = re.match(r"^pronunciation:\s*(.*)$", line, re.IGNORECASE)
        if m:
            pron = m.group(1).strip(); last = "pron"; continue
        m = re.match(r"^(?:-\s*)?(?:examples?|ex):\s*(.*)$", line, re.IGNORECASE)
        if m:
            if sense is not None:
                sense["examples"].append(m.group(1).strip())
                last = "ex"
            else:
                last = "other"
            continue
        if re.match(r"^(?:-\s*)?(derived terms|synonyms?|antonyms?|notes)\s*:", line, re.IGNORECASE):
            last = "other"; continue
        m = re.match(r"^(\d+)\.\s+(.*)$", line)
        if cur is not None and m:
            sense = {"text": m.group(2).strip(), "examples": []}
            cur["defs"].append(sense); last = "def"; continue

        parsed = _parse_entry_line(line)
        if parsed and (term is None or parsed[0].lower() == term.lower()):
            if term is None:
                term = parsed[0]
            cur = {"pos": parsed[1], "defs": []}
            sense = {"text": re.sub(r"^1\.\s+", "", parsed[2]), "examples": []}
            cur["defs"].append(sense); sections.append(cur); last = "def"; continue

        if re.match(r"^\([^)]{1,12}\)\.?$", line) or line.lower() in POS_WORDS:
            continue  # lone part-of-speech subheading

        text = re.sub(r"^-\s*", "", line)
        if term is None:
            # "term (pos)" with the definition on the next lines, or a bare term line
            bare = re.sub(r"\s*/[^/]+/\s*$", "", line)
            pm = re.search(r"\(([^)]{1,20})\)\.?\s*$", bare)
            pos = pm.group(1).strip() if pm else ""
            term = _clean_term(re.sub(r"\s*\([^)]{1,20}\)\.?\s*$", "", bare).strip())
            if not term:
                term = None
                continue
            cur = {"pos": pos, "defs": []}; sections.append(cur)
            sense = None; last = "need"
        elif last == "need":
            sense = {"text": text, "examples": []}
            cur["defs"].append(sense); last = "def"
        elif last == "ety" and ety:
            ety[-1] += " " + text
        elif last == "ex" and sense is not None:
            sense["examples"].append(text)
        elif last == "def" and sense is not None:
            if re.match(r"^[\"“'‘]", text) and not sense["examples"]:
                sense["examples"].append(text)
                last = "ex"
            else:
                sense["text"] += "; " + text

    sections = [s for s in sections if any(d["text"] for d in s["defs"])]
    if not term or not sections:
        return None
    return {"term": term, "sections": sections,
            "etymology": " ".join(e for e in ety if e).strip(), "pronunciation": pron}


def _process_block(results, block_lines):
    entry = _parse_block_structured(block_lines)
    if entry:
        _emit(results, entry)


def extract_definitions(content):
    """Return a list of entry dicts: term, sections (pos + numbered senses +
    examples), etymology, pronunciation, primary. A word with several parts
    of speech is ONE entry with several sections. primary is True for the
    real term and False for a derived alternate form (a "/"-split variant
    or a "the "-stripped variant).

    Uses a simple linear state machine (in-block / not-in-block) so a stray
    "-----" line cannot swallow the entries after it."""
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
                _emit(results, _make_entry(parsed[0], parsed[1], parsed[2]))

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

def _split_pos(raw_pos):
    """Split one raw pos label into (real parts of speech, extra tags).
    "Cyrilism, v."  -> (["Verb"], ["Cyrilism"])   any "...ism" word is a tag
    "mass n."       -> (["Noun"], ["Uncountable"])
    "v. + pron."    -> (["Verb", "Pronoun"], [])"""
    labels, tags = [], []
    for tok in re.split(r"\s*(?:,|\+|/|&|;)\s*", raw_pos or ""):
        tok = tok.strip()
        if not tok:
            continue
        if re.fullmatch(r"[A-Za-z]+ism", tok, re.IGNORECASE):
            tag = tok[0].upper() + tok[1:]
            if tag not in tags:
                tags.append(tag)
            continue
        label = _normalize_pos(tok)
        if label == "Mass Noun":
            label = "Noun"
            if "Uncountable" not in tags:
                tags.append("Uncountable")
        if label not in labels:
            labels.append(label)
    return labels, tags


def fetch_scrabble_words():
    """Load the Scrabble word list into a set of uppercase words."""
    text = fetch_raw(SCRABBLE_FILE)
    return {line.strip().upper() for line in text.splitlines() if line.strip()}


def strip_accents(s):
    """Fold accented characters to their plain ASCII equivalent, e.g.
    café -> cafe, naïve -> naive."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def scrabble_status(term, scrabble_set):
    """Returns 'legal', 'normalized', or 'illegal'.

    'legal'      -- the term, letters-only, is a single unbroken word in
                     the list exactly as typed (no spaces/hyphens/accents
                     to begin with, or they just happen not to matter).
    'normalized' -- only legal after stripping spaces, hyphens,
                     apostrophes, and accents -- i.e. it needed help to
                     qualify.
    'illegal'    -- not in the list either way.
    """
    stripped_raw = term.strip()
    if re.fullmatch(r"[A-Za-z]+", stripped_raw) and stripped_raw.upper() in scrabble_set:
        return "legal"

    normalized = strip_accents(stripped_raw)
    normalized = re.sub(r"[^A-Za-z]", "", normalized)
    if normalized and normalized.upper() in scrabble_set:
        return "normalized"

    return "illegal"

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

    # --- Latest file: entries, letter breakdown, word/definition extremes ---
    filenames = get_dictionary_filenames()
    latest_name = get_latest_filename(filenames)
    content = fetch_dictionary_file(latest_name)

    # The CORPUS block is just a comma-joined index and can't reliably be
    # split back into terms -- a term that itself contains a comma (e.g.
    # the idiom "If it ain't fixed, don't break it") is indistinguishable
    # from two separate terms once it's been joined with ", ". So the
    # term list comes from the DICTIONARY PROPER parse instead, which is
    # delimited by "-----" lines and never splits inside a term.
    raw_rows = extract_definitions(content)
    all_terms = sorted(
        {r["term"] for r in raw_rows if r["primary"]},
        key=sort_key_ignore_punct,
    )
    total_entries = len(all_terms)

    # --- Cumulative growth series, anchored to the real current total ---
    # (see the long note in git history: the baseline is solved backward
    # from today's real total so the chart always ends on Total Entries)
    additions_since_baseline = sum(
        n for d, n in additions_by_day_all.items() if d > BASELINE_DATE
    )
    effective_baseline = total_entries - additions_since_baseline

    running_total = effective_baseline
    cumulative_series = [{"date": BASELINE_DATE, "total": effective_baseline}]
    for d, n in sorted(additions_by_day_all.items()):
        if d <= BASELINE_DATE:
            continue
        running_total += n
        cumulative_series.append({"date": d, "total": running_total})

    def first_letter_key(term):
        for ch in sort_key_ignore_punct(term):
            if ch.isalpha():
                return ch.upper()
        return None

    letter_counts = dict(sorted(Counter(
        k for k in (first_letter_key(t) for t in all_terms) if k
    ).items()))

    def word_len(t):
        return len(sort_key_ignore_punct(t).replace(", the", ""))

    words_by_length_asc = sorted(all_terms, key=word_len)

    # First match wins if the raw text has duplicate lines for the same term.
    parsed_by_lower = {}
    for r in raw_rows:
        parsed_by_lower.setdefault(r["term"].lower(), r)

    def build_row(t, e):
        if not e or not e["sections"]:
            return {"term": t, "pos": "(no pos)", "pos_list": ["(no pos)"], "tags": [],
                    "definition": "(definition not parsed -- see raw file)",
                    "senses": [], "pronunciation": "", "etymology": ""}
        senses, pos_list, tags = [], [], []
        for s in e["sections"]:
            labels, stags = _split_pos(s["pos"])
            senses.append({"pos": ", ".join(labels) or "(no pos)", "defs": s["defs"]})
            for p in labels:
                if p not in pos_list:
                    pos_list.append(p)
            for t in stags:
                if t not in tags:
                    tags.append(t)
        if not pos_list:
            pos_list = ["(no pos)"]
        lines = []
        for s in senses:
            if len(senses) > 1:
                lines.append(f"[{s['pos']}]")
            if len(s["defs"]) > 1:
                lines += [f"{i}. {d['text']}" for i, d in enumerate(s["defs"], 1)]
            else:
                lines.append(s["defs"][0]["text"])
        return {"term": t, "pos": " / ".join(pos_list), "pos_list": pos_list, "tags": tags,
                "definition": "\n".join(lines), "senses": senses,
                "pronunciation": e["pronunciation"], "etymology": e["etymology"]}

    # A word with several parts of speech is ONE word in total_entries and
    # letter_counts, but is counted under EACH of its parts of speech here.
    rows = []
    pos_counts = Counter()
    for t in all_terms:
        row = build_row(t, parsed_by_lower.get(t.lower()))
        for p in row["pos_list"]:
            pos_counts[p] += 1
        rows.append(row)

    definitions_by_length_asc = sorted(rows, key=lambda r: len(r["definition"]))

    # --- Scrabble legality ---
    scrabble_set = fetch_scrabble_words()
    scrabble_statuses = {t: scrabble_status(t, scrabble_set) for t in all_terms}
    scrabble_counts = Counter(scrabble_statuses.values())
    for row in definitions_by_length_asc:
        row["scrabble"] = scrabble_statuses.get(row["term"], "illegal")

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
        "scrabble_legal_count": scrabble_counts.get("legal", 0),
        "scrabble_normalized_count": scrabble_counts.get("normalized", 0),
        "scrabble_illegal_count": scrabble_counts.get("illegal", 0),
        "scrabble_source_url": SCRABBLE_SOURCE_URL,
        "scrabble_source_label": SCRABBLE_SOURCE_LABEL,
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
