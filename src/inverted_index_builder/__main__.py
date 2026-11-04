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

from inverted_index_builder.pipeline import IndexBuilder, read_documents
from inverted_index_builder.segments import (
    SegmentedIndex,
    SegmentedIndexError,
    TieredMergePolicy,
)
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

    segments_parser = subparsers.add_parser(
        "segments", help="Índice segmentado incremental (altas, bajas, fusiones)"
    )
    segments_sub = segments_parser.add_subparsers(dest="segments_command", required=True)
    add_parser = segments_sub.add_parser("add", help="Añade o actualiza documentos")
    add_parser.add_argument("root", type=Path)
    add_parser.add_argument("documents_path", type=Path)
    delete_parser = segments_sub.add_parser("delete", help="Borra documentos por URL")
    delete_parser.add_argument("root", type=Path)
    delete_parser.add_argument("urls", nargs="+")
    merge_parser = segments_sub.add_parser("merge", help="Aplica la política de fusión")
    merge_parser.add_argument("root", type=Path)
    merge_parser.add_argument("--merge-factor", type=int, default=4)
    merge_parser.add_argument("--max-deleted-ratio", type=float, default=0.3)
    compact_parser = segments_sub.add_parser(
        "compact", help="Escribe los documentos vivos como un índice v1 clásico"
    )
    compact_parser.add_argument("root", type=Path)
    compact_parser.add_argument("--output-dir", type=Path, required=True)
    segments_stats_parser = segments_sub.add_parser("stats", help="Segmentos y borrados")
    segments_stats_parser.add_argument("root", type=Path)

    return parser


def _run_segments(args: argparse.Namespace) -> int:
    root: Path = args.root
    try:
        if args.segments_command == "add":
            if not args.documents_path.exists():
                print(f"Error: no existe {args.documents_path}", file=sys.stderr)
                return 2
            index = SegmentedIndex.open_or_create(root)
            doc_ids = index.add_documents(read_documents(args.documents_path))
            print(f"Segmento añadido con {len(doc_ids)} documentos")
            return 0
        index = SegmentedIndex.open(root)
        if args.segments_command == "delete":
            print(f"Documentos borrados: {index.delete_urls(args.urls)}")
        elif args.segments_command == "merge":
            policy = TieredMergePolicy(args.merge_factor, args.max_deleted_ratio)
            for report in index.maybe_merge(policy):
                print(
                    f"{' + '.join(report.merged)} -> {report.into or '(vacío)'}: "
                    f"{report.live_documents} vivos, {report.expunged_documents} purgados"
                )
        elif args.segments_command == "compact":
            compacted = index.compact(args.output_dir)
            print(f"Índice v1 escrito con {compacted.stats.total_documents} documentos")
        else:
            manifest = index.manifest
            print(f"Generación: {manifest.generation}")
            print(f"Documentos vivos: {manifest.live_documents}")
            for segment in manifest.segments:
                print(
                    f"  {segment.name}: {segment.doc_count} docs, {segment.deleted_count} borrados"
                )
    except SegmentedIndexError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    return 0


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
    if args.command == "segments":
        return _run_segments(args)

    parser.error(f"Comando desconocido: {args.command}")
    return 2  # inalcanzable: parser.error() termina el proceso con sys.exit()


if __name__ == "__main__":
    sys.exit(main())
