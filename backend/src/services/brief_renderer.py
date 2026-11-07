"""Shared self-contained HTML brief renderer.

Used by the reading-list print/PDF export and by the scheduled digest engine so
both produce one consistent layout: serif typography, no external assets, a
stable source/claim structure, and the same honesty footer. Pure and
dependency-free so either caller can render offline.
"""
from html import escape
from typing import Any, Dict, List, Optional


def render_brief_html(
    *,
    title: str,
    kind_label: str,
    generated_at: str,
    events: List[Dict[str, Any]],
    description: Optional[str] = None,
    provenance: str,
    empty_message: str,
) -> str:
    """Render a complete HTML brief document from serialized events.

    `events` entries use the shape produced by the reading-list serializer and
    the digest service: title, country, topic, created_at, importance_score,
    source_names (list of str), summary, claims (list of {text, confidence,
    status}).
    """
    blocks = []
    for index, event in enumerate(events, start=1):
        meta_bits = []
        if event.get("country"):
            meta_bits.append(f"Country: {escape(str(event['country']))}")
        if event.get("topic"):
            meta_bits.append(f"Topic: {escape(str(event['topic']))}")
        if event.get("created_at"):
            meta_bits.append(f"Event date: {escape(str(event['created_at'])[:10])}")
        meta_bits.append(
            f"Importance: {0 if event.get('importance_score') is None else event.get('importance_score'):g}"
        )

        sources = event.get("source_names") or []
        sources_line = (
            f'<p class="sources"><strong>Sources:</strong> {escape(", ".join(sources))}</p>'
            if sources else ""
        )

        claims_html = ""
        claims = event.get("claims") or []
        if claims:
            rows = "".join(
                f'<li><span class="claim">{escape(str(c.get("text", "")))}</span>'
                f'&mdash; confidence {c.get("confidence") if c.get("confidence") is not None else "not scored"}'
                f"{' and adjudicated ' + escape(str(c.get('status', ''))) if c.get('status') else ''}</li>"
                for c in claims
            )
            claims_html = f'<details open><summary>Claims &amp; citations ({len(claims)})</summary><ol>{rows}</ol></details>'

        blocks.append(
            f"""
            <section class="event">
              <h2>{index}. {escape(str(event.get('title', 'Untitled event')))}</h2>
              <p class="meta">{' &middot; '.join(escape(m) for m in meta_bits)}</p>
              {sources_line}
              <p class="summary">{escape(str(event.get('summary') or '')) or '<em>No synthesis available yet.</em>'}</p>
              {claims_html}
            </section>
            """
        )

    body = "\n".join(blocks) if blocks else f'<p class="empty">{escape(empty_message)}</p>'

    description_html = (
        f'<p class="description">{escape(description)}</p>' if description else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: Georgia, 'Times New Roman', serif; font-size: 12pt;
        color: #1a1a1a; line-height: 1.5; margin: 1in auto; max-width: 7.5in; padding: 0 0.25in; }}
  h1 {{ font-size: 20pt; margin: 0 0 2pt 0; }}
  a {{ color: inherit; text-decoration: none; }}
  .sub {{ color: #555; font-size: 10pt; margin: 0 0 4pt 0; }}
  .description {{ font-style: italic; color: #333; }}
  .event {{ page-break-inside: avoid; margin-top: 18pt; }}
  .event h2 {{ font-size: 13pt; margin: 0 0 3pt 0; border-bottom: 1px solid #bbb; padding-bottom: 3pt; }}
  .meta {{ font-size: 9.5pt; color: #444; margin: 0 0 6pt 0; }}
  .sources {{ font-size: 9.5pt; color: #444; margin: 0 0 6pt 0; }}
  .summary {{ margin: 0 0 8pt 0; }}
  .claim {{ }}
  details {{ font-size: 10pt; }}
  summary {{ font-weight: bold; cursor: default; }}
  footer {{ margin-top: 24pt; padding-top: 8pt; border-top: 1px solid #ddd;
         font-size: 8.5pt; color: #666; }}
  .empty {{ font-style: italic; color: #555; }}
  @media print {{ body {{ margin: 0.4in; }} a {{ color: inherit; }} }}
</style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <p class="sub">{escape(kind_label)} &middot; generated {escape(generated_at)} &middot; {len(events)} event(s)</p>
  {description_html}
  {body}
  <footer>
    <p>{escape(provenance)}</p>
    <p>Syntheses and claims are AI-generated and can be wrong. Source links, confidence scores, and bias
    classifications are aids, not verdicts; do not rely on this report for legal, financial, or medical decisions.</p>
  </footer>
</body>
</html>
"""