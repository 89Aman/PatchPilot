from app.github_auth import strip_emojis
from app.review_format import diagram, details, cell

def test_markdown_boundaries():
    value='| Bugs | 1 |\n\n### Findings'
    assert strip_emojis(value)==value
    value=details('Architecture',diagram('sequenceDiagram\n A->>B: Hello'))
    assert '</summary>\n\n```mermaid' in strip_emojis(value)

def test_diagram_fences():
    assert diagram('```mermaid\nflowchart LR\n A-->B\n```').count('```')==2
    assert 'unavailable' in diagram('```mermaid\nflowchart LR')
    assert 'unavailable' in diagram('pie\n title Unsupported')
    assert 'unavailable' in diagram('')
    assert cell('a|b\nc')=='a&#124;b c'
