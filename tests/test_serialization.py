import json
from pathlib import Path

import pytest

from inverted_index_builder.models import (
    DocumentRecord,
    IndexStats,
    InvertedIndex,
    Posting,
    PostingsList,
)
from inverted_index_builder.serialization import read_index, write_index


def _sample_index() -> InvertedIndex:
    documents = {
        0: DocumentRecord(doc_id=0, url="http://a", title="A", length=4),
        1: DocumentRecord(doc_id=1, url="http://b", title="B", length=4),
    }
    postings_lists = {
        "search": PostingsList(
            term="search",
            document_frequency=2,
            postings=(
                Posting(doc_id=0, term_frequency=1, positions=(0,)),
                Posting(doc_id=1, term_frequency=1, positions=(0,)),
            ),
        ),
        "rank": PostingsList(
            term="rank",
            document_frequency=1,
            postings=(Posting(doc_id=0, term_frequency=1, positions=(2,)),),
        ),
    }
    stats = IndexStats(
        total_documents=2, vocabulary_size=2, total_postings=3, average_document_length=4.0
    )
    return InvertedIndex(documents=documents, postings_lists=postings_lists, stats=stats)


class TestWriteAndReadIndex:
    def test_round_trips_a_full_index_through_disk(self, tmp_path: Path) -> None:
        index = _sample_index()
        output_dir = tmp_path / "index"

        write_index(index, output_dir)
        restored = read_index(output_dir)

        assert restored.documents == index.documents
        assert restored.postings_lists == index.postings_lists
        assert restored.stats == index.stats

    def test_writes_documents_ordered_by_doc_id(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "index"
        write_index(_sample_index(), output_dir)

        lines = (output_dir / "documents.jsonl").read_text(encoding="utf-8").splitlines()
        doc_ids = [json.loads(line)["doc_id"] for line in lines]

        assert doc_ids == sorted(doc_ids)

    def test_writes_terms_in_alphabetical_order(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "index"
        write_index(_sample_index(), output_dir)

        lines = (output_dir / "postings.jsonl").read_text(encoding="utf-8").splitlines()
        terms = [json.loads(line)["term"] for line in lines]

        assert terms == sorted(terms)
        assert terms == ["rank", "search"]

    def test_writes_a_manifest_declaring_the_format_version(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "index"
        write_index(_sample_index(), output_dir)

        manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))

        assert manifest["format_version"] == 1
        assert manifest["documents_file"] == "documents.jsonl"
        assert manifest["postings_file"] == "postings.jsonl"
        assert manifest["stats_file"] == "stats.json"

    def test_rejects_an_incompatible_format_version(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "index"
        write_index(_sample_index(), output_dir)

        manifest_path = output_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["format_version"] = 999
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with pytest.raises(ValueError):
            read_index(output_dir)

    def test_overwrites_a_previously_written_index(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "index"
        write_index(_sample_index(), output_dir)

        smaller_index = InvertedIndex(
            documents={0: DocumentRecord(doc_id=0, url="http://only", title="Only", length=1)},
            postings_lists={
                "only": PostingsList(
                    term="only",
                    document_frequency=1,
                    postings=(Posting(doc_id=0, term_frequency=1, positions=(0,)),),
                )
            },
            stats=IndexStats(
                total_documents=1,
                vocabulary_size=1,
                total_postings=1,
                average_document_length=1.0,
            ),
        )
        write_index(smaller_index, output_dir)
        restored = read_index(output_dir)

        assert restored.documents == smaller_index.documents
        assert restored.postings_lists == smaller_index.postings_lists
