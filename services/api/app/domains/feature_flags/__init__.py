"""Feature-flag resolution and firm administration.

Scoped kill-switches for product capabilities. Resolution precedence
(highest wins):

    explicit ``LF_FLAG_<KEY>`` env override
      > firm scope        (scope='firm',         scope_ref=<firm id>)
      > jurisdiction scope(scope='jurisdiction', scope_ref=<pack id>)
      > environment scope (scope='environment', scope_ref='')
      > built-in default

Built-in defaults fail closed where it matters: ``document_assembly`` is
``False``; ``ai_assistance`` and ``funding_reminders`` are ``True``.
Unknown keys also fail closed (``False``).
"""
