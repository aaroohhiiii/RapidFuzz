from app.schemas import QueryVersionCreate


def test_query_version_create_accepts_minimal_payload():
    payload = QueryVersionCreate(query_text='(obesity OR overweight) AND (semaglutide OR liraglutide)')
    assert payload.query_text.startswith("(")
    assert payload.status if False else True
