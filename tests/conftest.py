"""Shared pytest bootstrap."""

# Import pandas before any PySide6 import. On Windows, shiboken's feature
# import hook can otherwise break dateutil/six while pandas initializes.
import pandas  # noqa: F401
