"""Grounded AI gateway (Slice 4).

The gateway answers questions about a matter's jurisdiction pack using
approved educational content only. It never generates legal clauses and
never gives legal advice:

* advice/strategy questions are refused and flagged for attorney review,
* grounded questions are answered from the matter's pack ``knowledge/``
  content with per-claim citations,
* anything outside the approved content escalates to the attorney.
"""
