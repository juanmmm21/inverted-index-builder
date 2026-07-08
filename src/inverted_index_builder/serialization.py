"""Serialización del índice invertido a un formato propio en disco: JSONL para
documentos y postings, JSON para estadísticas agregadas, y un manifiesto con la versión
de formato. Se documenta explícitamente en el README para que `index-compression-codec`
y `bm25-ranking-engine` puedan leerlo sin ambigüedad (ver `../AGENTS.md`, integración).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from inverted_index_builder.models import (
    DocumentRecord,
    IndexStats,
    InvertedIndex,
    PostingsList,
)

FORMAT_VERSION: Final[int] = 1

MANIFEST_FILENAME: Final[str] = "manifest.json"
DOCUMENTS_FILENAME: Final[str] = "documents.jsonl"
POSTINGS_FILENAME: Final[str] = "postings.jsonl"
STATS_FILENAME: Final[str] = "stats.json"


def write_index(index: InvertedIndex, output_dir: Path) -> None:
    """Escribe `index` en `output_dir`, sobreescribiendo cualquier índice previo.

    Los documentos se escriben ordenados por `doc_id` y los términos por orden
    alfabético de codepoint Unicode: ningún orden depende de la iteración de un `dict`
    no determinista, cumpliendo la garantía de reproducibilidad exigida en
    `../CLAUDE.md` (sección 2.B).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    documents_path = output_dir / DOCUMENTS_FILENAME
    with documents_path.open("w", encoding="utf-8") as documents_file:
        for doc_id in sorted(index.documents):
            record = index.documents[doc_id]
            documents_file.write(json.dumps(record.to_json_dict(), ensure_ascii=False) + "\n")

    postings_path = output_dir / POSTINGS_FILENAME
    with postings_path.open("w", encoding="utf-8") as postings_file:
        for term in sorted(index.postings_lists):
            postings_list = index.postings_lists[term]
            postings_file.write(
                json.dumps(postings_list.to_json_dict(), ensure_ascii=False) + "\n"
            )

    stats_path = output_dir / STATS_FILENAME
    stats_path.write_text(
        json.dumps(index.stats.to_json_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest = {
        "format_version": FORMAT_VERSION,
        "documents_file": DOCUMENTS_FILENAME,
        "postings_file": POSTINGS_FILENAME,
        "stats_file": STATS_FILENAME,
    }
    (output_dir / MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def read_index(input_dir: Path) -> InvertedIndex:
    """Reconstruye un `InvertedIndex` completo a partir de un directorio escrito por
    `write_index`. Falla explícitamente si el manifiesto declara un `format_version`
    que esta versión de la librería no sabe leer, en vez de intentar parsear un
    formato incompatible en silencio.
    """
    manifest_path = input_dir / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    format_version = int(manifest["format_version"])
    if format_version != FORMAT_VERSION:
        raise ValueError(
            f"formato de índice no soportado: version {format_version}, "
            f"esperado {FORMAT_VERSION}"
        )

    documents: dict[int, DocumentRecord] = {}
    documents_path = input_dir / str(manifest["documents_file"])
    for line in documents_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = DocumentRecord.from_json_dict(json.loads(line))
        documents[record.doc_id] = record

    postings_lists: dict[str, PostingsList] = {}
    postings_path = input_dir / str(manifest["postings_file"])
    for line in postings_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        postings_list = PostingsList.from_json_dict(json.loads(line))
        postings_lists[postings_list.term] = postings_list

    stats_path = input_dir / str(manifest["stats_file"])
    stats = IndexStats.from_json_dict(json.loads(stats_path.read_text(encoding="utf-8")))

    return InvertedIndex(documents=documents, postings_lists=postings_lists, stats=stats)
