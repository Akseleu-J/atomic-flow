import pytest

from atomic_flow.activation.text import register_synonyms, stem, synonyms_for, terms


@pytest.mark.parametrize("a,b", [("process", "processes"), ("class", "classes"), ("address", "addresses"),
                                 ("file", "files"), ("rule", "rules"), ("node", "nodes"), ("status", "statuses"),
                                 ("refund", "refundable"), ("cache", "caches"), ("state", "states")])
def test_inflections_share_a_stem(a, b):
    assert stem(a) == stem(b)


def test_synonym_keys_with_trailing_e_fire():
    # v0.2 looked "state" up AFTER stemming it to "stat" -> never matched
    assert {"h0", "hidden"} <= synonyms_for(stem("state"))
    assert synonyms_for(stem("inverse")) and synonyms_for(stem("score"))


def test_expansion_is_query_side_only():
    assert "h0" not in terms("the state of the chunk")
    assert "h0" in terms("the state of the chunk", expand=True)


def test_project_synonyms_are_opt_in():
    assert "dhu" not in synonyms_for("gradient")
    register_synonyms({"gradient": {"dhu"}})
    assert "dhu" in synonyms_for("gradient")


def test_russian_query_gets_english_terms_without_llm():
    t = terms("Как считается скидка и как округляется итог счёта?")
    assert {"discount", "round", "total", "invoic"} <= set(t)
    assert "как" not in t


def test_unknown_russian_word_is_kept_for_docstring_matching():
    assert "устроен" in terms("как устроен пайплайн")


def test_hard_mode_trigger_is_substring_bug_documented():
    # regression for choose_mode(): "review" in "preview" is True -> needs word-boundary regex
    assert "review" in "preview the invoice"
