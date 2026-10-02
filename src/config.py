"""Configuration loader and system settings."""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"


@dataclass
class SystemConfig:
    polling_interval_seconds: int = 60
    watermark_file: Path = DATA_DIR / "watermark.json"
    csv_report_path: Path = DATA_DIR / "triage_results.csv"
    db_path: Path = DATA_DIR / "insurance_triage.db"
    log_level: str = "INFO"


@dataclass
class GmailConfig:
    account_name: str = "default"
    inbox_folder: str = "INBOX"
    keychain_service: str = "himalaya_gmail"
    keychain_account: str = "gmail_user"


@dataclass
class AIConfig:
    model: str = "auto/best-coding"
    temperature: float = 0.2
    max_tokens: int = 2000
    enable_safety_check: bool = True


@dataclass
class AutoReplyConfig:
    enabled: bool = False
    dry_run: bool = True
    signature: str = "\n\nBest regards,\nCustomer Support & Claims Team\nApex Shield Insurance"


@dataclass
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    refresh_interval_seconds: int = 10


@dataclass
class AppConfig:
    system: SystemConfig = field(default_factory=SystemConfig)
    gmail: GmailConfig = field(default_factory=GmailConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    autoreply: AutoReplyConfig = field(default_factory=AutoReplyConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from YAML file or return defaults."""
    path = Path(config_path) if config_path else CONFIG_DIR / "default_config.yaml"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    if not path.exists():
        return AppConfig()

    with open(path, "r", encoding="utf-8") as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}

    sys_data = data.get("system", {})
    system = SystemConfig(
        polling_interval_seconds=sys_data.get("polling_interval_seconds", 60),
        watermark_file=BASE_DIR / sys_data.get("watermark_file", "data/watermark.json"),
        csv_report_path=BASE_DIR / sys_data.get("csv_report_path", "data/triage_results.csv"),
        db_path=BASE_DIR / sys_data.get("db_path", "data/insurance_triage.db"),
        log_level=sys_data.get("log_level", "INFO"),
    )

    g_data = data.get("gmail", {})
    gmail = GmailConfig(
        account_name=g_data.get("account_name", "default"),
        inbox_folder=g_data.get("inbox_folder", "INBOX"),
        keychain_service=g_data.get("keychain_service", "himalaya_gmail"),
        keychain_account=g_data.get("keychain_account", "gmail_user"),
    )

    ai_data = data.get("ai", {})
    ai = AIConfig(
        model=ai_data.get("model", "auto/best-coding"),
        temperature=ai_data.get("temperature", 0.2),
        max_tokens=ai_data.get("max_tokens", 2000),
        enable_safety_check=ai_data.get("enable_safety_check", True),
    )

    ar_data = data.get("autoreply", {})
    autoreply = AutoReplyConfig(
        enabled=ar_data.get("enabled", False),
        dry_run=ar_data.get("dry_run", True),
        signature=ar_data.get("signature", "\n\nBest regards,\nCustomer Support & Claims Team\nApex Shield Insurance"),
    )

    dash_data = data.get("dashboard", {})
    dashboard = DashboardConfig(
        host=dash_data.get("host", "127.0.0.1"),
        port=dash_data.get("port", 8080),
        refresh_interval_seconds=dash_data.get("refresh_interval_seconds", 10),
    )

    return AppConfig(
        system=system,
        gmail=gmail,
        ai=ai,
        autoreply=autoreply,
        dashboard=dashboard,
    )
