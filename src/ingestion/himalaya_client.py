
"""Himalaya CLI client wrapper for Gmail IMAP/SMTP interactions.

IMAP/SMTP calls retry transient failures with backoff and raise typed
errors for permanent failures. This prevents authentication or network
errors from being mistaken for an empty inbox.
"""

import json
import logging
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from src.core.resilience import RetryPolicy, is_transient

logger = logging.getLogger(__name__)


class HimalayaError(RuntimeError):
    """Base error for Himalaya CLI failures."""

    def __init__(self, *args: Any, stage: str = "", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.stage = stage


class HimalayaTransientError(HimalayaError):
    """A network or timeout failure worth retrying."""


class HimalayaPermanentError(HimalayaError):
    """An authentication, configuration, or command failure."""


class HimalayaClient:
    """Wrapper around the Himalaya CLI for Gmail IMAP/SMTP."""

    RETRY_POLICY = RetryPolicy(
        max_attempts=3,
        base_delay=1.0,
        max_delay=15.0,
    )
    TIMEOUT_SECONDS = 30

    def __init__(
        self,
        account: str = "default",
        himalaya_path: str = "himalaya",
        mock_mode: bool = False,
        config_path: Optional[str] = None,
    ):
        self.account = account
        self.himalaya_path = himalaya_path
        self.mock_mode = mock_mode
        self.config_path = config_path
        self._mock_inbox: List[Dict[str, Any]] = []
        self._sent_messages: List[Dict[str, Any]] = []

    def _build_command(self, *args: str) -> List[str]:
        """Build a Himalaya command, optionally using a custom config."""
        cmd = [self.himalaya_path]

        if self.config_path:
            cmd.extend(["--config", self.config_path])

        cmd.extend(args)
        return cmd

    def is_cli_available(self) -> bool:
        """Check whether the Himalaya executable is on PATH."""
        return shutil.which(self.himalaya_path) is not None

    def test_connection(self) -> Dict[str, Any]:
        """Verify the Gmail connection using Himalaya."""
        if self.mock_mode:
            return {
                "connected": True,
                "mode": "mock",
                "message": "Connected in mock mode (Himalaya CLI simulated).",
            }

        if not self.is_cli_available():
            return {
                "connected": False,
                "mode": "cli",
                "message": (
                    f"Himalaya CLI executable "
                    f"'{self.himalaya_path}' not found on PATH."
                ),
            }

        cmd = self._build_command(
            "--account",
            self.account,
            "--backend",
            "imap",
            "mailbox",
            "list",
            "--json",
        )

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )

            if res.returncode == 0:
                try:
                    data = json.loads(res.stdout) if res.stdout.strip() else {}
                    folders = data if isinstance(data, list) else data.get("mailboxes", [])
                    return {
                        "connected": True,
                        "mode": "cli",
                        "folders": [
                            folder.get("name") if isinstance(folder, dict) else str(folder)
                            for folder in folders
                        ],
                        "message": "Gmail IMAP connection established successfully.",
                    }
                except json.JSONDecodeError:
                    return {
                        "connected": True,
                        "mode": "cli",
                        "folders": [],
                        "message": "Gmail IMAP connected (unable to parse mailbox list).",
                    }

            # Parse v2 error format: {"error": "...", "sources": [], "backtrace": null}
            error_msg = res.stderr or res.stdout or ""
            try:
                error_data = json.loads(error_msg) if error_msg.strip() else {}
                if isinstance(error_data, dict) and error_data.get("error"):
                    error_msg = error_data["error"]
            except json.JSONDecodeError:
                pass

            # Classify error type for better retry handling
            is_auth = "invalid credentials" in error_msg.lower() or "authentication" in error_msg.lower()
            return {
                "connected": False,
                "mode": "cli",
                "error": error_msg.strip(),
                "error_type": "auth" if is_auth else "other",
                "message": (
                    f"Gmail connection check returned exit code {res.returncode}"
                    + (" (auth failure)" if is_auth else "")
                    + "."
                ),
            }

        except subprocess.TimeoutExpired:
            return {
                "connected": False,
                "mode": "cli",
                "message": "Connection timed out after 15 seconds.",
            }
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "connected": False,
                "mode": "cli",
                "message": f"Connection error: {exc}",
            }

    def _run(self, cmd: List[str], stage: str) -> str:
        """Run a Himalaya command with bounded retries.

        Raises HimalayaTransientError for network/timeout issues,
        HimalayaPermanentError for auth/config failures.
        """

        def attempt() -> str:
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=self.TIMEOUT_SECONDS,
                )
            except subprocess.CalledProcessError as exc:
                detail = (
                    exc.stderr or exc.stdout or str(exc)
                ).strip()

                # Parse Himalaya v2 JSON error format
                try:
                    error_data = json.loads(detail)
                    if isinstance(error_data, dict) and error_data.get("error"):
                        detail = error_data["error"]
                except (json.JSONDecodeError, ValueError):
                    pass

                # Classify error: auth/config = permanent, network/timeout = transient
                detail_lower = detail.lower()
                if any(keyword in detail_lower for keyword in [
                    "invalid credentials",
                    "authentication failed",
                    "auth",
                    "no configuration",
                    "unknown account",
                    "missing field",
                ]):
                    raise HimalayaPermanentError(
                        detail, stage=stage
                    ) from exc

                if any(keyword in detail_lower for keyword in [
                    "timeout",
                    "connection",
                    "network",
                    "temporary",
                ]):
                    raise HimalayaTransientError(
                        detail, stage=stage
                    ) from exc

                # Default to transient for unknown errors
                raise HimalayaTransientError(
                    detail, stage=stage
                ) from exc

            return res.stdout or ""

        try:
            return self.RETRY_POLICY.run(attempt)

        except subprocess.TimeoutExpired as exc:
            raise HimalayaTransientError(
                f"{stage}: Himalaya timed out after "
                f"{self.TIMEOUT_SECONDS}s",
                stage=stage,
            ) from exc

        except FileNotFoundError as exc:
            raise HimalayaPermanentError(
                f"{stage}: Himalaya CLI not found at "
                f"'{self.himalaya_path}'",
                stage=stage,
            ) from exc

        except HimalayaError:
            raise

        except Exception as exc:
            if is_transient(exc):
                raise HimalayaTransientError(
                    f"{stage}: {exc}", stage=stage
                ) from exc

            raise HimalayaPermanentError(
                f"{stage}: {exc}", stage=stage
            ) from exc

    def list_inbox(
        self,
        page_size: int = 50,
        page: int = 1,
    ) -> List[Dict[str, Any]]:
        """List email envelopes in Gmail INBOX.

        Page numbering is 1-based.
        Errors are raised rather than silently returning an empty inbox.

        Returns:
            List of envelope dicts from Himalaya v2 JSON output.
        """
        if self.mock_mode or not self.is_cli_available():
            return self._mock_inbox

        cmd = self._build_command(
            "--account",
            self.account,
            "--backend",
            "imap",
            "envelope",
            "list",
            "--mailbox",
            "INBOX",
            "--page-size",
            str(page_size),
            "--page",
            str(page),
            "--json",
        )

        stdout = self._run(cmd, "list_inbox")

        if not stdout.strip():
            return []

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise HimalayaTransientError(
                f"list_inbox: malformed JSON from Himalaya: {exc}",
                stage="list_inbox",
            ) from exc

        # Himalaya v2 returns either:
        # - A list of envelope objects directly, or
        # - A dict with "envelopes" key containing the list
        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            # Check for error response
            if "error" in data:
                raise HimalayaPermanentError(
                    f"list_inbox: {data['error']}",
                    stage="list_inbox",
                )

            # Try "envelopes" key (v2.0 format)
            envelopes = data.get("envelopes", [])
            if isinstance(envelopes, list):
                return envelopes

        raise HimalayaPermanentError(
            f"list_inbox: unexpected JSON structure: {type(data).__name__}",
            stage="list_inbox",
        )

    def read_message(
        self,
        message_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Read a message from INBOX without marking it as read."""
        if self.mock_mode or not self.is_cli_available():
            for msg in self._mock_inbox:
                if str(msg.get("id")) == str(message_id):
                    return msg
            return None

        cmd = self._build_command(
            "--account",
            self.account,
            "--backend",
            "imap",
            "message",
            "read",
            "--mailbox",
            "INBOX",
            "--json",
            str(message_id),
        )

        stdout = self._run(
            cmd,
            f"read_message({message_id})",
        )

        if not stdout.strip():
            return None

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise HimalayaTransientError(
                f"read_message({message_id}): malformed JSON: {exc}",
                stage="read_message",
            ) from exc

        if not isinstance(data, dict):
            raise HimalayaPermanentError(
                f"read_message({message_id}): unexpected JSON "
                f"structure: {type(data).__name__}",
                stage="read_message",
            )

        # Preserve the Gmail envelope ID. Himalaya's message-read JSON
        # may omit it, but downstream processing and deduplication need it.
        data.setdefault("id", str(message_id))
        return data

    @staticmethod
    def extract_text_body(message: Dict[str, Any]) -> str:
        """Extract the plain-text MIME body from Himalaya's JSON output.

        Himalaya message read returns MIME parts and index arrays such as
        text_body and html_body. Prefer text/plain over HTML.
        """
        parts = message.get("parts", [])
        text_indices = message.get("text_body", [])

        if isinstance(parts, list) and isinstance(text_indices, list):
            for index in text_indices:
                if (
                    isinstance(index, int)
                    and 0 <= index < len(parts)
                ):
                    part = parts[index]
                    body = part.get("body", {})

                    if isinstance(body, dict):
                        text_body = body.get("Text")
                        if isinstance(text_body, str):
                            return text_body

                    elif isinstance(body, str):
                        return body

        # Fallback: inspect MIME content types.
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue

                headers = part.get("headers", {})
                content_type = headers.get("content_type", {})

                if (
                    isinstance(content_type, dict)
                    and content_type.get("c_type") == "text"
                    and content_type.get("c_subtype") == "plain"
                ):
                    body = part.get("body", {})

                    if isinstance(body, dict):
                        text_body = body.get("Text")
                        if isinstance(text_body, str):
                            return text_body

                    elif isinstance(body, str):
                        return body

        return ""

    def send_email(
            self,
            to: str,
            subject: str,
            body: str,
            in_reply_to: Optional[str] = None,
            from_address: Optional[str] = None,
        ) -> bool:
            """Send an email through Himalaya SMTP.

            Args:
                to: Recipient email address.
                subject: Email subject line.
                body: Email body content.
                in_reply_to: Message-ID to reply to (creates threading).
                from_address: Sender address (defaults to account email).

            Returns:
                True if the email was sent successfully, False otherwise.
            """
            if self.mock_mode or not self.is_cli_available():
                self._sent_messages.append({
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "in_reply_to": in_reply_to,
                })
                logger.info(
                    "[SIMULATED] Email to %s | Subject: %s",
                    to,
                    subject,
                )
                return True

            # Himalaya v2.1.0 requires explicit --mail-from and --rcpt-to
            cmd = self._build_command(
                "--account",
                self.account,
                "smtp",
                "send",
                "--mail-from",
                from_address or f"support@{self.account}.com",
                "--rcpt-to",
                to,
            )

            # Build RFC 5322 message (use \n for cross-platform compatibility)
            headers = [
                f"To: {to}",
                f"Subject: {subject}",
            ]

            if in_reply_to:
                headers.append(f"In-Reply-To: <{in_reply_to}>")
                headers.append(f"References: <{in_reply_to}>")

            raw_message = "\n".join(headers) + "\n\n" + body

            try:
                res = self.RETRY_POLICY.run(
                    lambda: subprocess.run(
                        cmd,
                        input=raw_message,
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=self.TIMEOUT_SECONDS,
                        # Himalaya reads message body from stdin when no file given
                        stdin=subprocess.DEVNULL if False else None,
                    )
                )
                if res.returncode == 0:
                    logger.info(
                        "SMTP email sent to %s | Subject: %s",
                        to,
                        subject,
                    )
                    return True

                detail = (res.stderr or res.stdout or "").strip()
                logger.error("SMTP send to %s failed: %s", to, detail)
                return False

            except subprocess.TimeoutExpired:
                logger.error(
                    "SMTP send to %s timed out after %ss.",
                    to,
                    self.TIMEOUT_SECONDS,
                )
                return False

            except subprocess.CalledProcessError as exc:
                detail = (
                    exc.stderr or exc.stdout or str(exc)
                ).strip()
                logger.error("SMTP send to %s failed: %s", to, detail)
                return False

            except Exception as exc:
                logger.error("SMTP send to %s failed: %s", to, exc)
                return False

    # Mock fixture helpers

    def inject_mock_email(
        self,
        email_data: Dict[str, Any],
    ) -> None:
        """Inject a test email into the mock inbox."""
        self._mock_inbox.append(email_data)

    def get_sent_emails(self) -> List[Dict[str, Any]]:
        """Return simulated emails sent in mock mode."""
        return self._sent_messages
