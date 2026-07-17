"""BrowserbaseTools — cloud browser automation via Browserbase.

Provides tools for navigating websites, taking screenshots, and extracting
content using Browserbase cloud browsers.

Requires:
    pip install browserbase playwright
    BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID environment variables
"""

import json
from os import getenv
from typing import Any, Dict, List, Optional

from agno.tools import Toolkit
from agno.utils.log import log_debug, logger

try:
    from browserbase import Browserbase
except ImportError:
    raise ImportError("`browserbase` not installed. Please install using `pip install browserbase`")


class BrowserbaseTools(Toolkit):
    """Cloud browser automation via Browserbase.

    Args:
        api_key: Browserbase API key. Defaults to BROWSERBASE_API_KEY env var.
        project_id: Browserbase project ID. Defaults to BROWSERBASE_PROJECT_ID env var.
        base_url: Custom Browserbase API endpoint (for self-hosted instances).
        enable_navigate_to: Enable URL navigation. Defaults to True.
        enable_screenshot: Enable screenshots. Defaults to True.
        enable_get_page_content: Enable page content extraction. Defaults to True.
        enable_close_session: Enable session cleanup. Defaults to True.
        all: Enable all tools. Defaults to False.
    """

    @classmethod
    def _build_instructions(cls, tool_names: list[str]) -> str:
        """Build instructions based on which tools are actually enabled."""
        enabled = set(tool_names)
        sections: list[str] = []

        if "navigate_to" in enabled:
            sections.append("**navigate_to** — go to a URL")
        if "get_page_content" in enabled:
            sections.append("**get_page_content** — get the current page HTML")
        if "screenshot" in enabled:
            sections.append("**screenshot** — save a screenshot")
        if "close_session" in enabled:
            sections.append("**close_session** — close the browser when done")

        if len(sections) < 2:
            return ""

        result = "## Browser Tools (Browserbase)\n\n" + "\n".join(f"- {s}" for s in sections)
        result += "\n\nThe browser session persists between calls. Always call close_session() when finished."
        return result

    def __init__(
        self,
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        base_url: Optional[str] = None,
        enable_navigate_to: bool = True,
        enable_screenshot: bool = True,
        enable_get_page_content: bool = True,
        enable_close_session: bool = True,
        all: bool = False,
        **kwargs,
    ):
        self.api_key = api_key or getenv("BROWSERBASE_API_KEY")
        if not self.api_key:
            raise ValueError("BROWSERBASE_API_KEY is required.")

        self.project_id = project_id or getenv("BROWSERBASE_PROJECT_ID")
        if not self.project_id:
            raise ValueError("BROWSERBASE_PROJECT_ID is required.")

        self.base_url = base_url or getenv("BROWSERBASE_BASE_URL")

        if self.base_url:
            self.app = Browserbase(api_key=self.api_key, base_url=self.base_url)
            log_debug(f"Using custom Browserbase API endpoint: {self.base_url}")
        else:
            self.app = Browserbase(api_key=self.api_key)

        # Playwright state
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._session: Any = None
        self._connect_url: Optional[str] = None

        # Build tool list
        tools: List[Any] = []

        if all or enable_navigate_to:
            tools.append(self.navigate_to)
        if all or enable_screenshot:
            tools.append(self.screenshot)
        if all or enable_get_page_content:
            tools.append(self.get_page_content)
        if all or enable_close_session:
            tools.append(self.close_session)

        # Build instructions dynamically based on enabled tools
        if kwargs.get("instructions") is None:
            tool_names = [t.__name__ for t in tools]
            built = self._build_instructions(tool_names)
            if built:
                kwargs["instructions"] = built
                kwargs.setdefault("add_instructions", True)

        super().__init__(name="browserbase_tools", tools=tools, **kwargs)

    def _ensure_session(self):
        """Ensures a session exists, creating one if needed."""
        if not self._session:
            try:
                self._session = self.app.sessions.create(project_id=self.project_id)
                self._connect_url = self._session.connect_url if self._session else ""
                if self._session:
                    log_debug(f"Created new session with ID: {self._session.id}")
            except Exception:
                logger.exception("Failed to create session")
                raise

    def _initialize_browser(self, connect_url: Optional[str] = None):
        """Initialize browser connection if not already initialized."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "`playwright` not installed. Please install using `pip install playwright` and run `playwright install`"
            )

        if connect_url:
            self._connect_url = connect_url
        elif not self._connect_url:
            self._ensure_session()

        if not self._playwright:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(self._connect_url)
            context = self._browser.contexts[0] if self._browser else None
            if context:
                self._page = context.pages[0] if context.pages else context.new_page()

    def _cleanup(self):
        """Clean up browser resources."""
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def navigate_to(self, url: str, connect_url: Optional[str] = None) -> str:
        """Navigates to a URL.

        Args:
            url: The URL to navigate to
            connect_url: Connection URL from an existing session (optional)
        """
        try:
            self._initialize_browser(connect_url)
            self._page.goto(url, wait_until="networkidle")
            return json.dumps({"status": "success", "title": self._page.title(), "url": url})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e), "url": url})

    def screenshot(self, path: str, full_page: bool = True, connect_url: Optional[str] = None) -> str:
        """Takes a screenshot of the current page.

        Args:
            path: File path to save the screenshot
            full_page: Whether to capture the full scrollable page
            connect_url: Connection URL from an existing session (optional)
        """
        try:
            self._initialize_browser(connect_url)
            self._page.screenshot(path=path, full_page=full_page)
            return json.dumps({"status": "success", "path": path})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e), "path": path})

    def get_page_content(self, connect_url: Optional[str] = None) -> str:
        """Gets the HTML content of the current page.

        Args:
            connect_url: Connection URL from an existing session (optional)
        """
        try:
            self._initialize_browser(connect_url)
            return json.dumps(
                {
                    "status": "success",
                    "url": self._page.url,
                    "title": self._page.title(),
                    "content": self._page.content(),
                }
            )
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e)})

    def close_session(self) -> str:
        """Closes the browser session."""
        try:
            self._cleanup()
            self._session = None
            self._connect_url = None
            return json.dumps({"status": "closed"})
        except Exception as e:
            return json.dumps({"status": "warning", "message": str(e)})
