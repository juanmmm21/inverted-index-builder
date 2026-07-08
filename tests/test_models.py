from inverted_index_builder.models import (
    DocumentRecord,
    IndexStats,
    Posting,
    PostingsList,
)


class TestPostingJsonRoundTrip:
    def test_round_trips_through_json_dict(self) -> None:
        posting = Posting(doc_id=3, term_frequency=2, positions=(5, 19))

        restored = Posting.from_json_dict(posting.to_json_dict())

        assert restored == posting


class TestPostingsListJsonRoundTrip:
    def test_round_trips_through_json_dict(self) -> None:
        postings_list = PostingsList(
            term="search",
            document_frequency=1,
            postings=(Posting(doc_id=0, term_frequency=1, positions=(2,)),),
        )

        restored = PostingsList.from_json_dict(postings_list.to_json_dict())

        assert restored == postings_list

    def test_round_trips_an_empty_postings_list(self) -> None:
        postings_list = PostingsList(term="ghost", document_frequency=0, postings=())

        restored = PostingsList.from_json_dict(postings_list.to_json_dict())

        assert restored == postings_list


class TestDocumentRecordJsonRoundTrip:
    def test_round_trips_through_json_dict(self) -> None:
        record = DocumentRecord(doc_id=1, url="http://example.com", title="Example", length=42)

        restored = DocumentRecord.from_json_dict(record.to_json_dict())

        assert restored == record


class TestIndexStatsJsonRoundTrip:
    def test_round_trips_through_json_dict(self) -> None:
        stats = IndexStats(
            total_documents=2,
            vocabulary_size=10,
            total_postings=15,
            average_document_length=37.5,
        )

        restored = IndexStats.from_json_dict(stats.to_json_dict())

        assert restored == stats
