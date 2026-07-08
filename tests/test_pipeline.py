import json
from pathlib import Path

from inverted_index_builder.pipeline import IndexBuilder


def _write_documents_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )


class TestIndexBuilderBuild:
    def test_assigns_doc_ids_in_order_of_appearance(self, tmp_path: Path) -> None:
        documents_path = tmp_path / "documents.jsonl"
        _write_documents_jsonl(
            documents_path,
            [
                {"url": "http://a", "title": "A", "main_text": "search engines rank pages"},
                {"url": "http://b", "title": "B", "main_text": "search engines index pages"},
            ],
        )

        index = IndexBuilder().build(documents_path)

        assert index.documents[0].url == "http://a"
        assert index.documents[1].url == "http://b"

    def test_produces_exact_expected_postings_for_a_fixed_corpus(self, tmp_path: Path) -> None:
        documents_path = tmp_path / "documents.jsonl"
        _write_documents_jsonl(
            documents_path,
            [
                {"url": "http://a", "title": "A", "main_text": "search engines rank pages"},
                {"url": "http://b", "title": "B", "main_text": "search engines index pages"},
            ],
        )

        index = IndexBuilder().build(documents_path)

        search_postings = index.postings_lists["search"]
        assert search_postings.document_frequency == 2
        assert [posting.doc_id for posting in search_postings.postings] == [0, 1]
        assert all(posting.term_frequency == 1 for posting in search_postings.postings)
        assert all(posting.positions == (0,) for posting in search_postings.postings)

        rank_postings = index.postings_lists["rank"]
        assert rank_postings.document_frequency == 1
        assert rank_postings.postings[0].doc_id == 0
        assert rank_postings.postings[0].positions == (2,)

        assert index.postings_lists["index"].document_frequency == 1
        assert "pages" in index.postings_lists
        assert index.postings_lists["pages"].document_frequency == 2

    def test_computes_document_length_and_average(self, tmp_path: Path) -> None:
        documents_path = tmp_path / "documents.jsonl"
        _write_documents_jsonl(
            documents_path,
            [
                {"url": "http://a", "title": "A", "main_text": "one two three"},
                {"url": "http://b", "title": "B", "main_text": "one two three four five"},
            ],
        )

        index = IndexBuilder().build(documents_path)

        assert index.documents[0].length == 3
        assert index.documents[1].length == 5
        assert index.stats.average_document_length == 4.0
        assert index.stats.total_documents == 2
        assert index.stats.vocabulary_size == 5

    def test_repeated_term_accumulates_term_frequency_and_positions(
        self, tmp_path: Path
    ) -> None:
        documents_path = tmp_path / "documents.jsonl"
        _write_documents_jsonl(
            documents_path,
            [{"url": "http://a", "title": "A", "main_text": "search for search engines"}],
        )

        index = IndexBuilder().build(documents_path)

        posting = index.postings_lists["search"].postings[0]
        assert posting.term_frequency == 2
        assert posting.positions == (0, 2)

    def test_building_twice_from_the_same_input_is_deterministic(self, tmp_path: Path) -> None:
        documents_path = tmp_path / "documents.jsonl"
        _write_documents_jsonl(
            documents_path,
            [
                {"url": "http://a", "title": "A", "main_text": "search engines rank pages"},
                {"url": "http://b", "title": "B", "main_text": "search engines index pages"},
            ],
        )

        first = IndexBuilder().build(documents_path)
        second = IndexBuilder().build(documents_path)

        assert first.documents == second.documents
        assert first.postings_lists == second.postings_lists
        assert first.stats == second.stats

    def test_skips_blank_lines_in_the_input_file(self, tmp_path: Path) -> None:
        documents_path = tmp_path / "documents.jsonl"
        record = {"url": "http://a", "title": "A", "main_text": "a single document"}
        documents_path.write_text(
            "\n" + json.dumps(record) + "\n\n \n" + json.dumps(record) + "\n",
            encoding="utf-8",
        )

        index = IndexBuilder().build(documents_path)

        assert index.stats.total_documents == 2
        assert index.documents[0].doc_id == 0
        assert index.documents[1].doc_id == 1

    def test_empty_input_produces_an_empty_index(self, tmp_path: Path) -> None:
        documents_path = tmp_path / "documents.jsonl"
        documents_path.write_text("", encoding="utf-8")

        index = IndexBuilder().build(documents_path)

        assert index.stats.total_documents == 0
        assert index.stats.vocabulary_size == 0
        assert index.stats.average_document_length == 0.0
