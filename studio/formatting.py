"""Safe, deliberately limited markdown-v1 body rendering.

Only fixed HTML tags and bounded integer list attributes are produced. User
content is always escaped. Parsing is iterative and linear in input size;
there are no regular expressions, recursive parsing, or dependencies.

Tables require outer pipes, 2..8 columns, a hyphen separator and 1..40 data
rows. Invalid pipe-prefixed runs are preserved literally as one paragraph.
Plain table cells are tab-separated. Other block boundaries retain newlines.
"""
from html import escape
from string import punctuation


def _inline(value: str) -> tuple[str, str]:
    """Tokenize once, then pair unescaped bold markers without recursion."""
    tokens: list[tuple[str, bool]] = []
    markers: list[int] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value) and value[i + 1] in punctuation:
            tokens.append((value[i + 1], False))
            i += 2
        elif value.startswith("**", i):
            markers.append(len(tokens))
            tokens.append(("**", True))
            i += 2
        else:
            tokens.append((value[i], False))
            i += 1
    tags: dict[int, str] = {}
    for j in range(0, len(markers) - 1, 2):
        start, end = markers[j:j + 2]
        if end > start + 1:
            tags[start] = "<strong>"
            tags[end] = "</strong>"
    html, text = [], []
    for j, (token, _) in enumerate(tokens):
        if j in tags:
            html.append(tags[j])
        else:
            html.append(escape(token))
            text.append(token)
    return "".join(html), "".join(text)


def _block(line: str) -> tuple[str, str, int]:
    for prefix, tag in (("### ", "h3"), ("## ", "h2"), ("- ", "ul"), ("> ", "blockquote")):
        if line.startswith(prefix):
            return tag, line[len(prefix):], 0
    number, dot, content = line.partition(". ")
    if dot and 1 <= len(number) <= 9 and number.isascii() and number.isdigit():
        return "ol", content, int(number)
    return "p", line, 0


def _group_html(tag: str, items: list[tuple[str, int]]) -> str:
    if tag == "p":
        return "<p>" + "<br>".join(item for item, _ in items) + "</p>"
    if tag == "blockquote":
        return "<blockquote><p>" + "<br>".join(item for item, _ in items) + "</p></blockquote>"
    if tag in ("h2", "h3"):
        return f"<{tag}>{items[0][0]}</{tag}>"
    start = items[0][1]
    attr = f' start="{start}"' if tag == "ol" and start != 1 else ""
    out = [f"<{tag}{attr}>"]
    expected = start
    for item, number in items:
        attr = f' value="{number}"' if tag == "ol" and number != expected else ""
        out.append(f"<li{attr}>{item}</li>")
        expected = number + 1
    out.append(f"</{tag}>")
    return "".join(out)


def _cells(line: str) -> list[str] | None:
    """Split only unescaped pipes, retaining escapes for the inline pass."""
    line = line.strip()
    parts: list[str] = []
    cell: list[str] = []
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            cell.append(line[i:i + 2])
            i += 2
        elif line[i] == "|":
            parts.append("".join(cell))
            cell.clear()
            i += 1
        else:
            cell.append(line[i])
            i += 1
    parts.append("".join(cell))
    if len(parts) < 3 or parts[0] or parts[-1]:
        return None
    return [part.strip() for part in parts[1:-1]]


def _table(lines: list[str]) -> tuple[str, list[str]] | None:
    if not 3 <= len(lines) <= 42:
        return None
    rows = [_cells(line) for line in lines]
    if len(rows) < 3 or any(row is None for row in rows):
        return None
    columns = len(rows[0])
    if not 2 <= columns <= 8:
        return None
    if any(len(row) != columns for row in rows):
        return None
    if not all(len(cell) >= 3 and set(cell) == {"-"} for cell in rows[1]):
        return None
    html = ["<table><thead>"]
    text = []
    for index, row in enumerate([rows[0], *rows[2:]]):
        tag = "th" if index == 0 else "td"
        rendered = [_inline(cell) for cell in row]
        html.append("<tr>" + "".join(f"<{tag}>{value}</{tag}>" for value, _ in rendered) + "</tr>")
        text.append("\t".join(plain for _, plain in rendered))
        if index == 0:
            html.append("</thead><tbody>")
    html.append("</tbody></table>")
    return "".join(html), text


def render_body(body: str) -> dict[str, str]:
    """Render markdown-v1; reject non-strings and >20000 characters, never truncate.

    This API is not the legacy plain renderer. Callers select their own legacy
    path when body_format is absent/plain to preserve historical HTML bytes.
    """
    if not isinstance(body, str):
        raise TypeError("body must be str")
    if len(body) > 20_000:
        raise ValueError("body exceeds 20000 characters")
    html: list[str] = []
    text: list[str] = []
    items: list[tuple[str, int]] = []
    current = "p"

    def flush() -> None:
        if items:
            html.append(_group_html(current, items))
            items.clear()

    lines = body.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.lstrip().startswith("|"):
            end = i
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            table = _table(lines[i - 1:end])
            flush()
            if table is not None:
                rendered, plain = table
                html.append(rendered)
                text.extend(plain)
            else:
                # Consume the entire candidate once: never salvage a suffix,
                # strip its decoration, truncate rows, or repeatedly rescan it.
                original = lines[i - 1:end]
                html.append("<p>" + "<br>".join(escape(row) for row in original) + "</p>")
                text.extend(original)
            i = end
            continue
        if not line:
            flush()
            text.append("")
            continue
        tag, content, number = _block(line)
        if tag != current or tag in ("h2", "h3"):
            flush()
        current = tag
        rendered, plain = _inline(content)
        text.append(plain)
        items.append((rendered, number))
    flush()
    return {"html": "\n".join(html), "text": "\n".join(text)}
