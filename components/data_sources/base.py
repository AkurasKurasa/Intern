"""
components/data_sources/base.py
================================
Abstract base class for data sources.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict


class DataSource(ABC):
    """Abstract data source for reading field values."""

    @abstractmethod
    def lookup(self, field_name: str, section: str = "") -> Optional[str]:
        """Return the value for field_name, or None if not found."""
        ...

    @abstractmethod
    def get_all(self) -> Dict[str, str]:
        """Return all cached field values."""
        ...

    def refresh(self, record_num: int) -> None:
        """Reload data for the given record number. Optional."""
