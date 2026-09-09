"""Legacy filesystem namespace.

Per-user browsing, mutation and workspace services were retired by UX-05B.
The only remaining compatibility module is ``app.files.downloads``, which
re-exports neutral HTTP streaming primitives used by READY downloads.
"""
