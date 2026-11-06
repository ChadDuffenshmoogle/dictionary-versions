// HIDDEN CATEGORY LINKS (these categories are not listed on the Categories page):
//  - All of them, with the ones that have at least 4 entries in bold: wiktionary.html#hidden
//  - One of them: wiktionary.html#c=puzzle%3A plus the rule name, URL-encoded (example: wiktionary.html#c=puzzle%3ADouble%20Z).
//    The hidden page above lists every rule automatically, so no list is kept here.

// FILE DEPENDENCIES (things in other files that depend on this one):
//  - connections.html  loads this file, calls puzzleSpans() through makeAutoCats() and reads PUZZLE_RULES (name, level, desc, find).
//  - wiktionary.html   loads this file too, to build the hidden category pages (wiktionary.html#hidden) and to underline the matching letters (puzzleMarkHtml).
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

const PZ_HAND_RULES = [
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
];

// ---------- fixed lists ----------
// Each closed list below makes three categories: Starts With, Ends With, and Hides (the item sits inside, touching neither end).
// The word has to be longer than the item, so "one" itself never counts. For "starts with", a bare plural (item + s or es) does not count either.
// Accidental matches are fine (ZONE ends in ONE): that is what makes these hard. Items under three letters are left out because they match almost everything.
// To add a list, add one line here. noun = how the category name reads; plain = how the description reads.
const PZ_LISTS = [
  { noun: 'a Greek Letter', plain: 'a Greek letter', items: 'alpha beta gamma delta epsilon zeta eta theta iota kappa lambda rho sigma tau upsilon phi chi psi omega omicron' },
  { noun: 'a NATO Alphabet Word', plain: 'a NATO alphabet word', items: 'alfa bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu' },
  { noun: 'a Weekday', plain: 'a day of the week', items: 'sunday monday tuesday wednesday thursday friday saturday' },
  { noun: 'a Weekday Abbreviation', plain: 'a three-letter weekday abbreviation', items: 'sun mon tue wed thu fri sat' },
  { noun: 'a Month', plain: 'a month', items: 'january february march april may june july august september october november december' },
  { noun: 'a Planet', plain: 'a planet', items: 'mercury venus earth mars jupiter saturn uranus neptune' },
  { noun: 'a Chess Piece', plain: 'a chess piece', items: 'king queen rook bishop knight pawn' },
  { noun: 'a Card Suit', plain: 'a card suit', items: 'heart spade club diamond' },
  { noun: 'an Ace or Court Card', plain: 'an ace or court card', items: 'ace king queen jack' },
  { noun: 'a Number Word', plain: 'a number from one to twelve', items: 'one two three four five six seven eight nine ten eleven twelve' },
  { noun: 'a Large Number Word', plain: 'a tens or big number word', items: 'twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion' },
  { noun: 'an Ordinal Number', plain: 'an ordinal number from first to tenth', items: 'first second third fourth fifth sixth seventh eighth ninth tenth' },
  { noun: 'a Rainbow Color', plain: 'a rainbow color', items: 'red orange yellow green blue indigo violet' },
  { noun: 'a Classical Element', plain: 'a classical element', items: 'earth air fire water' },
  { noun: 'a Compass Direction', plain: 'a compass direction', items: 'north south east west' },
  { noun: 'a Season', plain: 'a season', items: 'spring summer autumn winter fall' },
  { noun: 'a Sense', plain: 'one of the five senses', items: 'sight hearing taste smell touch' },
  { noun: 'a Continent', plain: 'a continent', items: 'africa asia europe antarctica australia america' },
  { noun: 'an Ocean', plain: 'an ocean', items: 'atlantic pacific indian arctic southern' },
  { noun: 'a Great Lake', plain: 'a Great Lake', items: 'huron ontario michigan erie superior' },
  { noun: 'a Noble Gas', plain: 'a noble gas', items: 'helium neon argon krypton xenon radon' },
  { noun: 'a US Coin', plain: 'a US coin', items: 'penny nickel dime quarter' },
  { noun: 'a Greek God', plain: 'a Greek god', items: 'zeus hera poseidon demeter athena apollo artemis ares hermes hephaestus aphrodite dionysus' },
  { noun: 'a Roman God', plain: 'a Roman god', items: 'jupiter juno neptune ceres minerva mars diana vulcan mercury venus bacchus vesta cupid janus' },
  { noun: 'a Metal', plain: 'a metal', items: 'gold silver copper iron tin lead zinc nickel' },
  { noun: 'a Gemstone', plain: 'a gemstone', items: 'ruby emerald sapphire diamond opal pearl topaz garnet amber jade amethyst quartz onyx' },
  { noun: 'a Time of Day', plain: 'a time of day', items: 'dawn noon dusk midnight' },
];

function pzListRules(list) {
  const items = [...new Set(list.items.split(' '))];
  const make = (where, name, desc) => ({
    name, level: 3, desc,
    find(t) {
      const n = pzNorm(t), spans = [];
      for (const item of items) {
        if (n.length <= item.length) continue;
        for (let i = 0; i + item.length <= n.length; i++) {
          if (!n.startsWith(item, i)) continue;
          const end = i + item.length, rest = n.slice(end);
          if (where === 'start' && (i > 0 || rest === 's' || rest === 'es')) continue;
          if (where === 'end' && end !== n.length) continue;
          if (where === 'inside' && (i === 0 || end === n.length)) continue;
          spans.push([i, end]);
        }
      }
      return spans.length ? spans : null;
    },
  });
  return [
    make('start', 'Starts With ' + list.noun, `Words that begin with the name of ${list.plain}. The name is underlined.`),
    make('end', 'Ends With ' + list.noun, `Words that end with the name of ${list.plain}. The name is underlined.`),
    make('inside', 'Hides ' + list.noun, `Words with the name of ${list.plain} hidden inside, not at the start or the end. The name is underlined.`),
  ];
}

const PUZZLE_RULES = PZ_HAND_RULES.concat(PZ_LISTS.flatMap(pzListRules));

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
