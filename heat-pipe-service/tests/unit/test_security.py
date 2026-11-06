from heatpipe_service.security import redact_url_query


def test_signed_url_query_is_removed_from_error_text() -> None:
    message = "Download failed: https://minio.example/raw/a.att?X-Amz-Signature=secret&expires=123"
    safe = redact_url_query(message)
    assert safe == "Download failed: https://minio.example/raw/a.att?[REDACTED]"
    assert "secret" not in safe
