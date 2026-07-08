"""Orquestador de construcción del índice: lee el `documents.jsonl` producido por
`html-content-extractor`, tokeniza el texto principal de cada documento y acumula
postings por término hasta producir un `InvertedIndex` completo.

La asignación de `doc_id` es el orden de aparición en el fichero de entrada (0-based,
estrictamente creciente): dado el mismo `documents.jsonl`, la construcción siempre
produce los mismos IDs de documento, cumpliendo la garantía de determinismo exigida en
`../CLAUDE.md` (sección 2.B).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from inverted_index_builder.models import (
    DocumentRecord,
    IndexStats,
    InvertedIndex,
    Posting,
    PostingsList,
)
from inverted_index_builder.protocols import Tokenizer
from inverted_index_builder.tokenizer import tokenize as _default_tokenize

# término -> doc_id -> posiciones de aparición, acumulado durante la construcción.
_Occurrences = dict[str, dict[int, list[int]]]


class IndexBuilder:
    """Construye un `InvertedIndex` en memoria a partir de un `documents.jsonl`.

    El tokenizador es inyectable (ver `protocols.Tokenizer`) para poder testear la
    agregación de postings con dobles de prueba deterministas, independientes de las
    reglas Unicode reales de `tokenizer.tokenize`.
    """

    def __init__(self, tokenizer: Tokenizer = _default_tokenize) -> None:
        self._tokenizer = tokenizer

    def build(self, documents_path: Path) -> InvertedIndex:
        documents: dict[int, DocumentRecord] = {}
        occurrences: _Occurrences = defaultdict(dict)

        doc_id = 0
        with documents_path.open("r", encoding="utf-8") as documents_file:
            for raw_line in documents_file:
                line = raw_line.strip()
                if not line:
                    continue
                record = json.loads(line)
                self._index_document(doc_id, record, documents, occurrences)
                doc_id += 1

        postings_lists = self._finalize_postings(occurrences)
        stats = self._compute_stats(documents, postings_lists)
        return InvertedIndex(documents=documents, postings_lists=postings_lists, stats=stats)

    def _index_document(
        self,
        doc_id: int,
        record: dict[str, Any],
        documents: dict[int, DocumentRecord],
        occurrences: _Occurrences,
    ) -> None:
        main_text = str(record["main_text"])
        tokens = self._tokenizer(main_text)

        for token in tokens:
            positions = occurrences[token.text].setdefault(doc_id, [])
            positions.append(token.position)

        documents[doc_id] = DocumentRecord(
            doc_id=doc_id,
            url=str(record["url"]),
            title=str(record["title"]),
            length=len(tokens),
        )

    def _finalize_postings(self, occurrences: _Occurrences) -> dict[str, PostingsList]:
        postings_lists: dict[str, PostingsList] = {}
        for term, positions_by_doc in occurrences.items():
            postings = tuple(
                Posting(
                    doc_id=doc_id,
                    term_frequency=len(positions_by_doc[doc_id]),
                    positions=tuple(positions_by_doc[doc_id]),
                )
                for doc_id in sorted(positions_by_doc)
            )
            postings_lists[term] = PostingsList(
                term=term,
                document_frequency=len(postings),
                postings=postings,
            )
        return postings_lists

    def _compute_stats(
        self,
        documents: dict[int, DocumentRecord],
        postings_lists: dict[str, PostingsList],
    ) -> IndexStats:
        total_documents = len(documents)
        total_postings = sum(
            len(postings_list.postings) for postings_list in postings_lists.values()
        )
        average_length = (
            sum(record.length for record in documents.values()) / total_documents
            if total_documents > 0
            else 0.0
        )
        return IndexStats(
            total_documents=total_documents,
            vocabulary_size=len(postings_lists),
            total_postings=total_postings,
            average_document_length=average_length,
        )
