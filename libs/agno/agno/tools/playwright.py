"""PlaywrightTools — local browser automation via Playwright.

Provides tools for navigating websites, taking screenshots, extracting content,
and interacting with pages using a local Playwright browser.

Requires:
    pip install playwright
    playwright install chromium  # or firefox, webkit
"""

import json
from typing import Any, Dict, List, Optional

from agno.tools import Toolkit
from agno.utils.log import log_debug

try:
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
except ImportError:
    raise ImportError(
        "`playwright` not installed. Please install using `pip install playwright` "
        "and run `playwright install chromium`"
    )


class PlaywrightTools(Toolkit):
    """Local browser automation via Playwright.

    Args:
        headless: Run browser in headless mode. Defaults to True.
        browser: Browser to use: chromium, firefox, or webkit. Defaults to chromium.
        user_agent: Custom user agent string.
        timeout_ms: Default timeout in milliseconds. Defaults to 30000.
        enable_navigate_to: Enable URL navigation. Defaults to True.
        enable_go_back: Enable history back navigation. Defaults to True.
        enable_screenshot: Enable screenshots. Defaults to True.
        enable_get_page_content: Enable page content extraction. Defaults to True.
        enable_close_session: Enable session cleanup. Defaults to True.
        enable_click: Enable clicking elements. Defaults to False.
        enable_type: Enable typing text. Defaults to False.
        enable_fill_form: Enable form filling. Defaults to False.
        enable_get_element_text: Enable element text extraction. Defaults to False.
        enable_wait_for: Enable waiting for elements. Defaults to False.
        enable_evaluate_js: Enable JavaScript execution. Defaults to False.
        enable_save_pdf: Enable PDF generation. Defaults to False.
        enable_get_console_messages: Enable console message capture. Defaults to False.
        enable_get_network_requests: Enable network request capture. Defaults to False.
        all: Enable all tools. Defaults to False.
        record_video_dir: Directory to save video recordings. Enables get_recording tool.
    """

    @classmethod
    def _build_instructions(cls, tool_names: list[str]) -> str:
        """Build instructions based on which tools are actually enabled."""
        enabled = set(tool_names)
        sections: list[str] = []

        if "navigate_to" in enabled:
            sections.append("**navigate_to** — go to a URL")
        if "go_back" in enabled:
            sections.append("**go_back** — navigate back in browser history")
        if "get_page_content" in enabled:
            sections.append("**get_page_content** — get the current page HTML")
        if "get_element_text" in enabled:
            sections.append("**get_element_text** — get text content of an element")
        if "click" in enabled:
            sections.append("**click** — click an element by CSS selector")
        if "type_text" in enabled:
            sections.append("**type_text** — type text into an input element")
        if "fill_form" in enabled:
            sections.append("**fill_form** — fill multiple form fields at once")
        if "wait_for" in enabled:
            sections.append("**wait_for** — wait for an element to appear")
        if "screenshot" in enabled:
            sections.append("**screenshot** — save a screenshot")
        if "save_pdf" in enabled:
            sections.append("**save_pdf** — generate a PDF (Chromium only)")
        if "get_console_messages" in enabled:
            sections.append("**get_console_messages** — get browser console output")
        if "get_network_requests" in enabled:
            sections.append("**get_network_requests** — get recent network requests")
        if "evaluate_js" in enabled:
            sections.append("**evaluate_js** — execute JavaScript on the page")
        if "get_recording" in enabled:
            sections.append("**get_recording** — get the video recording path")
        if "close_session" in enabled:
            sections.append("**close_session** — close the browser when done")

        if len(sections) < 2:
            return ""

        result = "## Browser Tools\n\n" + "\n".join(f"- {s}" for s in sections)
        result += "\n\nThe browser session persists between calls. Always call close_session() when finished."
        return result

    def __init__(
        self,
        headless: bool = True,
        browser: str = "chromium",
        user_agent: Optional[str] = None,
        timeout_ms: int = 30000,
        enable_navigate_to: bool = True,
        enable_go_back: bool = True,
        enable_screenshot: bool = True,
        enable_get_page_content: bool = True,
        enable_close_session: bool = True,
        enable_click: bool = False,
        enable_type: bool = False,
        enable_fill_form: bool = False,
        enable_get_element_text: bool = False,
        enable_wait_for: bool = False,
        enable_evaluate_js: bool = False,
        enable_save_pdf: bool = False,
        enable_get_console_messages: bool = False,
        enable_get_network_requests: bool = False,
        all: bool = False,
        record_video_dir: Optional[str] = None,
        **kwargs,
    ):
        self.headless = headless
        self.browser_type = browser
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self.record_video_dir = record_video_dir
        self.enable_get_console_messages = all or enable_get_console_messages
        self.enable_get_network_requests = all or enable_get_network_requests

        # Playwright state
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

        # Console/network buffers
        self._console_messages: List[Dict[str, Any]] = []
        self._network_requests: List[Dict[str, Any]] = []

        # Build tool list
        tools: List[Any] = []

        if all or enable_navigate_to:
            tools.append(self.navigate_to)
        if all or enable_go_back:
            tools.append(self.go_back)
        if all or enable_screenshot:
            tools.append(self.screenshot)
        if all or enable_get_page_content:
            tools.append(self.get_page_content)
        if all or enable_close_session:
            tools.append(self.close_session)
        if all or enable_click:
            tools.append(self.click)
        if all or enable_type:
            tools.append(self.type_text)
        if all or enable_fill_form:
            tools.append(self.fill_form)
        if all or enable_get_element_text:
            tools.append(self.get_element_text)
        if all or enable_wait_for:
            tools.append(self.wait_for)
        if all or enable_evaluate_js:
            tools.append(self.evaluate_js)
        if all or enable_save_pdf:
            tools.append(self.save_pdf)
        if all or enable_get_console_messages:
            tools.append(self.get_console_messages)
        if all or enable_get_network_requests:
            tools.append(self.get_network_requests)
        if record_video_dir:
            tools.append(self.get_recording)

        # Build instructions dynamically based on enabled tools
        if kwargs.get("instructions") is None:
            tool_names = [t.__name__ for t in tools]
            built = self._build_instructions(tool_names)
            if built:
                kwargs["instructions"] = built
                kwargs.setdefault("add_instructions", True)

        super().__init__(name="playwright_tools", tools=tools, **kwargs)

    def _initialize_browser(self):
        """Initialize browser if not already initialized."""
        if self._page:
            return

        self._playwright = sync_playwright().start()
        browser_launcher = getattr(self._playwright, self.browser_type)
        self._browser = browser_launcher.launch(headless=self.headless)

        context_options: Dict[str, Any] = {}
        if self.user_agent:
            context_options["user_agent"] = self.user_agent
        if self.record_video_dir:
            context_options["record_video_dir"] = self.record_video_dir

        context = self._browser.new_context(**context_options)
        context.set_default_timeout(self.timeout_ms)
        page = context.new_page()

        if self.enable_get_console_messages:
            page.on("console", self._on_console_message)
        if self.enable_get_network_requests:
            page.on("request", self._on_request)

        self._context = context
        self._page = page
        log_debug(f"Playwright browser initialized: {self.browser_type}, headless={self.headless}")

    def _on_console_message(self, msg):
        self._console_messages.append({"type": msg.type, "text": msg.text})
        if len(self._console_messages) > 200:
            self._console_messages = self._console_messages[-200:]

    def _on_request(self, request):
        self._network_requests.append({"url": request.url, "method": request.method})
        if len(self._network_requests) > 100:
            self._network_requests = self._network_requests[-100:]

    def _cleanup(self):
        if self._context:
            self._context.close()
            self._context = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self._page = None
        self._console_messages = []
        self._network_requests = []

    def navigate_to(self, url: str) -> str:
        """Navigates to a URL.

        Args:
            url: The URL to navigate to

        Returns:
            JSON string with navigation status, title, and URL
        """
        try:
            self._initialize_browser()
            self._page.goto(url, wait_until="networkidle")
            return json.dumps({"status": "success", "title": self._page.title(), "url": url})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e), "url": url})

    def go_back(self) -> str:
        """Navigates back in browser history."""
        try:
            self._initialize_browser()
            self._page.go_back(wait_until="networkidle")
            return json.dumps({"status": "success", "title": self._page.title(), "url": self._page.url})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e)})

    def screenshot(self, path: str, full_page: bool = True) -> str:
        """Takes a screenshot of the current page.

        Args:
            path: File path to save the screenshot
            full_page: Whether to capture the full scrollable page
        """
        try:
            self._initialize_browser()
            self._page.screenshot(path=path, full_page=full_page)
            return json.dumps({"status": "success", "path": path})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e), "path": path})

    def get_page_content(self) -> str:
        """Gets the HTML content of the current page."""
        try:
            self._initialize_browser()
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
            return json.dumps({"status": "closed"})
        except Exception as e:
            return json.dumps({"status": "warning", "message": str(e)})

    def click(self, selector: str) -> str:
        """Clicks an element on the page.

        Args:
            selector: CSS selector of element to click
        """
        try:
            self._initialize_browser()
            self._page.click(selector)
            return json.dumps({"status": "success", "selector": selector})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e), "selector": selector})

    def type_text(self, selector: str, text: str) -> str:
        """Types text into an input element.

        Args:
            selector: CSS selector of input element
            text: Text to type
        """
        try:
            self._initialize_browser()
            self._page.fill(selector, text)
            return json.dumps({"status": "success", "selector": selector})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e), "selector": selector})

    def fill_form(self, form_data: Dict[str, str]) -> str:
        """Fills multiple form fields at once.

        Args:
            form_data: Dictionary mapping CSS selectors to values
        """
        try:
            self._initialize_browser()
            for selector, value in form_data.items():
                self._page.fill(selector, value)
            return json.dumps({"status": "success", "filled": list(form_data.keys())})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e)})

    def get_element_text(self, selector: str) -> str:
        """Gets text content of a specific element.

        Args:
            selector: CSS selector of element
        """
        try:
            self._initialize_browser()
            element = self._page.query_selector(selector)
            if element:
                return json.dumps({"status": "success", "text": element.inner_text()})
            return json.dumps({"status": "error", "message": f"Element not found: {selector}"})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e)})

    def wait_for(self, selector: str, timeout_ms: Optional[int] = None) -> str:
        """Waits for an element to appear on the page.

        Args:
            selector: CSS selector to wait for
            timeout_ms: Maximum time to wait in milliseconds
        """
        try:
            self._initialize_browser()
            self._page.wait_for_selector(selector, timeout=timeout_ms or self.timeout_ms)
            return json.dumps({"status": "success", "selector": selector})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e), "selector": selector})

    def evaluate_js(self, expression: str) -> str:
        """Executes JavaScript on the page.

        Args:
            expression: JavaScript expression to evaluate
        """
        try:
            self._initialize_browser()
            result = self._page.evaluate(expression)
            return json.dumps({"status": "success", "result": result})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def save_pdf(self, path: str) -> str:
        """Generates a PDF of the current page. Chromium only.

        Args:
            path: File path to save the PDF
        """
        try:
            self._initialize_browser()
            self._page.pdf(path=path)
            return json.dumps({"status": "success", "path": path})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e), "path": path})

    def get_console_messages(self) -> str:
        """Gets console messages from the browser (up to last 200)."""
        return json.dumps({"status": "success", "messages": self._console_messages})

    def get_network_requests(self) -> str:
        """Gets recent network requests (up to last 100)."""
        return json.dumps({"status": "success", "requests": self._network_requests})

    def get_recording(self) -> str:
        """Gets the video recording path. Closes the session to finalize the video."""
        try:
            if not self.record_video_dir:
                return json.dumps({"status": "error", "message": "Video recording not enabled"})
            if not self._page:
                return json.dumps({"status": "error", "message": "No active session"})
            video = self._page.video
            if not video:
                return json.dumps({"status": "error", "message": "No video available"})
            path = video.path()
            self._cleanup()
            return json.dumps({"status": "success", "path": path})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e)})
