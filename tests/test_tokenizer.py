from inverted_index_builder.tokenizer import tokenize


class TestTokenize:
    def test_splits_on_punctuation_and_whitespace(self) -> None:
        tokens = tokenize("Search engines: how do they work?")

        assert [token.text for token in tokens] == [
            "search",
            "engines",
            "how",
            "do",
            "they",
            "work",
        ]
        assert [token.position for token in tokens] == [0, 1, 2, 3, 4, 5]

    def test_is_case_and_locale_fold_insensitive(self) -> None:
        tokens = tokenize("Straße STRASSE")

        assert tokens[0].text == "strasse"
        assert tokens[1].text == "strasse"

    def test_normalizes_nfc_so_composed_and_decomposed_forms_match(self) -> None:
        composed = "café"  # 'e' con acento agudo como un único codepoint precompuesto
        decomposed = "café"  # 'e' + acento agudo combinante como codepoints separados

        assert composed != decomposed  # difieren a nivel de codepoints, no solo de bytes
        assert tokenize(composed)[0].text == tokenize(decomposed)[0].text

    def test_keeps_alphanumeric_sequences_together(self) -> None:
        tokens = tokenize("html5 is not the same token as html")

        assert tokens[0].text == "html5"
        assert tokens[0].text != "html"

    def test_returns_empty_list_for_text_without_indexable_terms(self) -> None:
        assert tokenize("   --- ... !!! ") == []

    def test_positions_are_ordinal_token_indices_not_character_offsets(self) -> None:
        tokens = tokenize("a bb ccc")

        assert [token.position for token in tokens] == [0, 1, 2]
