"""Tipos de datos del dominio para inverted-index-builder.

`Posting`, `PostingsList` y `DocumentRecord` son inmutables (`frozen=True`) porque
representan el artefacto final serializado a disco y consumido por
`index-compression-codec`, `bm25-ranking-engine` y `query-parser-autocomplete`:
mutarlos tras construir el índice rompería la garantía de determinismo exigida en
`../CLAUDE.md` (sección 2.B). `InvertedIndex` envuelve diccionarios mutables por
diseño (acceso aleatorio por `doc_id`/término), así que no se marca `frozen`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Token:
    """Un término normalizado y su posición ordinal dentro del texto tokenizado.

    La posición es un índice de token (0-based), no un offset de carácter: es lo
    que necesitan las búsquedas de frases exactas para comprobar adyacencia entre
    términos consecutivos.
    """

    text: str
    position: int


@dataclass(frozen=True, slots=True)
class Posting:
    """Aparición de un término en un documento concreto: frecuencia y posiciones."""

    doc_id: int
    term_frequency: int
    positions: tuple[int, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "term_frequency": self.term_frequency,
            "positions": list(self.positions),
        }

    @staticmethod
    def from_json_dict(data: dict[str, Any]) -> Posting:
        return Posting(
            doc_id=int(data["doc_id"]),
            term_frequency=int(data["term_frequency"]),
            positions=tuple(int(position) for position in data["positions"]),
        )


@dataclass(frozen=True, slots=True)
class PostingsList:
    """Lista de postings de un término, ordenada por `doc_id` ascendente.

    El orden ascendente por `doc_id` no es cosmético: es el que necesita
    `index-compression-codec` para aplicar delta encoding sobre los IDs de
    documento (ver `../CLAUDE.md`, sección 2.E).
    """

    term: str
    document_frequency: int
    postings: tuple[Posting, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "document_frequency": self.document_frequency,
            "postings": [posting.to_json_dict() for posting in self.postings],
        }

    @staticmethod
    def from_json_dict(data: dict[str, Any]) -> PostingsList:
        return PostingsList(
            term=str(data["term"]),
            document_frequency=int(data["document_frequency"]),
            postings=tuple(Posting.from_json_dict(entry) for entry in data["postings"]),
        )


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """Metadatos mínimos de un documento indexado, dirigidos a ranking y presentación.

    `length` es el número de tokens indexados del documento (no de caracteres):
    es el que necesita `bm25-ranking-engine` para la normalización por longitud.
    """

    doc_id: int
    url: str
    title: str
    length: int

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "url": self.url,
            "title": self.title,
            "length": self.length,
        }

    @staticmethod
    def from_json_dict(data: dict[str, Any]) -> DocumentRecord:
        return DocumentRecord(
            doc_id=int(data["doc_id"]),
            url=str(data["url"]),
            title=str(data["title"]),
            length=int(data["length"]),
        )


@dataclass(frozen=True, slots=True)
class IndexStats:
    """Estadísticas globales del índice, necesarias para BM25 (ver `bm25-ranking-engine`)."""

    total_documents: int
    vocabulary_size: int
    total_postings: int
    average_document_length: float

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "total_documents": self.total_documents,
            "vocabulary_size": self.vocabulary_size,
            "total_postings": self.total_postings,
            "average_document_length": self.average_document_length,
        }

    @staticmethod
    def from_json_dict(data: dict[str, Any]) -> IndexStats:
        return IndexStats(
            total_documents=int(data["total_documents"]),
            vocabulary_size=int(data["vocabulary_size"]),
            total_postings=int(data["total_postings"]),
            average_document_length=float(data["average_document_length"]),
        )


@dataclass(slots=True)
class InvertedIndex:
    """Índice invertido completo en memoria: documentos y postings indexados por clave.

    `documents` se indexa por `doc_id` y `postings_lists` por término, para dar acceso
    aleatorio O(1) tanto a la construcción (`pipeline.py`) como a la serialización
    (`serialization.py`) y a consultas puntuales de depuración (CLI `lookup`).
    """

    documents: dict[int, DocumentRecord]
    postings_lists: dict[str, PostingsList]
    stats: IndexStats
