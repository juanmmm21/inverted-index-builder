# inverted-index-builder

**Project 3/10 of the [`beacon-search-engine`](https://github.com/juanmmm21/beacon-search-engine) ecosystem** — *Indexing* category.
Repository: [`github.com/juanmmm21/inverted-index-builder`](https://github.com/juanmmm21/inverted-index-builder)

The core data structure of the whole search engine: it takes the clean documents
produced by [`html-content-extractor`](https://github.com/juanmmm21/html-content-extractor)
and builds an inverted index — for every term in the vocabulary, the ordered list of
documents it appears in, how many times, and at which token positions. No `whoosh`,
no `Lucene`, no third-party indexing library: the Unicode-aware tokenizer, the
positional postings structure and the on-disk index format are all implemented from
scratch.

## What problem it solves

Answering "which documents contain this word" by scanning every document at query
time doesn't scale past a handful of pages. An inverted index flips the relationship
once, at indexing time, so that at query time each term resolves directly to its
postings list — no per-document text scan. Positions inside each posting are what
make phrase queries ("machine learning", not just "machine" and "learning"
separately) and proximity scoring possible later in the pipeline, instead of only
supporting single-term lookups. This project is exactly that layer: turn a stream of
extracted documents into a term-indexed, position-aware structure that every ranking
module downstream can query directly, without re-reading or re-tokenizing raw text.

## Role in `beacon-search-engine`

```text
web-crawler-scheduler → html-content-extractor
                                │ documents.jsonl (url, title, main_text, ...)
                                ▼
                    ┌───────────────────────────┐
                    │  inverted-index-builder    │   (this project)
                    │  documents → term index    │
                    └─────────────┬──────────────┘
                                  │ index/ (documents.jsonl, postings.jsonl, stats.json)
                                  ▼
                    index-compression-codec
                                  │
                                  ▼
   bm25-ranking-engine ── query-parser-autocomplete ── distributed-index-sharding
                                  │
                                  ▼
                       (converges in beacon-search-console)
```

Together with `html-content-extractor`, this closes the ingestion-to-index pipeline:
every ranking and query-serving module in the ecosystem (`bm25-ranking-engine`,
`query-parser-autocomplete`, `distributed-index-sharding`) reads the index this
project produces, directly or through `index-compression-codec`.

## Goal and skills demonstrated

- A from-scratch, Unicode-aware tokenizer: NFC normalization so that two differently
  encoded representations of the same accented word don't fragment the vocabulary,
  and `casefold()` (not `lower()`) so locale-sensitive case folding — German `ß`,
  Turkish dotted/dotless `i`, and similar edge cases — doesn't silently split terms
  either.
- Positional postings, not just term-in-document booleans: every occurrence of a
  term keeps its ordinal position inside the document, which is the exact
  information phrase-query support and proximity scoring need later in the pipeline.
- Deterministic, reproducible index construction: document IDs are the order of
  appearance in the input file, term postings are sorted by ascending `doc_id`, and
  building the same corpus twice always produces byte-identical output — this is
  what makes ranking bugs debuggable rather than "sometimes reproducible."
- A documented, self-describing on-disk index format (manifest + JSONL + JSON
  stats) designed to be read by other independent repositories without importing
  this project's Python code.
- Global corpus statistics (vocabulary size, average document length) computed once
  at build time, because `bm25-ranking-engine` needs them for length normalization
  and cannot recompute them per query.

## How it works

1. **Reading** (`pipeline.py`): `documents.jsonl` is read line by line; each line's
   `0`-based position in the file becomes that document's `doc_id`, deterministically
   and independently of any dictionary iteration order.
2. **Tokenization** (`tokenizer.py`): each document's `main_text` is normalized to
   Unicode NFC, split into maximal runs of Unicode letters/digits (punctuation,
   whitespace and symbols are separators), and case-folded. Each resulting token
   carries its ordinal position (`0`, `1`, `2`, ...) within that document.
3. **Postings aggregation** (`pipeline.py`): for every token, its position is
   appended to that term's postings for the current `doc_id`. Once a term has been
   seen in a document, its term frequency there is simply the number of positions
   recorded.
4. **Finalization**: once the whole file has been consumed, each term's postings are
   sorted by ascending `doc_id` — required by `index-compression-codec` downstream
   for delta encoding — and each document's length (token count) and the corpus-wide
   stats (vocabulary size, total postings, average document length) are computed.
5. **Serialization** (`serialization.py`): the resulting `InvertedIndex` is written
   to a directory as a manifest plus three files, all deterministically ordered (see
   *Data formats* below).

## Architecture

```text
src/inverted_index_builder/
├── models.py         # Token, Posting, PostingsList, DocumentRecord, IndexStats, InvertedIndex
├── protocols.py      # Tokenizer interface
├── tokenizer.py       # from-scratch Unicode-aware tokenizer (NFC + casefold)
├── pipeline.py         # IndexBuilder: orchestrates tokenization + postings aggregation
├── serialization.py      # on-disk index format: write_index / read_index
└── __main__.py             # CLI (`build`, `stats`, `lookup`)
```

## Requirements and installation

- Python `>=3.11`
- No runtime dependencies — tokenization, postings aggregation and serialization are
  all built on the standard library.

```bash
git clone https://github.com/juanmmm21/inverted-index-builder.git
cd inverted-index-builder
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage (CLI)

```bash
# Build an index from a documents.jsonl produced by html-content-extractor
inverted-index-builder build ./extracted/documents.jsonl --output-dir ./index

# Quick stats on an already-built index
inverted-index-builder stats ./index

# Inspect the postings of a single term — doc_id, term frequency, positions, url
inverted-index-builder lookup ./index search
```

Try it end-to-end against the tiny fixture checked into `samples/tiny_example/`
(three hand-written documents sharing enough vocabulary — "search", "python",
"inverted index" — to produce real multi-document postings):

```bash
inverted-index-builder build samples/tiny_example/documents.jsonl --output-dir /tmp/index-example
inverted-index-builder stats /tmp/index-example
inverted-index-builder lookup /tmp/index-example search
```

It can also be invoked as a module: `python -m inverted_index_builder build ...`.

## Data formats

**Input** — `documents.jsonl`, produced by `html-content-extractor`: one JSON object
per line, at minimum `url`, `title` and `main_text` (see that project's README for
the full schema). Every other field is ignored.

**Output** — `--output-dir` produces an index directory with four files:

**`manifest.json`** — format version and file names, read first so a future
incompatible format change fails loudly instead of being parsed silently wrong:

```json
{
  "format_version": 1,
  "documents_file": "documents.jsonl",
  "postings_file": "postings.jsonl",
  "stats_file": "stats.json"
}
```

**`documents.jsonl`** — one line per document, ordered by ascending `doc_id`:

```json
{"doc_id": 0, "url": "http://example.com/article", "title": "A Long Article About Search", "length": 214}
```

`length` is the number of indexed tokens in the document (not characters), the value
`bm25-ranking-engine` needs for document-length normalization.

**`postings.jsonl`** — one line per term, ordered alphabetically by Unicode
codepoint; postings inside each line are ordered by ascending `doc_id`:

```json
{
  "term": "search",
  "document_frequency": 2,
  "postings": [
    {"doc_id": 0, "term_frequency": 1, "positions": [3]},
    {"doc_id": 5, "term_frequency": 2, "positions": [0, 47]}
  ]
}
```

The ascending-`doc_id` order inside each posting list is a hard contract, not an
implementation detail: `index-compression-codec` relies on it to delta-encode
document IDs.

**`stats.json`** — corpus-wide statistics, needed by `bm25-ranking-engine`:

```json
{
  "total_documents": 128,
  "vocabulary_size": 4213,
  "total_postings": 51820,
  "average_document_length": 187.5
}
```

## Programmatic usage

```python
from pathlib import Path

from inverted_index_builder.pipeline import IndexBuilder
from inverted_index_builder.serialization import write_index, read_index

index = IndexBuilder().build(Path("./extracted/documents.jsonl"))
write_index(index, Path("./index"))

# ... later, in another process:
index = read_index(Path("./index"))
postings = index.postings_lists["search"]
print(postings.document_frequency, [p.doc_id for p in postings.postings])
```

## Development

```bash
pytest
ruff check .
mypy --strict src/
```

Tests cover the domain models (JSON round-trips), the tokenizer (Unicode NFC
equivalence between composed and decomposed accented forms, locale-aware case
folding, alphanumeric token boundaries), the index builder (an exact expected
postings structure for a small fixed corpus, deterministic rebuilds, document length
and corpus-wide statistics), the on-disk serialization format (ordering guarantees,
manifest version checking) and the CLI.

## Troubleshooting

- **A term I expect to find isn't in the index:** only `main_text` is tokenized and
  indexed — `title` is stored as document metadata for display but isn't part of the
  searchable vocabulary in this project. Title-boosted ranking is a concern for
  `bm25-ranking-engine`, not for index construction.
- **Rebuilding from the same `documents.jsonl` gives a different `doc_id` for a URL
  I expected:** `doc_id` is strictly the line order of the input file. If the
  upstream `html-content-extractor` run reordered or deduplicated differently
  between runs, the IDs will shift accordingly — this project doesn't re-derive IDs
  from URL hashes, by design, to keep ID assignment a pure function of input order.
- **`read_index` raises `ValueError: formato de índice no soportado`:** the
  `manifest.json` in that directory declares a `format_version` this version of the
  library doesn't know how to read. Rebuild the index with a matching version of
  `inverted-index-builder`.
- **`mypy` fails under `tests/`:** expected — only `src/` is type-checked in
  `--strict` mode; test fixtures use deliberately looser typing.

## License

MIT — see [`LICENSE`](./LICENSE).
