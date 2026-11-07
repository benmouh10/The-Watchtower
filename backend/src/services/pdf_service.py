"""
Server-side PDF rendering for self-contained print HTML.

The HTML documents produced by ``brief_renderer`` are server-authored, carry no
external assets, and are styled with print CSS. Playwright rasterizes them with
the same headless Chromium engine the ingestion scraper drives, so the server
PDF matches what a reader would get from the browser's Print → Save as PDF.

The Chromium binary is provisioned at deploy time (``playwright install
chromium`` in the Docker image). When no browser is available the renderer
raises ``PdfRenderError`` and callers fall back to the print-friendly HTML
surface with an honest message.
"""
import logging

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


class PdfRenderError(RuntimeError):
    """Raised when the headless Chromium PDF renderer cannot produce output."""


async def render_html_to_pdf(html: str) -> bytes:
    """Render a self-contained HTML document to PDF bytes.

    Raises PdfRenderError when the document is empty or the browser/render
    step fails, so callers can degrade to the HTML export honestly.
    """
    if not html or not html.strip():
        raise PdfRenderError("Nothing to render: the document is empty.")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await (await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )).new_page()
                await page.set_content(html, wait_until="load")
                # Chromium's print emulation applies @media print styles (the
                # body margin in brief_renderer) and honors page-break rules.
                pdf = await page.pdf(format="A4", print_background=True)
                if not pdf or not pdf.startswith(b"%PDF"):
                    raise PdfRenderError("Renderer returned no PDF document.")
                return pdf
            finally:
                await browser.close()
    except PdfRenderError:
        raise
    except Exception as exc:  # browser binary missing / crash / timeout
        logger.error("Server-side PDF render failed", error=str(exc), exc_info=True)
        raise PdfRenderError(f"PDF renderer unavailable: {str(exc)}") from exc