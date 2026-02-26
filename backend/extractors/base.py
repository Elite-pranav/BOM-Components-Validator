"""
Abstract base class for all document extractors.

Every extractor (CS, BOM, SAP) inherits from BaseExtractor, which provides:
  - A consistent constructor that sets up raw/processed folder paths
  - A per-class logger for structured log output
  - An abstract extract() method that subclasses must implement
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path


class BaseExtractor(ABC):
    """Common interface for all document extractors."""

    def __init__(self, raw_folder: Path, processed_folder: Path):
        self.raw_folder = raw_folder
        self.processed_folder = processed_folder
        self.processed_folder.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def extract(self) -> dict | list:
        """Run extraction and return structured data."""
        ...
