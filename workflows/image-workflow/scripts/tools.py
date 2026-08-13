"""Workflow-local helpers for image-workflow.

The actual candidate search and image probe implementation lives in the shared
writer/image support module so Writer can reuse it without invoking this workflow.
"""
from __future__ import annotations

from lazymind.chat.engine.tools.infra.image_search_support import search_image_urls
# Exposed by name for the workflow package's dynamic tool loader.
from lazymind.chat.engine.tools.infra.image_search_support import validate_image_ref  # noqa: F401


def image_search_tool(query: str) -> str:
    """Search for reference images matching a visual concept.

    URLs are candidates only. Call ``validate_image_ref`` before persisting one.
    """
    urls = search_image_urls(str(query or '').strip(), count=5)
    if not urls:
        return f'No image URLs found for "{query}". Try a more specific query.'
    return '\n'.join(urls)
