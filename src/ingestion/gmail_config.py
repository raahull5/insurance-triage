"""Himalaya configuration generator with secure Keychain credential integration."""

import os
from pathlib import Path
from typing import Optional


def generate_himalaya_gmail_toml(
    email: str = "support@apexshield.com",
    display_name: str = "Apex Shield Insurance Support",
    keychain_service: str = "himalaya_gmail",
    keychain_account: Optional[str] = None,
    output_path: Optional[Path] = None,
) -> str:
    """Generate Himalaya config.toml using secure macOS Keychain password retrieval."""
    account_user = keychain_account or email
    
    # macOS Keychain command for secure credential retrieval
    keychain_cmd = f"security find-generic-password -s '{keychain_service}' -a '{account_user}' -w"

    toml_content = f"""[accounts.default]
email = "{email}"
display-name = "{display_name}"
default = true

# IMAP Configuration for Reading INBOX
backend.type = "imap"
backend.host = "imap.gmail.com"
backend.port = 993
backend.encryption.type = "tls"
backend.login = "{email}"
backend.auth.type = "password"
backend.auth.cmd = "{keychain_cmd}"

# SMTP Configuration for Sending Replies
message.send.backend.type = "smtp"
message.send.backend.host = "smtp.gmail.com"
message.send.backend.port = 587
message.send.backend.encryption.type = "start-tls"
message.send.backend.login = "{email}"
message.send.backend.auth.type = "password"
message.send.backend.auth.cmd = "{keychain_cmd}"

# Gmail Folder Aliases (v1.2.0+ format)
folder.aliases.inbox = "INBOX"
folder.aliases.sent = "[Gmail]/Sent Mail"
folder.aliases.drafts = "[Gmail]/Drafts"
folder.aliases.trash = "[Gmail]/Trash"
"""
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(toml_content)

    return toml_content
