
"""Email ingestion, parsing, cleaning, and normalization into structured objects."""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import re
import html
from html.parser import HTMLParser
import email
from email import policy
from email.message import EmailMessage


class HTMLTextExtractor(HTMLParser):
    """HTML-to-clean-text parser with entity decoding and tag stripping."""

    def __init__(self):
        super().__init__()
        self.result: List[str] = []
        self.skip_tags = {"script", "style", "meta", "noscript", "head", "svg"}
        self.skip_stack = 0

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        if tag_lower in self.skip_tags:
            self.skip_stack += 1
        elif tag_lower in (
            "br", "p", "div", "tr", "li",
            "h1", "h2", "h3", "h4", "h5", "h6"
        ):
            self.result.append("\n")

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower in self.skip_tags and self.skip_stack > 0:
            self.skip_stack -= 1
        elif tag_lower in (
            "p", "div", "tr", "li",
            "h1", "h2", "h3", "h4", "h5", "h6"
        ):
            self.result.append("\n")

    def handle_data(self, data):
        if self.skip_stack == 0:
            self.result.append(data)

    def get_text(self) -> str:
        raw = "".join(self.result)
        raw = raw.replace("\xa0", " ").replace("\u200b", "")
        decoded = html.unescape(raw)

        # Normalize whitespace while maintaining paragraphs.
        lines = [line.strip() for line in decoded.splitlines()]
        cleaned_lines = []
        last_empty = False

        for line in lines:
            if line:
                cleaned_lines.append(line)
                last_empty = False
            elif not last_empty:
                cleaned_lines.append("")
                last_empty = True

        return "\n".join(cleaned_lines).strip()


@dataclass
class StructuredEmail:
    """Normalized structured representation of an incoming email."""

    message_id: str
    sender_email: str
    sender_name: str
    recipient: str
    subject: str
    received_date: str
    body_text: str
    in_reply_to: Optional[str] = None
    references: Optional[str] = None
    raw_body: str = ""
    is_html: bool = False
    headers: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert structured email to dictionary."""
        return {
            "message_id": self.message_id,
            "sender_email": self.sender_email,
            "sender_name": self.sender_name,
            "recipient": self.recipient,
            "subject": self.subject,
            "received_date": self.received_date,
            "body_text": self.body_text,
            "in_reply_to": self.in_reply_to,
            "references": self.references,
            "is_html": self.is_html,
            "headers": self.headers,
        }


class EmailPreprocessor:
    """Preprocesses raw email contents, extracts plain text, and sanitizes HTML."""

    @staticmethod
    def clean_html(html_content: str) -> str:
        """Strip scripts, styles, and tags, decode entities, and normalize whitespace."""
        if not html_content:
            return ""

        # Remove HTML comments.
        clean = re.sub(r"<!--.*?-->", "", html_content, flags=re.DOTALL)

        extractor = HTMLTextExtractor()
        extractor.feed(clean)
        return extractor.get_text()

    @classmethod
    def from_raw_rfc822(
        cls, raw_content: str, message_id: str = ""
    ) -> StructuredEmail:
        """Parse raw RFC 822 / MIME email string."""
        msg: EmailMessage = email.message_from_string(
            raw_content, policy=policy.default
        )

        sender_header = msg.get("From", "")
        sender_name, sender_email = "", ""

        if sender_header:
            match = re.match(r"(.*?)(?:<(.+@.+)>)?$", str(sender_header))
            if match:
                sender_name = match.group(1).strip(' "\'')
                sender_email = (
                    match.group(2)
                    if match.group(2)
                    else match.group(1).strip(' "\'')
                )

        recipient = str(msg.get("To", ""))
        subject = str(msg.get("Subject", ""))
        received_date = str(msg.get("Date", ""))
        in_reply_to = str(msg.get("In-Reply-To", "")) or None
        references = str(msg.get("References", "")) or None
        msg_id = message_id or str(msg.get("Message-ID", ""))

        # Extract body.
        body_text = ""
        raw_body = ""
        is_html = False

        if msg.is_multipart():
            text_part = None
            html_part = None

            for part in msg.walk():
                content_type = part.get_content_type()

                if content_type == "text/plain" and text_part is None:
                    text_part = part.get_payload(decode=True)
                elif content_type == "text/html" and html_part is None:
                    html_part = part.get_payload(decode=True)

            if text_part:
                body_text = text_part.decode(errors="replace").strip()
                raw_body = body_text
            elif html_part:
                raw_body = html_part.decode(errors="replace")
                body_text = cls.clean_html(raw_body)
                is_html = True

        else:
            payload = msg.get_payload(decode=True)

            if payload:
                raw_body = payload.decode(errors="replace")
            else:
                raw_body = str(msg.get_payload() or "")

            if (
                msg.get_content_type() == "text/html"
                or "<html" in raw_body.lower()
            ):
                body_text = cls.clean_html(raw_body)
                is_html = True
            else:
                body_text = raw_body.strip()

        headers = {k: str(v) for k, v in msg.items()}

        return StructuredEmail(
            message_id=msg_id,
            sender_email=sender_email.lower().strip(),
            sender_name=sender_name,
            recipient=recipient,
            subject=subject,
            received_date=received_date,
            body_text=body_text,
            in_reply_to=in_reply_to,
            references=references,
            raw_body=raw_body,
            is_html=is_html,
            headers=headers,
        )

    @classmethod
    def from_himalaya_dict(
        cls, data: Dict[str, Any]
    ) -> StructuredEmail:
        """Construct a StructuredEmail from Himalaya CLI message output.

        Raises ValueError when the payload carries no usable message
        identifier. An empty message_id would be worse than a crash:
        every such email would share the same dedup key, so the second
        one would be silently discarded as a duplicate of the first.
        """
        if not isinstance(data, dict):
            raise ValueError(
                f"Expected a message dict, got {type(data).__name__}"
            )

        msg_id = str(
            data.get("id") or data.get("message_id") or ""
        ).strip()

        if not msg_id:
            raise ValueError(
                "Message payload has no 'id' or 'message_id'"
            )

        from_field = data.get("from", {})
        sender_name = ""
        sender_email = ""

        # Handle Himalaya address lists, dictionaries and strings.
        if isinstance(from_field, list):
            first = from_field[0] if from_field else {}

            if isinstance(first, dict):
                sender_name = first.get("name", "") or ""
                sender_email = (
                    first.get("addr")
                    or first.get("email")
                    or ""
                )
            else:
                sender_email = str(first).strip()

        elif isinstance(from_field, dict):
            sender_name = from_field.get("name", "") or ""
            sender_email = (
                from_field.get("addr")
                or from_field.get("email")
                or ""
            )

        elif isinstance(from_field, str):
            match = re.match(
                r"(.*?)(?:<(.+@.+)>)?$", from_field
            )

            if match:
                sender_name = match.group(1).strip(' "\'')
                sender_email = (
                    match.group(2)
                    if match.group(2)
                    else match.group(1).strip(' "\'')
                )
            else:
                sender_email = from_field.strip()

        elif from_field is not None:
            sender_email = str(from_field).strip()

        to_field = data.get("to", "")

        if isinstance(to_field, list) and to_field:
            first_to = to_field[0]
            if isinstance(first_to, dict):
                to_addr = (
                    first_to.get("addr")
                    or first_to.get("email")
                    or ""
                )
            else:
                to_addr = str(first_to)

        elif isinstance(to_field, dict):
            to_addr = (
                to_field.get("addr")
                or to_field.get("email")
                or ""
            )
        else:
            to_addr = str(to_field)

        subject = data.get("subject", "") or ""

        if isinstance(subject, list):
            subject = " ".join(str(s) for s in subject)

        subject = subject.strip()

        received_date = data.get("date", "") or ""

        if isinstance(received_date, list):
            received_date = (
                str(received_date[0]) if received_date else ""
            )

        received_date = received_date.strip()
        in_reply_to = (
            data.get("in_reply_to")
            or data.get("in-reply-to")
            or None
        )
        references = data.get("references") or None

        # Prefer the already-extracted body, then fall back to
        # the MIME fields when necessary.
        raw_body = (
            data.get("body", "")
            or data.get("text_body", "")
            or data.get("html_body", "")
        )

        if isinstance(raw_body, list):
            chunks = []

            for part in raw_body:
                if isinstance(part, dict):
                    chunks.append(
                        str(
                            part.get("content", "")
                            or part.get("body", "")
                        )
                    )
                else:
                    chunks.append(str(part))

            raw_body = "\n".join(
                chunk for chunk in chunks if chunk.strip()
            )

        # Classify based on the selected body itself, not the
        # mere presence of a separate HTML MIME part.
        is_html = bool(
            re.search(
                r"<(?:html|body|p|div|br|table|span|h[1-6])(?:\s|/?>)",
                str(raw_body),
                re.IGNORECASE,
            )
        )

        if is_html:
            body_text = cls.clean_html(str(raw_body))
        else:
            body_text = str(raw_body).strip()

        return StructuredEmail(
            message_id=msg_id,
            sender_email=sender_email.lower().strip(),
            sender_name=sender_name,
            recipient=to_addr,
            subject=subject,
            received_date=received_date,
            body_text=body_text,
            in_reply_to=in_reply_to,
            references=references,
            raw_body=str(raw_body),
            is_html=is_html,
            headers=data.get("headers", {}),
        )