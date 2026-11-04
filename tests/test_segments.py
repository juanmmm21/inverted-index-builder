import json
import threading
from pathlib import Path

import pytest

from inverted_index_builder.pipeline import IndexBuilder
from inverted_index_builder.segments import (
    SEGMENTS_MANIFEST_FILENAME,
    BackgroundMerger,
    SegmentedIndex,
    SegmentedIndexError,
    SegmentsManifest,
    TieredMergePolicy,
    read_deleted_doc_ids,
)
from inverted_index_builder.serialization import read_index


def _doc(url: str, text: str) -> dict[str, object]:
    return {"url": url, "title": url, "main_text": text}


def _live_postings(index_root: Path, tmp_path: Path, term: str) -> list[int]:
    compacted = SegmentedIndex.open(index_root).compact(tmp_path / "compacted")
    postings_list = compacted.postings_lists.get(term)
    return [] if postings_list is None else [p.doc_id for p in postings_list.postings]


class TestAddAndDelete:
    def test_each_batch_is_an_immutable_v1_segment_with_global_doc_ids(
        self, tmp_path: Path
    ) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        assert index.add_documents([_doc("a", "red fish"), _doc("b", "blue fish")]) == [0, 1]
        assert index.add_documents([_doc("c", "green fish")]) == [2]

        manifest = index.manifest
        assert [s.name for s in manifest.segments] == ["seg-000001", "seg-000002"]
        assert manifest.next_doc_id == 3
        second = read_index(tmp_path / "idx" / "seg-000002")
        assert list(second.documents) == [2]

    def test_update_tombstones_previous_version(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        index.add_documents([_doc("a", "old text"), _doc("b", "other")])
        index.add_documents([_doc("a", "new text")])

        first = index.manifest.segments[0]
        assert first.deleted_count == 1
        assert read_deleted_doc_ids(tmp_path / "idx", first) == frozenset({0})
        assert index.live_doc_id("a") == 2
        assert _live_postings(tmp_path / "idx", tmp_path, "old") == []
        assert _live_postings(tmp_path / "idx", tmp_path, "text") == [2]

    def test_duplicate_url_in_batch_keeps_last(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        assert index.add_documents([_doc("a", "first"), _doc("a", "second")]) == [0]
        assert _live_postings(tmp_path / "idx", tmp_path, "second") == [0]

    def test_delete_is_idempotent(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        index.add_documents([_doc("a", "x"), _doc("b", "x")])
        assert index.delete_urls(["a", "missing"]) == 1
        assert index.delete_urls(["a"]) == 0
        assert _live_postings(tmp_path / "idx", tmp_path, "x") == [1]

    def test_reopen_restores_live_documents(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        index.add_documents([_doc("a", "x"), _doc("b", "y")])
        index.delete_urls(["a"])
        reopened = SegmentedIndex.open(tmp_path / "idx")
        assert reopened.live_doc_id("a") is None
        assert reopened.live_doc_id("b") == 1
        assert reopened.add_documents([_doc("c", "z")]) == [2]

    def test_old_tombstone_files_are_removed_after_commit(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        index.add_documents([_doc("a", "x"), _doc("b", "x"), _doc("c", "x")])
        index.delete_urls(["a"])
        index.delete_urls(["b"])
        files = sorted(p.name for p in (tmp_path / "idx" / "deletes").iterdir())
        assert files == [Path(str(index.manifest.segments[0].deletes_file)).name]


class TestManifest:
    def test_create_refuses_existing_index(self, tmp_path: Path) -> None:
        SegmentedIndex.create(tmp_path / "idx")
        with pytest.raises(SegmentedIndexError):
            SegmentedIndex.create(tmp_path / "idx")

    def test_unknown_format_version_fails_loudly(self, tmp_path: Path) -> None:
        SegmentedIndex.create(tmp_path / "idx")
        path = tmp_path / "idx" / SEGMENTS_MANIFEST_FILENAME
        data = json.loads(path.read_text(encoding="utf-8"))
        data["format_version"] = 99
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(SegmentedIndexError, match="no soportado"):
            SegmentsManifest.load(tmp_path / "idx")

    def test_orphans_from_a_crash_are_removed_on_open(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        index.add_documents([_doc("a", "x")])
        (tmp_path / "idx" / "seg-000099").mkdir()
        (tmp_path / "idx" / "deletes").mkdir(exist_ok=True)
        (tmp_path / "idx" / "deletes" / "seg-000001-g000099.json").write_text("{}", "utf-8")
        SegmentedIndex.open(tmp_path / "idx")
        assert not (tmp_path / "idx" / "seg-000099").exists()
        assert list((tmp_path / "idx" / "deletes").iterdir()) == []


class TestMerge:
    def test_merge_expunges_deletes_and_preserves_doc_ids(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        index.add_documents([_doc("a", "fish"), _doc("b", "fish")])
        index.add_documents([_doc("c", "fish")])
        index.delete_urls(["b"])

        report = index.merge(["seg-000001", "seg-000002"])

        assert report.live_documents == 2
        assert report.expunged_documents == 1
        assert [s.name for s in index.manifest.segments] == ["seg-000003"]
        assert not (tmp_path / "idx" / "seg-000001").exists()
        merged = read_index(tmp_path / "idx" / "seg-000003")
        assert [p.doc_id for p in merged.postings_lists["fish"].postings] == [0, 2]
        assert merged.stats.total_documents == 2

    def test_merge_of_fully_deleted_segment_drops_it(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        index.add_documents([_doc("a", "x")])
        index.add_documents([_doc("b", "y")])
        index.delete_urls(["a"])
        report = index.merge(["seg-000001"])
        assert report.into is None
        assert [s.name for s in index.manifest.segments] == ["seg-000002"]

    def test_merged_result_equals_full_rebuild(self, tmp_path: Path) -> None:
        documents = [_doc(f"u{i}", f"term{i % 3} shared word{i}") for i in range(12)]
        index = SegmentedIndex.create(tmp_path / "idx")
        for start in range(0, 12, 3):
            index.add_documents(documents[start : start + 3])
        index.maybe_merge(TieredMergePolicy(merge_factor=2))
        assert len(index.manifest.segments) == 1

        rebuilt = IndexBuilder().build_from_records(documents)
        compacted = index.compact(tmp_path / "out")
        assert compacted.postings_lists == rebuilt.postings_lists
        assert compacted.documents == rebuilt.documents
        assert compacted.stats == rebuilt.stats

    def test_delete_during_merge_is_carried_to_merged_segment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        index.add_documents([_doc("a", "x"), _doc("b", "x")])
        index.add_documents([_doc("c", "x")])

        import inverted_index_builder.segments as segments_module

        original = segments_module._merge_segment_indexes

        def merge_then_delete(sources):  # type: ignore[no-untyped-def]
            merged = original(sources)
            index.delete_urls(["a"])
            return merged

        monkeypatch.setattr(segments_module, "_merge_segment_indexes", merge_then_delete)
        index.merge(["seg-000001", "seg-000002"])
        monkeypatch.setattr(segments_module, "_merge_segment_indexes", original)

        (merged,) = index.manifest.segments
        assert merged.deleted_count == 1
        assert _live_postings(tmp_path / "idx", tmp_path, "x") == [1, 2]

    def test_merge_unknown_segment_fails(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        with pytest.raises(SegmentedIndexError):
            index.merge(["seg-000042"])


class TestTieredMergePolicy:
    def test_merges_when_a_tier_fills_up(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        policy = TieredMergePolicy(merge_factor=3)
        for i in range(2):
            index.add_documents([_doc(f"u{i}", "x")])
        assert policy.find_merge(index.manifest) is None
        index.add_documents([_doc("u2", "x")])
        assert policy.find_merge(index.manifest) == ("seg-000001", "seg-000002", "seg-000003")

    def test_rewrites_segment_with_too_many_deletes(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        index.add_documents([_doc(f"u{i}", "x") for i in range(10)])
        index.delete_urls([f"u{i}" for i in range(4)])
        assert TieredMergePolicy(max_deleted_ratio=0.3).find_merge(index.manifest) == (
            "seg-000001",
        )

    @pytest.mark.parametrize("kwargs", [{"merge_factor": 1}, {"max_deleted_ratio": 0.0}])
    def test_invalid_parameters(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            TieredMergePolicy(**kwargs)  # type: ignore[arg-type]


class TestBackgroundMerger:
    def test_merges_in_background_while_documents_keep_arriving(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        with BackgroundMerger(
            index, TieredMergePolicy(merge_factor=2), interval_seconds=0.01
        ) as merger:
            for i in range(20):
                index.add_documents([_doc(f"u{i}", f"shared t{i}")])
                merger.trigger()
            index.delete_urls(["u3"])
        assert merger.last_error is None
        assert merger.reports
        assert len(index.manifest.segments) < 20
        assert _live_postings(tmp_path / "idx", tmp_path, "shared") == [
            i for i in range(20) if i != 3
        ]

    def test_merge_failure_is_exposed_not_swallowed(self, tmp_path: Path) -> None:
        index = SegmentedIndex.create(tmp_path / "idx")
        done = threading.Event()

        class ExplodingPolicy(TieredMergePolicy):
            def find_merge(self, manifest: SegmentsManifest) -> tuple[str, ...] | None:
                done.set()
                raise RuntimeError("boom")

        merger = BackgroundMerger(index, ExplodingPolicy(), interval_seconds=0.01)
        merger.start()
        merger.trigger()
        assert done.wait(2)
        merger.stop()
        assert isinstance(merger.last_error, RuntimeError)
