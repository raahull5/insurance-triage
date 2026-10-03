
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

        parts = data.get("parts", [])

        def get_value(obj, *names):
            """Get a field using case-insensitive key matching."""
            if not isinstance(obj, dict):
                return None
            wanted = {name.lower() for name in names}
            for key, value in obj.items():
                if str(key).lower() in wanted:
                    return value
            return None

        def unwrap_text(value):
            """Extract text from strings and Himalaya value wrappers."""
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return str(value)
            if isinstance(value, list):
                for item in value:
                    result = unwrap_text(item)
                    if result:
                        return result
                return ""
            if isinstance(value, dict):
                for key in ("Text", "String", "Value"):
                    nested = get_value(value, key)
                    if nested is not None:
                        result = unwrap_text(nested)
                        if result:
                            return result
            return ""

        def header_name(header):
            """Normalize Himalaya header names, including {other: ...}."""
            if not isinstance(header, dict):
                return str(header).strip().lower()
            name = header.get("name", "")
            if isinstance(name, str):
                return name.strip().lower().replace("-", "_")
            if isinstance(name, dict):
                other = get_value(name, "other")
                if isinstance(other, str):
                    return other.strip().lower().replace("-", "_")
                for key in name:
                    if str(key).lower() not in ("other",):
                        return str(key).strip().lower().replace("-", "_")
            return ""

        # Himalaya stores the original message headers in the first MIME part.
        mime_headers = []
        if isinstance(parts, list) and parts and isinstance(parts[0], dict):
            candidate = parts[0].get("headers", [])
            if isinstance(candidate, list):
                mime_headers = candidate

        def get_mime_header(*names):
            wanted = {name.lower().replace("-", "_") for name in names}
            for header in mime_headers:
                if not isinstance(header, dict):
                    continue
                if header_name(header) in wanted:
                    return header.get("value")
            return None

        def first_available(top_level_name, *header_names):
            value = data.get(top_level_name)
            if value not in (None, "", [], {}):
                return value
            return get_mime_header(*header_names)

        def parse_address(value):
            """Return (display name, email) from Himalaya address data."""
            if isinstance(value, list):
                for item in value:
                    name, address = parse_address(item)
                    if name or address:
                        return name, address
                return "", ""

            if isinstance(value, str):
                value = value.strip()
                match = re.match(r'^\s*(.*?)\s*<([^<>]+)>\s*$', value)
                if match:
                    return match.group(1).strip(" \"'"), match.group(2).strip()
                if "@" in value:
                    return "", value
                return value, ""

            if not isinstance(value, dict):
                return "", ""

            # Himalaya address representation: {"Address": {"List": [...]}}.
            nested_address = get_value(value, "Address")
            if isinstance(nested_address, dict):
                address_list = get_value(nested_address, "List")
                if isinstance(address_list, list):
                    return parse_address(address_list)
            if isinstance(nested_address, list):
                return parse_address(nested_address)

            name = unwrap_text(get_value(value, "Name", "name"))
            address = unwrap_text(
                get_value(value, "addr", "email", "address")
            )
            if not address and isinstance(nested_address, str):
                address = nested_address.strip()

            if address and "<" in address:
                parsed_name, parsed_address = parse_address(address)
                if parsed_address:
                    address = parsed_address
                    name = name or parsed_name

            return name, address

        sender_value = first_available("from", "from")
        sender_name, sender_email = parse_address(sender_value)

        recipient_value = first_available("to", "to")
        _, to_addr = parse_address(recipient_value)

        subject_value = first_available("subject", "subject")
        subject = unwrap_text(subject_value)

        date_value = first_available("date", "date")

        def format_himalaya_date(value):
            """Format Himalaya's structured DateTime value as ISO 8601."""
            if isinstance(value, dict):
                date_obj = get_value(value, "DateTime", "date_time")
                if isinstance(date_obj, dict):
                    year = get_value(date_obj, "year")
                    month = get_value(date_obj, "month")
                    day = get_value(date_obj, "day")
                    hour = get_value(date_obj, "hour")
                    minute = get_value(date_obj, "minute")
                    second = get_value(date_obj, "second")
                    if all(v is not None for v in (year, month, day)):
                        hour = int(hour or 0)
                        minute = int(minute or 0)
                        second = int(second or 0)
                        tz_hour = int(get_value(date_obj, "tz_hour") or 0)
                        tz_minute = int(get_value(date_obj, "tz_minute") or 0)
                        negative = bool(get_value(date_obj, "tz_before_gmt"))
                        sign = "-" if negative else "+"
                        return (
                            f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
                            f"T{hour:02d}:{minute:02d}:{second:02d}"
                            f"{sign}{tz_hour:02d}:{tz_minute:02d}"
                        )
            return unwrap_text(value)

        received_date = format_himalaya_date(date_value)

        in_reply_to = (
            data.get("in_reply_to")
            or data.get("in-reply-to")
            or unwrap_text(get_mime_header("in_reply_to", "in-reply-to"))
            or None
        )
        if isinstance(in_reply_to, str) and in_reply_to.strip().lower() == "empty":
            in_reply_to = None

        references = (
            data.get("references")
            or unwrap_text(get_mime_header("references"))
            or None
        )
        if isinstance(references, str) and references.strip().lower() == "empty":
            references = None

        # Himalaya's text_body/html_body fields are indexes into
        # the parts array, not the actual message text.
        parts = data.get("parts", [])
        text_indices = data.get("text_body", [])
        html_indices = data.get("html_body", [])

        def get_part_content(index):
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not isinstance(parts, list)
                or not 0 <= index < len(parts)
            ):
                return ""

            part = parts[index]
            if not isinstance(part, dict):
                return ""

            body = part.get("body", {})
            if isinstance(body, dict):
                return body.get("Text") or body.get("Html") or ""
            if isinstance(body, str):
                return body
            return ""

        raw_body = ""

        # Prefer plain text.
        if isinstance(text_indices, list):
            for index in text_indices:
                content = get_part_content(index)
                if isinstance(content, str) and content.strip():
                    raw_body = content
                    break

        # Fall back to HTML if no plain-text part is available.
        if not raw_body and isinstance(html_indices, list):
            for index in html_indices:
                content = get_part_content(index)
                if isinstance(content, str) and content.strip():
                    raw_body = content
                    break

        # Support simpler or older Himalaya response formats.
        if not raw_body:
            for key in ("body", "text_body", "html_body"):
                value = data.get(key)

                if isinstance(value, str) and value.strip():
                    raw_body = value
                    break

                if isinstance(value, dict):
                    content = value.get("Text") or value.get("Html") or ""
                    if isinstance(content, str) and content.strip():
                        raw_body = content
                        break

                # Older Himalaya responses may represent body as a list
                # of content dictionaries or strings. Only apply this to
                # body; text_body/html_body can contain MIME part indexes.
                if key == "body" and isinstance(value, list):
                    body_parts = []
                    for item in value:
                        if isinstance(item, dict):
                            content = item.get("content")
                            if isinstance(content, str) and content.strip():
                                body_parts.append(content)
                        elif isinstance(item, str) and item.strip():
                            body_parts.append(item)

                    if body_parts:
                        raw_body = "\n\n".join(body_parts)
                        break

        # Classify based on the selected body itself.
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