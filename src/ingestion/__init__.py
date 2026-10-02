"""Email ingestion and preprocessing package."""

from src.ingestion.himalaya_client import HimalayaClient
from src.ingestion.detector import EmailDetector, WatermarkManager
from src.ingestion.preprocessor import EmailPreprocessor, StructuredEmail
from src.ingestion.gmail_config import generate_himalaya_gmail_toml

__all__ = [
    "HimalayaClient",
    "EmailDetector",
    "WatermarkManager",
    "EmailPreprocessor",
    "StructuredEmail",
    "generate_himalaya_gmail_toml",
]
