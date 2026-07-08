import json
from pathlib import Path

import pytest

from inverted_index_builder.__main__ import _build_parser, _run_build, _run_lookup, _run_stats


def _write_documents_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )


class TestArgumentParsing:
    def test_build_requires_output_dir(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["build", "documents.jsonl"])

    def test_lookup_requires_a_term(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["lookup", "index_dir"])

    def test_unknown_command_exits(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["bogus"])


class TestRunBuild:
    def test_reports_missing_documents_file(self, tmp_path: Path) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["build", str(tmp_path / "missing.jsonl"), "--output-dir", str(tmp_path / "out")]
        )

        assert _run_build(args) == 2

    def test_builds_index_and_writes_expected_files(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        documents_path = tmp_path / "documents.jsonl"
        _write_documents_jsonl(
            documents_path,
            [{"url": "http://a", "title": "A", "main_text": "search engines rank pages"}],
        )
        output_dir = tmp_path / "index"
        parser = _build_parser()
        args = parser.parse_args(
            ["build", str(documents_path), "--output-dir", str(output_dir)]
        )

        exit_code = _run_build(args)

        assert exit_code == 0
        assert (output_dir / "manifest.json").exists()
        assert (output_dir / "documents.jsonl").exists()
        assert (output_dir / "postings.jsonl").exists()
        output = capsys.readouterr().out
        assert "1 documentos" in output
        assert "4 términos" in output


class TestRunStats:
    def test_reports_missing_index_dir(self, tmp_path: Path) -> None:
        parser = _build_parser()
        args = parser.parse_args(["stats", str(tmp_path / "missing")])

        assert _run_stats(args) == 2

    def test_prints_stats_for_an_existing_index(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        documents_path = tmp_path / "documents.jsonl"
        _write_documents_jsonl(
            documents_path,
            [{"url": "http://a", "title": "A", "main_text": "search engines rank pages"}],
        )
        output_dir = tmp_path / "index"
        build_parser = _build_parser()
        _run_build(
            build_parser.parse_args(
                ["build", str(documents_path), "--output-dir", str(output_dir)]
            )
        )

        stats_parser = _build_parser()
        exit_code = _run_stats(stats_parser.parse_args(["stats", str(output_dir)]))

        assert exit_code == 0
        output = capsys.readouterr().out
        assert "Documentos: 1" in output
        assert "Vocabulario: 4" in output


class TestRunLookup:
    def test_prints_postings_for_an_existing_term(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        documents_path = tmp_path / "documents.jsonl"
        _write_documents_jsonl(
            documents_path,
            [{"url": "http://a", "title": "A", "main_text": "search engines rank pages"}],
        )
        output_dir = tmp_path / "index"
        build_parser = _build_parser()
        _run_build(
            build_parser.parse_args(
                ["build", str(documents_path), "--output-dir", str(output_dir)]
            )
        )

        lookup_parser = _build_parser()
        exit_code = _run_lookup(
            lookup_parser.parse_args(["lookup", str(output_dir), "search"])
        )

        assert exit_code == 0
        output = capsys.readouterr().out
        assert "doc_id=0" in output
        assert "url=http://a" in output

    def test_reports_absent_term_without_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        documents_path = tmp_path / "documents.jsonl"
        _write_documents_jsonl(
            documents_path,
            [{"url": "http://a", "title": "A", "main_text": "search engines rank pages"}],
        )
        output_dir = tmp_path / "index"
        build_parser = _build_parser()
        _run_build(
            build_parser.parse_args(
                ["build", str(documents_path), "--output-dir", str(output_dir)]
            )
        )

        lookup_parser = _build_parser()
        exit_code = _run_lookup(
            lookup_parser.parse_args(["lookup", str(output_dir), "nonexistent"])
        )

        assert exit_code == 0
        output = capsys.readouterr().out
        assert "no aparece" in output

    def test_reports_missing_index_dir(self, tmp_path: Path) -> None:
        parser = _build_parser()
        args = parser.parse_args(["lookup", str(tmp_path / "missing"), "search"])

        assert _run_lookup(args) == 2
