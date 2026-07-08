"""CLI de `inverted-index-builder`: `build` (construye el índice desde un
`documents.jsonl`), `stats` (estadísticas de un índice ya construido) y `lookup`
(postings de un término concreto, para verificar manualmente el soporte de frases
exactas sin tener que esperar a `bm25-ranking-engine`).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from inverted_index_builder.pipeline import IndexBuilder
from inverted_index_builder.serialization import read_index, write_index


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inverted-index-builder",
        description="Construye un índice invertido a partir de documentos extraídos.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build", help="Construye el índice desde un documents.jsonl"
    )
    build_parser.add_argument(
        "documents_path",
        type=Path,
        help="documents.jsonl producido por html-content-extractor",
    )
    build_parser.add_argument("--output-dir", type=Path, required=True)

    stats_parser = subparsers.add_parser("stats", help="Muestra estadísticas de un índice")
    stats_parser.add_argument("index_dir", type=Path)

    lookup_parser = subparsers.add_parser(
        "lookup", help="Muestra los postings de un término concreto"
    )
    lookup_parser.add_argument("index_dir", type=Path)
    lookup_parser.add_argument("term", type=str)

    return parser


def _run_build(args: argparse.Namespace) -> int:
    documents_path: Path = args.documents_path
    if not documents_path.exists():
        print(f"Error: no existe {documents_path}", file=sys.stderr)
        return 2

    index = IndexBuilder().build(documents_path)
    write_index(index, args.output_dir)

    print(
        f"Índice construido: {index.stats.total_documents} documentos, "
        f"{index.stats.vocabulary_size} términos, "
        f"{index.stats.total_postings} postings, "
        f"longitud media {index.stats.average_document_length:.1f} tokens"
    )
    return 0


def _run_stats(args: argparse.Namespace) -> int:
    index_dir: Path = args.index_dir
    if not index_dir.exists():
        print(f"Error: no existe {index_dir}", file=sys.stderr)
        return 2

    index = read_index(index_dir)
    print(f"Documentos: {index.stats.total_documents}")
    print(f"Vocabulario: {index.stats.vocabulary_size}")
    print(f"Postings totales: {index.stats.total_postings}")
    print(f"Longitud media de documento: {index.stats.average_document_length:.1f} tokens")
    return 0


def _run_lookup(args: argparse.Namespace) -> int:
    index_dir: Path = args.index_dir
    if not index_dir.exists():
        print(f"Error: no existe {index_dir}", file=sys.stderr)
        return 2

    index = read_index(index_dir)
    postings_list = index.postings_lists.get(args.term)
    if postings_list is None:
        print(f"'{args.term}' no aparece en el índice")
        return 0

    print(f"'{postings_list.term}' — document_frequency={postings_list.document_frequency}")
    for posting in postings_list.postings:
        document = index.documents.get(posting.doc_id)
        url = document.url if document is not None else "?"
        print(
            f"  doc_id={posting.doc_id} tf={posting.term_frequency} "
            f"positions={list(posting.positions)} url={url}"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "build":
        return _run_build(args)
    if args.command == "stats":
        return _run_stats(args)
    if args.command == "lookup":
        return _run_lookup(args)

    parser.error(f"Comando desconocido: {args.command}")
    return 2  # inalcanzable: parser.error() termina el proceso con sys.exit()


if __name__ == "__main__":
    sys.exit(main())
