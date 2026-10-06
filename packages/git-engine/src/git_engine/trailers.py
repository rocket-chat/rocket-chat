"""Git commit message trailer formatting and parsing according to standard Git hygiene."""

import re

from specifications.interfaces.git import CoAuthor

CO_AUTHOR_PATTERN = re.compile(
    r"^Co-authored-by:\s+(?P<name>[^<]+?)\s+<(?P<email>[^>]+)>",
    re.MULTILINE | re.IGNORECASE,
)


def format_commit_message(message: str, co_authors: list[CoAuthor] | None = None) -> str:
    """
    Format a commit message with standard Git Co-authored-by trailers.
    Separates the commit body and trailers with a blank line and prevents duplicate trailers.
    """
    cleaned = message.strip()
    if not co_authors:
        return cleaned

    existing_authors = {
        (author.name.strip().lower(), author.email.strip().lower())
        for author in parse_co_authors(cleaned)
    }

    new_trailers: list[str] = []
    for author in co_authors:
        name = author.name.strip()
        email = author.email.strip()
        if not name or not email:
            continue
        key = (name.lower(), email.lower())
        if key not in existing_authors:
            new_trailers.append(f"Co-authored-by: {name} <{email}>")
            existing_authors.add(key)

    if not new_trailers:
        return cleaned

    return f"{cleaned}\n\n" + "\n".join(new_trailers)


def parse_co_authors(message: str) -> list[CoAuthor]:
    """Extract all Co-authored-by trailers from a commit message."""
    results: list[CoAuthor] = []
    for match in CO_AUTHOR_PATTERN.finditer(message):
        name = match.group("name").strip()
        email = match.group("email").strip()
        results.append(CoAuthor(name=name, email=email))
    return results
