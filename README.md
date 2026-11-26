# dictionary-versions

Home of the UNICYCLIST DICTIONARY: the dictionary file, its word lists, and the code that builds its website.

## Files

- `UNICYCLIST DICTIONARY.txt` is the dictionary itself. The first line holds its version (v + the number of words), and the Discord bot and the wiki editor both save changes straight into it.
- `Zoogliography.txt` is the list of zoogs, plus the predicted next word, nonsense word, and rejected guesses written by the stats script.
- `178,691 Scrabble Legal Words.txt` is the Scrabble word list used for legal-word checks and the word games.
- `Entry_Messages.json` remembers which Discord message added which word, so editing the message can update the word.
- `nudge_state.json` is the bot's reminder state.

## Folders

- `site/` is the GitHub Pages website: the stats page (`index.html`), the Wiktionary-style page with editing (`wiktionary.html`), the word game (`wordle.html`), and their data files.
- `scripts/generate_stats.py` builds `site/stats.json` and the word game lists from the dictionary and the commit history.
- `.github/workflows/dictionary-stats.yml` runs that script and publishes the site whenever something is pushed to `main`.
- `archive/` holds the old numbered versions (`UNICYCLIST DICTIONARY v1.2.N.txt`) from before the dictionary became a single file.

## History

Every change to the dictionary is a commit, so any earlier version can be seen from the History button on `UNICYCLIST DICTIONARY.txt` (older ones are also in `archive/`).
