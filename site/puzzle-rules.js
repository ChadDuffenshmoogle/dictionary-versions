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
