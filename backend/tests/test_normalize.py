from app.services.normalize import normalize_doi, normalize_title


def test_normalize_doi_removes_scheme_and_whitespace():
    assert normalize_doi(" https://doi.org/10.1000/example ") == "10.1000/example"


def test_normalize_title_strips_punctuation_and_lowercases():
    assert normalize_title("A Randomized Trial of GLP-1 Therapy!") == "a randomized trial of glp 1 therapy"
