from __future__ import annotations

import httpx
import pytest

from deeptutor.services.web_source import crawler, robots
from deeptutor.tools.web_fetch import DEFAULT_MAX_CHARS as WEB_FETCH_MAX_CHARS


@pytest.fixture
def crawl_clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(robots.time, "monotonic", lambda: now[0])

    async def sleep(delay):
        now[0] += delay

    monkeypatch.setattr(robots.asyncio, "sleep", sleep)
    monkeypatch.setattr(robots, "_is_disallowed_host", lambda _: False)
    monkeypatch.setattr(crawler, "_is_disallowed_host", lambda _: False)
    return now


async def _crawl_single_page(body_chars: int) -> str:
    paragraph = "<p>" + "word " * 199 + "end</p>"  # ~1k chars per paragraph

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        paragraphs = paragraph * (body_chars // len(paragraph) + 1)
        return httpx.Response(200, text=f"<html><h1>Chapter</h1>{paragraphs}</html>")

    result = await crawler.crawl_docs_site(
        "https://example.com/book/",
        max_depth=0,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert len(result.pages) == 1
    return result.pages[0].markdown


@pytest.mark.asyncio
async def test_long_book_chapter_is_not_cut_at_the_web_fetch_tool_limit(crawl_clock):
    markdown = await _crawl_single_page(WEB_FETCH_MAX_CHARS * 2)

    assert len(markdown) > WEB_FETCH_MAX_CHARS * 2
    assert "[truncated]" not in markdown


@pytest.mark.asyncio
async def test_pages_beyond_the_crawler_limit_are_still_truncated(crawl_clock):
    markdown = await _crawl_single_page(crawler.MAX_PAGE_CHARS + 10_000)

    assert markdown.endswith("…[truncated]")
    assert len(markdown) <= crawler.MAX_PAGE_CHARS + len("\n…[truncated]")
