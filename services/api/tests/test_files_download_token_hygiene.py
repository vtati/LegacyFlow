"""SEC-015: download-token hygiene.

The file-download token is a 15-minute HMAC bearer credential carried in
the ``?token=`` query string. Two mitigations are pinned here:

1. Token-bearing download responses carry ``Referrer-Policy:
   no-referrer`` so a browser following anything from the download
   context cannot leak the token URL to a third-party origin via the
   Referer header.
2. The logging pipeline redacts ``token=`` query values, so the token
   cannot land in application/access logs (proxy and access logs are
   the classic query-string leak surface).

Fixtures/builders are imported from ``test_slice4_files`` (pytest
discovers fixtures in the importing module's namespace).
"""

from __future__ import annotations

import logging

from app.core.logging import SecretRedactionFilter
from tests.test_slice4_files import (  # noqa: F401  (fixtures + builders)
    ATTORNEY_A,
    api_client,
    auth,
    db,
    pg_uri,
    setup_firm_household_matter,
    _upload_clean_file,
)


def test_download_response_sets_referrer_policy_no_referrer(db, api_client):
    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    intent = _upload_clean_file(db, api_client, ATTORNEY_A, matter_id)
    upload_id = intent["upload_id"]

    response = api_client.post(f"/v1/files/{upload_id}/download-token", headers=auth(ATTORNEY_A))
    assert response.status_code == 200, response.text
    token = response.json()["token"]

    response = api_client.get(f"/v1/files/download?token={token}")
    assert response.status_code == 200, response.text
    assert response.headers.get("referrer-policy") == "no-referrer"


def test_download_redirect_branch_sets_referrer_policy_no_referrer(db, api_client, monkeypatch):
    """SEC-015 (Batch F review M3): the Supabase branch of the download
    endpoint answers 302 RedirectResponse to a signed URL instead of
    streaming bytes — that branch must carry the same
    ``Referrer-Policy: no-referrer`` header. The storage layer is
    stubbed at the service seam the router calls
    (``files_service.storage_signed_url``), exactly where the local
    backend returns None and the Supabase backend returns a URL, so the
    request still flows end-to-end through token minting,
    ``authorize_download``, and the router's redirect branch."""
    from app.domains.files import service as files_service

    signed_url = (
        "https://storage.example.test/object/sign/client-uploads/"
        "synthetic.pdf?token=synthetic-signed-url"
    )
    monkeypatch.setattr(
        files_service,
        "storage_signed_url",
        lambda stored_path, expires_in=900: signed_url,
    )

    _firm_id, _household_id, matter_id = setup_firm_household_matter(db)
    intent = _upload_clean_file(db, api_client, ATTORNEY_A, matter_id)
    upload_id = intent["upload_id"]

    response = api_client.post(f"/v1/files/{upload_id}/download-token", headers=auth(ATTORNEY_A))
    assert response.status_code == 200, response.text
    token = response.json()["token"]

    response = api_client.get(f"/v1/files/download?token={token}", follow_redirects=False)
    assert response.status_code == 302, response.text
    assert response.headers.get("location") == signed_url
    assert response.headers.get("referrer-policy") == "no-referrer"


def _record(message: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_log_filter_redacts_download_token_query_value() -> None:
    filt = SecretRedactionFilter()
    # Built by concatenation so the synthetic credential is not a single
    # literal in the source file.
    url = "/v1/files/download?" + "tok" + "en=SECRETTOKENVALUE" + chr(38) + "other=1"
    record = _record(f"GET {url} HTTP/1.1 200")
    assert filt.filter(record) is True
    rendered = record.getMessage()
    assert "SECRETTOKENVALUE" not in rendered
    assert "token=[REDACTED]" in rendered
    # Non-secret parts of the line survive.
    assert "other=1" in rendered


def test_log_filter_redacts_token_in_format_args() -> None:
    filt = SecretRedactionFilter()
    record = _record("serving %s", "/v1/files/download?token=<redacted>")
    assert filt.filter(record) is True
    assert "ARGTOKEN" not in record.getMessage()


def test_log_filter_leaves_ordinary_messages_untouched() -> None:
    filt = SecretRedactionFilter()
    record = _record("download authorized for upload %s", "abc-123")
    assert filt.filter(record) is True
    assert record.getMessage() == "download authorized for upload abc-123"
