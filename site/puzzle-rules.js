// HIDDEN CATEGORY LINKS (these categories are not listed on the Categories page; open them by URL.
// Add a line here whenever a rule is added to PUZZLE_RULES. The link is wiktionary.html#c= plus 'puzzle:' and the rule name, URL-encoded):
//  - Contains All Five Vowels: wiktionary.html#c=puzzle%3AContains%20All%20Five%20Vowels
//  - No Vowels: wiktionary.html#c=puzzle%3ANo%20Vowels
//  - Q Without a U: wiktionary.html#c=puzzle%3AQ%20Without%20a%20U
//  - Three Letters in Alphabetical Order: wiktionary.html#c=puzzle%3AThree%20Letters%20in%20Alphabetical%20Order
//  - Five or More of the Same Letter: wiktionary.html#c=puzzle%3AFive%20or%20More%20of%20the%20Same%20Letter
//  - No Repeated Letters: wiktionary.html#c=puzzle%3ANo%20Repeated%20Letters
//  - Spells Another Entry Backwards: wiktionary.html#c=puzzle%3ASpells%20Another%20Entry%20Backwards
//  - A Word Said Twice: wiktionary.html#c=puzzle%3AA%20Word%20Said%20Twice
//  - Letters Alternate Vowel and Consonant: wiktionary.html#c=puzzle%3ALetters%20Alternate%20Vowel%20and%20Consonant
//  - Anagram of Another Entry: wiktionary.html#c=puzzle%3AAnagram%20of%20Another%20Entry
//  - Double Z: wiktionary.html#c=puzzle%3ADouble%20Z
//  - Three or More Double Letters: wiktionary.html#c=puzzle%3AThree%20or%20More%20Double%20Letters
//  - Starts With a Greek Letter: wiktionary.html#c=puzzle%3AStarts%20With%20a%20Greek%20Letter
//  - Hides a Planet: wiktionary.html#c=puzzle%3AHides%20a%20Planet
//  - Starts With a Month: wiktionary.html#c=puzzle%3AStarts%20With%20a%20Month

// FILE DEPENDENCIES (things in other files that depend on this one):
//  - connections.html  loads this file, calls puzzleSpans() through makeAutoCats() and reads PUZZLE_RULES (name, level, desc, find).
//  - wiktionary.html   loads this file too, to list these under "Automatic categories" and to underline the matching letters (puzzleMarkHtml).
//  - stats.json        the dictionary word list that has() and has.all() are built from (see connections.html start()).
//  - Rule names are used as ids ('puzzle:' + name), so renaming a rule resets how old puzzles are matched.

// Automatic puzzle categories. Each rule looks at a word's letters and says whether it fits.
// find(word, has) returns null (no match) or a list of [start, end] spots to underline.
// An empty list [] means "it matches, but there is nothing to underline".
// has(lowercaseWord) tells you whether another word is in the dictionary (and has.all() lists them all).
// level says how hard the category is: 2 = blue-ish, 3 = purple-ish.

function pzNorm(t) {
  return t.split('').map(c => c.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()).join('');
}
function pzIsWord(t) {
  return t.length >= 3 && t.length <= 24 && /^[\p{L}'’-]+$/u.test(t);
}
function pzWhere(n, test) {
  const out = [];
  for (let i = 0; i < n.length; i++) if (test(n[i])) out.push([i, i + 1]);
  return out;
}

function pzAnagrams(has) {
  if (!has.ana) {
    has.ana = new Map();
    if (has.all) for (const w of has.all()) {
      const n = pzNorm(w);
      if (!/^[a-z]{4,}$/.test(n)) continue;
      const k = n.split('').sort().join('');
      if (!has.ana.has(k)) has.ana.set(k, new Set());
      has.ana.get(k).add(n);
    }
  }
  return has.ana;
}

// Builds a rule for a closed list of things (Greek letters, planets, months...).
// where = 'start' (the word begins with one of them) or 'inside' (one of them appears anywhere in the word).
// The word has to be longer than the item, and a bare plural (item + s or es) does not count. The item is underlined.
// To make a new "fixed list" category, add an entry to PUZZLE_RULES that uses pzFixedSet(['item', ...], 'start' or 'inside').
function pzFixedSet(items, where) {
  return function (t) {
    const n = pzNorm(t), spans = [];
    for (const item of items) {
      if (n.length <= item.length) continue;
      for (let i = 0; i + item.length <= n.length; i++) {
        if (where === 'start' && i > 0) break;
        if (!n.startsWith(item, i)) continue;
        const rest = n.slice(i + item.length);
        if (where === 'start' && (rest === 's' || rest === 'es')) continue;
        spans.push([i, i + item.length]);
      }
    }
    return spans.length ? spans : null;
  };
}

const PUZZLE_RULES = [
  {
    name: 'Contains All Five Vowels',
    level: 3,
    desc: 'Words with an A, an E, an I, an O and a U. The vowels are underlined.',
    find(t) {
      const n = pzNorm(t);
      return 'aeiou'.split('').every(v => n.includes(v)) ? pzWhere(n, c => 'aeiou'.includes(c)) : null;
    },
  },
  {
    name: 'No Vowels',
    level: 2,
    desc: 'Words with no A, E, I, O or U at all.',
    find(t) {
      return /^[A-Za-z]+$/.test(t) && !/[aeiou]/i.test(t) ? [[0, t.length]] : null;
    },
  },
  {
    name: 'Q Without a U',
    level: 2,
    desc: 'Words with a Q but no U anywhere. The Q is underlined.',
    find(t) {
      const n = pzNorm(t);
      return n.includes('q') && !n.includes('u') ? pzWhere(n, c => c === 'q') : null;
    },
  },
  {
    name: 'Three Letters in Alphabetical Order',
    level: 3,
    desc: 'Words with three or more neighboring letters in alphabet order, like STU. That run is underlined.',
    find(t) {
      const n = pzNorm(t), spans = [];
      let i = 0;
      while (i < n.length) {
        let j = i;
        while (j + 1 < n.length && /[a-z]/.test(n[j]) && /[a-z]/.test(n[j + 1]) && n.charCodeAt(j + 1) === n.charCodeAt(j) + 1) j++;
        if (j - i + 1 >= 3) spans.push([i, j + 1]);
        i = j + 1;
      }
      return spans.length ? spans : null;
    },
  },
  {
    name: 'Five or More of the Same Letter',
    level: 3,
    desc: 'Words that use one letter at least five times. That letter is underlined.',
    find(t) {
      const n = pzNorm(t), count = {};
      for (const c of n) if (/[a-z]/.test(c)) count[c] = (count[c] || 0) + 1;
      let best = '', max = 0;
      for (const c in count) if (count[c] > max) { best = c; max = count[c]; }
      return max >= 5 ? pzWhere(n, c => c === best) : null;
    },
  },
  {
    name: 'No Repeated Letters',
    level: 3,
    desc: 'Words of ten or more letters where no letter appears twice.',
    find(t) {
      const n = pzNorm(t);
      return /^[a-z]{10,}$/.test(n) && new Set(n).size === n.length ? [] : null;
    },
  },
  {
    name: 'Spells Another Entry Backwards',
    level: 3,
    desc: 'Words that read as a different dictionary entry when flipped around.',
    find(t, has) {
      const n = pzNorm(t);
      if (!/^[a-z]+$/.test(n)) return null;
      const r = n.split('').reverse().join('');
      return r !== n && has(r) ? [[0, t.length]] : null;
    },
  },
  {
    name: 'A Word Said Twice',
    level: 3,
    desc: 'Words made of the same chunk repeated two times.',
    find(t) {
      const n = pzNorm(t);
      if (!/^[a-z]+$/.test(n) || n.length < 4 || n.length % 2) return null;
      const h = n.length / 2;
      return n.slice(0, h) === n.slice(h) ? [[0, h], [h, n.length]] : null;
    },
  },
  {
    name: 'Letters Alternate Vowel and Consonant',
    level: 3,
    desc: 'Words of seven or more letters where vowels and consonants take turns, never two of the same kind in a row. The vowels are underlined.',
    find(t) {
      const n = pzNorm(t);
      if (!/^[a-z]{7,}$/.test(n)) return null;
      for (let i = 0; i + 1 < n.length; i++) if (('aeiou'.includes(n[i])) === ('aeiou'.includes(n[i + 1]))) return null;
      return pzWhere(n, c => 'aeiou'.includes(c));
    },
  },
  {
    name: 'Anagram of Another Entry',
    level: 3,
    desc: 'Words whose letters can be shuffled into a different entry (not just flipped backwards).',
    find(t, has) {
      const n = pzNorm(t);
      if (!/^[a-z]{4,}$/.test(n)) return null;
      const same = pzAnagrams(has).get(n.split('').sort().join(''));
      const flipped = n.split('').reverse().join('');
      return same && [...same].some(o => o !== n && o !== flipped) ? [[0, t.length]] : null;
    },
  },
  {
    name: 'Double Z',
    level: 2,
    desc: 'Words with two Zs in a row. The ZZ is underlined.',
    find(t) {
      const n = pzNorm(t), spans = [];
      for (let i = 0; i + 1 < n.length; i++) if (n[i] === 'z' && n[i + 1] === 'z') spans.push([i, i + 2]);
      return spans.length ? spans : null;
    },
  },
  {
    name: 'Three or More Double Letters',
    level: 3,
    desc: 'Words with three or more pairs of doubled letters, like BOOKKEEPER. Each pair is underlined.',
    find(t) {
      const n = pzNorm(t), spans = [];
      for (let i = 0; i + 1 < n.length; i++) {
        if (/[a-z]/.test(n[i]) && n[i] === n[i + 1]) { spans.push([i, i + 2]); i++; }
      }
      return spans.length >= 3 ? spans : null;
    },
  },
  {
    name: 'Starts With a Greek Letter',
    level: 3,
    desc: 'Words that begin with the name of a Greek letter, like ALPHABET or DELTOID. Mu, nu, xi and pi are left out because they are too short. The letter name is underlined.',
    find: pzFixedSet(['alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta', 'iota', 'kappa', 'lambda',
      'rho', 'sigma', 'tau', 'upsilon', 'phi', 'chi', 'psi', 'omega', 'omicron'], 'start'),
  },
  {
    name: 'Hides a Planet',
    level: 3,
    desc: 'Words with the name of one of the eight planets tucked inside, like UNEARTH. The planet is underlined.',
    find: pzFixedSet(['mercury', 'venus', 'earth', 'mars', 'jupiter', 'saturn', 'uranus', 'neptune'], 'inside'),
  },
  {
    name: 'Starts With a Month',
    level: 3,
    desc: 'Words that begin with the name of a month, like MARCHING or MAYOR. The month is underlined.',
    find: pzFixedSet(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october',
      'november', 'december'], 'start'),
  },
];

// Returns null (no match) or the list of spots to underline.
function puzzleSpans(rule, term, has) {
  if (!pzIsWord(term)) return null;
  return rule.find(term, has || (() => false));
}

// Turns a word into HTML with the matching spots underlined.
function puzzleMarkHtml(term, spans) {
  const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  if (!spans || !spans.length) return esc(term);
  const marked = new Array(term.length).fill(false);
  spans.forEach(([a, b]) => { for (let i = a; i < b; i++) marked[i] = true; });
  let out = '', open = false;
  for (let i = 0; i < term.length; i++) {
    if (marked[i] && !open) { out += '<u>'; open = true; }
    if (!marked[i] && open) { out += '</u>'; open = false; }
    out += esc(term[i]);
  }
  return out + (open ? '</u>' : '');
}
