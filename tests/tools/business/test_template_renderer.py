from src.tools.business.template_renderer import render_html_template


def test_plain_text_variables_are_autoescaped() -> None:
    html = render_html_template(
        html_template="<div>{{ transcript }}</div>",
        variables={"transcript": "<script>alert('x')</script>"},
    )
    assert "<script>" not in html
    assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;" in html


def test_transcript_html_preserves_intentional_markup() -> None:
    html = render_html_template(
        html_template="<div>{{ transcript_html }}</div>",
        variables={"transcript_html": "Caller: hello<br/>\nAI: hi"},
    )
    assert "<br/>" in html
    assert "Caller: hello" in html


def test_other_variables_remain_escaped() -> None:
    html = render_html_template(
        html_template="<p>{{ transcript_note }}</p>",
        variables={"transcript_note": "<b>unsafe</b>"},
    )
    assert "<b>unsafe</b>" not in html
    assert "&lt;b&gt;unsafe&lt;/b&gt;" in html
