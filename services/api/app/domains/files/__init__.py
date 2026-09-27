"""Files domain: client file uploads with server-side malware-format scanning (Slice 4, Track B4).

Flow: a caller creates an upload *intent* (validated filename, MIME, size),
uploads the bytes via ``complete``, a worker scan validates the magic bytes
against the claimed MIME type (``scan_status`` ``pending`` -> ``clean`` or
``quarantined``), and downloads go through short-lived HMAC-signed tokens.
Quarantined or unscanned files are never downloadable.
"""
