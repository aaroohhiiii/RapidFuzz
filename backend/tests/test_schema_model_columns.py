from app.models import QueryHit, QueryRecord, QueryVersion, Record, RunLog


EXPECTED_COLUMNS = {
    QueryVersion: [
        "id", "parent_id", "label", "query_text", "query_struct", "date_from",
        "date_to", "status", "created_by", "created_at",
    ],
    QueryHit: ["query_version_id", "source", "hit_count", "counted_at"],
    RunLog: [
        "id", "query_version_id", "step", "rule_version", "params", "stats",
        "status", "started_at", "finished_at",
    ],
    Record: [
        "id", "source", "source_id", "pmid", "doi", "title", "title_clean",
        "abstract", "authors", "first_author", "year", "journal", "volume",
        "pages", "language", "pub_types", "mesh_terms", "raw", "first_seen_run",
        "created_at",
    ],
    QueryRecord: ["query_version_id", "record_id", "rank_in_results", "found_at"],
}


def test_models_match_schema_sql_column_for_column():
    for model, expected in EXPECTED_COLUMNS.items():
        assert list(model.__table__.columns.keys()) == expected
