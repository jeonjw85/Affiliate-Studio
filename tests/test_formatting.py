"""Contract tests for the dependency-free markdown-v1 renderer."""
import importlib
import pytest


@pytest.mark.parametrize("body, html, text", [
    ("## **장점**\n### 상세\n평문 **강조**와 **미완", "<h2><strong>장점</strong></h2>\n<h3>상세</h3>\n<p>평문 <strong>강조</strong>와 **미완</p>", "장점\n상세\n평문 강조와 **미완"),
    (r"\## 제목\n".replace(r"\n", "\n") + r"\**literal\** C:\tmp \| \\ **<b>**", "<p>## 제목<br>**literal** C:\\tmp | \\ <strong>&lt;b&gt;</strong></p>", "## 제목\n**literal** C:\\tmp | \\ <b>"),
    ("# no\n#### no\n[x](javascript:alert(1)) ![x](data:x)\n**ok**", "<p># no<br>#### no<br>[x](javascript:alert(1)) ![x](data:x)<br><strong>ok</strong></p>", "# no\n#### no\n[x](javascript:alert(1)) ![x](data:x)\nok"),
])
def test_heading_with_safe_inline_markup(body, html, text):
    assert render(body) == {"html": html, "text": text}


@pytest.mark.parametrize("body, html, text", [
    ("- **첫째**\n- 둘째\n문장", "<ul><li><strong>첫째</strong></li><li>둘째</li></ul>\n<p>문장</p>", "첫째\n둘째\n문장"),
    ("1. 하나\n2. 둘\n\n7. 일곱\n10. 열", '<ol><li>하나</li><li>둘</li></ol>\n<ol start="7"><li>일곱</li><li value="10">열</li></ol>', "하나\n둘\n\n일곱\n열"),
    ("> **주의**\n> <img src=x onerror=x>\n## 끝", "<blockquote><p><strong>주의</strong><br>&lt;img src=x onerror=x&gt;</p></blockquote>\n<h2>끝</h2>", "주의\n<img src=x onerror=x>\n끝"),
    ("- 목록\n1. 순서\n> 인용\n\\- 문자\n1\\. 문자\n\\> 문자", "<ul><li>목록</li></ul>\n<ol><li>순서</li></ol>\n<blockquote><p>인용</p></blockquote>\n<p>- 문자<br>1. 문자<br>&gt; 문자</p>", "목록\n순서\n인용\n- 문자\n1. 문자\n> 문자"),
])
def test_structural_blocks(body, html, text):
    assert render(body) == {"html": html, "text": text}


def test_table_preserves_escaped_pipes_numbers_and_inline_markup():
    body = "위\n| **항목** | 값 |\n| --- | --- |\n| A\\|B | 001.20 |\n| <img src=x> | **42** |\n\n아래"
    result = render(body)
    assert result == {
        "html": "<p>위</p>\n<table><thead><tr><th><strong>항목</strong></th><th>값</th></tr></thead><tbody><tr><td>A|B</td><td>001.20</td></tr><tr><td>&lt;img src=x&gt;</td><td><strong>42</strong></td></tr></tbody></table>\n<p>아래</p>",
        "text": "위\n항목\t값\nA|B\t001.20\n<img src=x>\t42\n\n아래",
    }


@pytest.mark.parametrize("cols, rows", [(2, 1), (8, 40)])
def test_valid_table_boundaries(cols, rows):
    header = "|" + "|".join(f"h{i}" for i in range(cols)) + "|"
    separator = "|" + "|".join(["---"] * cols) + "|"
    data = ["|" + "|".join(f"{r}.{c:02d}" for c in range(cols)) + "|" for r in range(rows)]
    result = render("\n".join([header, separator, *data]))
    assert result["html"].count("<th>") == cols
    assert result["html"].count("<td>") == rows * cols
    assert len(result["text"].splitlines()) == rows + 1
    for r in range(rows):
        for c in range(cols):
            assert f"{r}.{c:02d}" in result["text"]


@pytest.mark.parametrize("body", [
    "| **항목** | 값 |\n| --- | --- |\n| A\\|B | 001.20 |\n| 유실금지 | 99 | 추가 |",
    "| a | b |\n| - | --- |\n| 01 | 02 |",
    "| a | b |\n| --- | --- |",
    "| **a** | b |\n| --- | --- |\n| 01 | 02",
    "| a | b |\n| --- | --- |\n| 01 | 02\\|",
    "| a | b |\n| --- |\n| 01 | 02 |",
    "| **a** | b |\n| no separator | 007 |",
    "| a |\n| --- |\n| 0.001 |",
    "|" + "h|" * 9 + "\n|" + "---|" * 9 + "\n|" + "001|" * 9,
    "| a | b |\n| --- | --- |\n" + "\n".join(f"| {i:03d} | 99.00 |" for i in range(41)),
])
def test_malformed_table_falls_back_without_losing_any_source(body):
    from html import escape
    assert render(body) == {"html": "<p>" + escape(body).replace("\n", "<br>") + "</p>", "text": body}


def test_input_limit_rejects_instead_of_truncating():
    assert render("x" * 20_000)["text"] == "x" * 20_000
    with pytest.raises(ValueError, match="20000"):
        render("x" * 20_001)


def test_non_string_input_is_rejected_explicitly():
    with pytest.raises(TypeError, match="str"):
        render(None)


def test_xss_fuzz_emits_only_balanced_allowlisted_tags():
    from html.parser import HTMLParser
    import random

    class Audit(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []

        def handle_starttag(self, tag, attrs):
            assert tag in {"p", "br", "h2", "h3", "strong", "ul", "ol", "li", "blockquote", "table", "thead", "tbody", "tr", "th", "td"}
            for name, value in attrs:
                assert (tag, name) in {("ol", "start"), ("li", "value")}
                assert value.isascii() and value.isdigit() and len(value) <= 9
            if tag != "br":
                self.stack.append(tag)

        def handle_endtag(self, tag):
            assert self.stack.pop() == tag

        def handle_comment(self, data):
            pytest.fail("user content became an HTML comment")

        def handle_decl(self, decl):
            pytest.fail("user content became an HTML declaration")

    payloads = [
        '<script>alert(1)</script>', '<img src=x onerror="alert(1)">',
        '<svg/onload=alert(1)>', '</td></tr><iframe srcdoc="x">',
        '<!--x-->', '<!DOCTYPE html>', '&lt;script&gt;', '&#x3c;img>',
        '[x](javascript:alert(1))', '![x](data:text/html,x)',
        '<style>@import "x";</style>', '" onmouseover="x',
    ]
    rng = random.Random(20260403)
    cases = []
    for payload in payloads:
        cases.extend([payload, f"## **{payload}**", f"- {payload}", f"> {payload}", f"| x | y |\n| --- | --- |\n| {payload} | 000.10 |"])
    atoms = payloads + ["**", "\\", "|", "\n", "## ", "- ", "> ", "1. ", "한글", "001.20"]
    cases.extend("".join(rng.choice(atoms) for _ in range(60)) for _ in range(300))
    for body in cases:
        result = render(body)
        parser = Audit()
        parser.feed(result["html"])
        parser.close()
        assert not parser.stack
        assert result == render(body)
    print(f"XSS fuzz: {len(cases)} deterministic cases passed")


def test_20k_adversarial_inputs_are_bounded():
    from time import perf_counter
    patterns = ["*", "\\", "|\n", "|**x**|\\|\n", "9", "**x", "<>&\"", "## \n", "1. \n"]
    started = perf_counter()
    for pattern in patterns:
        body = (pattern * (20_000 // len(pattern) + 1))[:20_000]
        result = render(body)
        assert isinstance(result["text"], str)
        assert len(result["html"]) <= 20 * len(body) + 100
    elapsed = perf_counter() - started
    print(f"20k adversarial: {len(patterns)} cases in {elapsed:.4f}s")
    assert elapsed < 5.0


def test_escaped_table_pipe_and_backslash_parity():
    body = r"| a | b |" + "\n| --- | --- |\n" + r"| slash\\ | a\|b |"
    assert render(body)["text"] == "a\tb\nslash\\\ta|b"
    assert render(r"\| not a table **bold**")["html"] == "<p>| not a table <strong>bold</strong></p>"
    assert render("**** **odd")["text"] == "**** **odd"


def render(body):
    return importlib.import_module("studio.formatting").render_body(body)


def test_plain_paragraphs_escape_html_and_preserve_line_breaks():
    body = '<script>alert("x")</script> & 한글\n둘째\n\n다음'
    assert render(body) == {
        "html": '<p>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; 한글<br>둘째</p>\n<p>다음</p>',
        "text": body,
    }
    assert render("") == {"html": "", "text": ""}
