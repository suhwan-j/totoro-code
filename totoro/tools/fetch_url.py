from langchain_core.tools import tool


@tool
def fetch_url_tool(url: str, max_length: int = 10000) -> str:
    """Fetch content from a URL.

    Args:
        url: URL to fetch
        max_length: Maximum response length (default 10000)
    """
    import httpx
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        response = client.get(url)
        response.raise_for_status()

    content = response.text
    if len(content) > max_length:
        content = content[:max_length] + f"\n\n... (truncated, {len(response.text)} total chars)"
    return content
