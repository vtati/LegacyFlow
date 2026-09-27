"""Privacy domain: notification preferences, data export, deletion requests.

Own-data surface for the authenticated user plus the firm-admin
reminder-dispatch trigger. All reads/writes are scoped to the caller's own
user id; the API service role bypasses RLS and enforces authorization
server-side (app/core/authz.py).
"""
