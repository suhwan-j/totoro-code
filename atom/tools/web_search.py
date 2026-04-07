import os
from langchain_core.tools import tool


@tool
def web_search_tool(query: str, max_results: int = 5) -> str:
    """Search the web using Tavily.

    Args:
        query: Search query
        max_results: Maximum number of results (default 5)
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return "Web search unavailable: TAVILY_API_KEY not set."

    from tavily import TavilyClient
    client = TavilyClient(api_key=api_key)
    response = client.search(query, max_results=max_results)

    results = []
    for r in response.get("results", []):
        results.append(f"**{r['title']}**\n{r['url']}\n{r['content'][:500]}")
    return "\n\n---\n\n".join(results) if results else "No results found."
