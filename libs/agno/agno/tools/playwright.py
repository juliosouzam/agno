"""PlaywrightTools — local browser automation via Playwright.

Provides tools for navigating websites, taking screenshots, extracting content,
generating PDFs, and recording video using a local Playwright browser.

Requires:
    pip install playwright
    playwright install chromium  # or firefox, webkit
"""

import json
import re
from typing import Any, Dict, List, Optional

from agno.tools import Toolkit
from agno.utils.log import log_debug

try:
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
except ImportError:
    raise ImportError(
        "`playwright` not installed. Please install using `pip install playwright` "
        "and run `playwright install chromium`"
    )


PLAYWRIGHT_INSTRUCTIONS = """
## PlaywrightTools Usage

You have access to a local browser for web automation. Core tools:

- `navigate_to(url)`: Go to a URL
- `go_back()`: Navigate back in browser history
- `get_page_content()`: Get the current page content (text or HTML)
- `screenshot(path)`: Save a screenshot
- `close_session()`: Close the browser when done

Advanced tools (if enabled):
- `save_pdf(path)`: Generate a PDF of the current page (Chromium only)
- `get_console_messages()`: Get browser console output
- `get_network_requests()`: Get recent network requests
- `evaluate_js(expression)`: Execute JavaScript on the page
- `get_recording()`: Get the video recording path (if recording enabled)

The browser session persists between calls. Always call close_session() when finished.
"""


class PlaywrightTools(Toolkit):
    """Local browser automation via Playwright.

    Provides tools for navigating websites, taking screenshots, extracting
    content, generating PDFs, and recording video using a local browser.

    Args:
        headless: Run browser in headless mode. Defaults to True.
        browser: Browser to use: chromium, firefox, or webkit. Defaults to chromium.
        user_agent: Custom user agent string.
        viewport_width: Browser viewport width. Defaults to 1280.
        viewport_height: Browser viewport height. Defaults to 720.
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
        blocked_url_patterns: URL patterns to block (e.g., ads, trackers).
        parse_html: Extract text content instead of raw HTML. Defaults to True.
        max_content_length: Maximum content length before truncation. Defaults to 100000.
    """

    def __init__(
        self,
        headless: bool = True,
        browser: str = "chromium",
        user_agent: Optional[str] = None,
        viewport_width: int = 1280,
        viewport_height: int = 720,
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
        blocked_url_patterns: Optional[List[str]] = None,
        parse_html: bool = True,
        max_content_length: Optional[int] = 100000,
        **kwargs,
    ):
        self.headless = headless
        self.browser_type = browser
        self.user_agent = user_agent
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.timeout_ms = timeout_ms
        self.record_video_dir = record_video_dir
        self.blocked_url_patterns = blocked_url_patterns or []
        self.parse_html = parse_html
        self.max_content_length = max_content_length
        self.enable_get_console_messages = all or enable_get_console_messages
        self.enable_get_network_requests = all or enable_get_network_requests

        # Sync playwright state
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

        # Async playwright state
        self._async_playwright: Any = None
        self._async_browser: Any = None
        self._async_context: Any = None
        self._async_page: Any = None

        # Console/network buffers (populated via event listeners)
        self._console_messages: List[Dict[str, Any]] = []
        self._network_requests: List[Dict[str, Any]] = []

        # Build tool lists
        tools: List[Any] = []
        async_tools: List[tuple] = []

        if all or enable_navigate_to:
            tools.append(self.navigate_to)
            async_tools.append((self.anavigate_to, "navigate_to"))
        if all or enable_go_back:
            tools.append(self.go_back)
            async_tools.append((self.ago_back, "go_back"))
        if all or enable_screenshot:
            tools.append(self.screenshot)
            async_tools.append((self.ascreenshot, "screenshot"))
        if all or enable_get_page_content:
            tools.append(self.get_page_content)
            async_tools.append((self.aget_page_content, "get_page_content"))
        if all or enable_close_session:
            tools.append(self.close_session)
            async_tools.append((self.aclose_session, "close_session"))
        if all or enable_click:
            tools.append(self.click)
            async_tools.append((self.aclick, "click"))
        if all or enable_type:
            tools.append(self.type_text)
            async_tools.append((self.atype_text, "type_text"))
        if all or enable_fill_form:
            tools.append(self.fill_form)
            async_tools.append((self.afill_form, "fill_form"))
        if all or enable_get_element_text:
            tools.append(self.get_element_text)
            async_tools.append((self.aget_element_text, "get_element_text"))
        if all or enable_wait_for:
            tools.append(self.wait_for)
            async_tools.append((self.await_for, "wait_for"))
        if all or enable_evaluate_js:
            tools.append(self.evaluate_js)
            async_tools.append((self.aevaluate_js, "evaluate_js"))
        if all or enable_save_pdf:
            tools.append(self.save_pdf)
            async_tools.append((self.asave_pdf, "save_pdf"))
        if all or enable_get_console_messages:
            tools.append(self.get_console_messages)
            async_tools.append((self.aget_console_messages, "get_console_messages"))
        if all or enable_get_network_requests:
            tools.append(self.get_network_requests)
            async_tools.append((self.aget_network_requests, "get_network_requests"))
        # Recording tool only if video dir is set
        if record_video_dir:
            tools.append(self.get_recording)
            async_tools.append((self.aget_recording, "get_recording"))

        super().__init__(
            name="playwright_tools",
            tools=tools,
            async_tools=async_tools,
            instructions=PLAYWRIGHT_INSTRUCTIONS,
            **kwargs,
        )

    def _initialize_browser(self):
        """Initialize sync browser if not already initialized."""
        if self._page:
            return

        self._playwright = sync_playwright().start()
        browser_launcher = getattr(self._playwright, self.browser_type)
        browser = browser_launcher.launch(headless=self.headless)
        self._browser = browser

        context_options: Dict[str, Any] = {
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
        }
        if self.user_agent:
            context_options["user_agent"] = self.user_agent
        if self.record_video_dir:
            context_options["record_video_dir"] = self.record_video_dir

        context = browser.new_context(**context_options)
        context.set_default_timeout(self.timeout_ms)
        page = context.new_page()

        # Set up URL blocking
        for pattern in self.blocked_url_patterns:
            context.route(pattern, lambda route: route.abort())

        # Set up console/network listeners if enabled
        if self.enable_get_console_messages:
            page.on("console", self._on_console_message)
        if self.enable_get_network_requests:
            page.on("request", self._on_request)

        self._context = context
        self._page = page

        log_debug(f"Playwright browser initialized: {self.browser_type}, headless={self.headless}")

    def _on_console_message(self, msg):
        """Handle console message events."""
        self._console_messages.append(
            {
                "type": msg.type,
                "text": msg.text,
                "location": f"{msg.location.get('url', '')}:{msg.location.get('lineNumber', '')}",
            }
        )
        # Keep only last 200 messages
        if len(self._console_messages) > 200:
            self._console_messages = self._console_messages[-200:]

    def _on_request(self, request):
        """Handle network request events."""
        self._network_requests.append(
            {
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
            }
        )
        # Keep only last 100 requests
        if len(self._network_requests) > 100:
            self._network_requests = self._network_requests[-100:]

    def _cleanup(self):
        """Clean up sync browser resources."""
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

    def _extract_text_content(self, html: str) -> str:
        """Extract visible text content from HTML."""
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        html = re.sub(r"<[^>]+>", " ", html)
        html = html.replace("&nbsp;", " ")
        html = html.replace("&amp;", "&")
        html = html.replace("&lt;", "<")
        html = html.replace("&gt;", ">")
        html = html.replace("&quot;", '"')
        html = html.replace("&#39;", "'")
        html = re.sub(r"\s+", " ", html)
        return html.strip()

    def _truncate_content(self, content: str) -> str:
        """Truncate content if it exceeds max_content_length."""
        if self.max_content_length is None or len(content) <= self.max_content_length:
            return content
        truncated = content[: self.max_content_length]
        return f"{truncated}\n\n[Content truncated. Original: {len(content)} chars, showing first {self.max_content_length}.]"

    # -------------------------------------------------------------------------
    # Sync tools
    # -------------------------------------------------------------------------

    def navigate_to(self, url: str) -> str:
        """Navigates to a URL.

        Args:
            url: The URL to navigate to

        Returns:
            JSON string with navigation status, title, and URL
        """
        try:
            self._initialize_browser()
            if self._page:
                self._page.goto(url, wait_until="networkidle")
            title = self._page.title() if self._page else ""
            return json.dumps({"status": "success", "title": title, "url": url})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e), "url": url})

    def go_back(self) -> str:
        """Navigates back in browser history.

        Returns:
            JSON string with navigation status
        """
        try:
            self._initialize_browser()
            if self._page:
                self._page.go_back(wait_until="networkidle")
            url = self._page.url if self._page else ""
            title = self._page.title() if self._page else ""
            return json.dumps({"status": "success", "title": title, "url": url})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e)})

    def screenshot(self, path: str, full_page: bool = True) -> str:
        """Takes a screenshot of the current page.

        Args:
            path: File path to save the screenshot
            full_page: Whether to capture the full scrollable page

        Returns:
            JSON string confirming screenshot was saved
        """
        try:
            self._initialize_browser()
            if self._page:
                self._page.screenshot(path=path, full_page=full_page)
            return json.dumps({"status": "success", "path": path})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e), "path": path})

    def get_page_content(self) -> str:
        """Gets the content of the current page.

        Returns:
            JSON string with page content (text if parse_html=True, otherwise HTML)
        """
        try:
            self._initialize_browser()
            if not self._page:
                return json.dumps({"status": "error", "message": "No page available"})

            raw_content = self._page.content()
            url = self._page.url
            title = self._page.title()

            if self.parse_html:
                content = self._extract_text_content(raw_content)
            else:
                content = raw_content

            content = self._truncate_content(content)
            return json.dumps({"status": "success", "url": url, "title": title, "content": content})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e)})

    def close_session(self) -> str:
        """Closes the browser session.

        Returns:
            JSON string with closure status
        """
        try:
            self._cleanup()
            return json.dumps({"status": "closed", "message": "Browser session closed"})
        except Exception as e:
            return json.dumps({"status": "warning", "message": f"Cleanup completed with warning: {str(e)}"})

    def click(self, selector: str) -> str:
        """Clicks an element on the page.

        Args:
            selector: CSS selector of element to click

        Returns:
            JSON string with click status
        """
        try:
            self._initialize_browser()
            if self._page:
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

        Returns:
            JSON string with typing status
        """
        try:
            self._initialize_browser()
            if self._page:
                self._page.fill(selector, text)
            return json.dumps({"status": "success", "selector": selector, "text_length": len(text)})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e), "selector": selector})

    def fill_form(self, form_data: Dict[str, str]) -> str:
        """Fills multiple form fields at once.

        Args:
            form_data: Dictionary mapping CSS selectors to values

        Returns:
            JSON string with fill status
        """
        try:
            self._initialize_browser()
            filled: List[str] = []
            if self._page:
                for selector, value in form_data.items():
                    self._page.fill(selector, value)
                    filled.append(selector)
            return json.dumps({"status": "success", "filled_fields": filled})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e)})

    def get_element_text(self, selector: str) -> str:
        """Gets text content of a specific element.

        Args:
            selector: CSS selector of element

        Returns:
            JSON string with the text content
        """
        try:
            self._initialize_browser()
            if self._page:
                element = self._page.query_selector(selector)
                if element:
                    text = element.inner_text()
                    return json.dumps({"status": "success", "text": text, "selector": selector})
            return json.dumps({"status": "error", "message": f"Element not found: {selector}"})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e)})

    def wait_for(self, selector: str, timeout_ms: Optional[int] = None) -> str:
        """Waits for an element to appear on the page.

        Args:
            selector: CSS selector to wait for
            timeout_ms: Maximum time to wait in milliseconds

        Returns:
            JSON string with wait status
        """
        try:
            self._initialize_browser()
            if self._page:
                self._page.wait_for_selector(selector, timeout=timeout_ms or self.timeout_ms)
            return json.dumps({"status": "success", "selector": selector})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e), "selector": selector})

    def evaluate_js(self, expression: str) -> str:
        """Executes JavaScript on the page.

        Args:
            expression: JavaScript expression to evaluate

        Returns:
            JSON string with the evaluation result
        """
        try:
            self._initialize_browser()
            if self._page:
                result = self._page.evaluate(expression)
                return json.dumps({"status": "success", "result": result})
            return json.dumps({"status": "error", "message": "No page available"})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def save_pdf(self, path: str) -> str:
        """Generates a PDF of the current page. Chromium only.

        Args:
            path: File path to save the PDF

        Returns:
            JSON string confirming PDF was saved
        """
        try:
            self._initialize_browser()
            if self._page:
                self._page.pdf(path=path)
            return json.dumps({"status": "success", "path": path})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e), "path": path})

    def get_console_messages(self) -> str:
        """Gets console messages from the browser.

        Returns:
            JSON string with console messages (up to last 200)
        """
        try:
            return json.dumps({"status": "success", "messages": self._console_messages})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def get_network_requests(self) -> str:
        """Gets recent network requests from the browser.

        Returns:
            JSON string with network requests (up to last 100)
        """
        try:
            return json.dumps({"status": "success", "requests": self._network_requests})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def get_recording(self) -> str:
        """Gets the video recording path. Closes the session to finalize the video.

        Returns:
            JSON string with the video file path
        """
        try:
            if not self.record_video_dir:
                return json.dumps({"status": "error", "message": "Video recording not enabled"})
            if not self._page:
                return json.dumps({"status": "error", "message": "No active session"})

            video = self._page.video
            if not video:
                return json.dumps({"status": "error", "message": "No video available"})

            # Video path is only available after context close
            path = video.path()
            self._cleanup()
            return json.dumps({"status": "success", "path": path})
        except Exception as e:
            self._cleanup()
            return json.dumps({"status": "error", "message": str(e)})

    # -------------------------------------------------------------------------
    # Async tools
    # -------------------------------------------------------------------------

    async def _ainitialize_browser(self):
        """Initialize async browser if not already initialized."""
        if self._async_page:
            return

        self._async_playwright = await async_playwright().start()
        browser_launcher = getattr(self._async_playwright, self.browser_type)
        browser = await browser_launcher.launch(headless=self.headless)
        self._async_browser = browser

        context_options: Dict[str, Any] = {
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
        }
        if self.user_agent:
            context_options["user_agent"] = self.user_agent
        if self.record_video_dir:
            context_options["record_video_dir"] = self.record_video_dir

        context = await browser.new_context(**context_options)
        context.set_default_timeout(self.timeout_ms)
        page = await context.new_page()

        # Set up URL blocking
        for pattern in self.blocked_url_patterns:
            await context.route(pattern, lambda route: route.abort())

        # Set up console/network listeners if enabled
        if self.enable_get_console_messages:
            page.on("console", self._on_console_message)
        if self.enable_get_network_requests:
            page.on("request", self._on_request)

        self._async_context = context
        self._async_page = page

        log_debug(f"Async Playwright browser initialized: {self.browser_type}, headless={self.headless}")

    async def _acleanup(self):
        """Clean up async browser resources."""
        if self._async_context:
            await self._async_context.close()
            self._async_context = None
        if self._async_browser:
            await self._async_browser.close()
            self._async_browser = None
        if self._async_playwright:
            await self._async_playwright.stop()
            self._async_playwright = None
        self._async_page = None
        self._console_messages = []
        self._network_requests = []

    async def anavigate_to(self, url: str) -> str:
        """Navigates to a URL asynchronously.

        Args:
            url: The URL to navigate to

        Returns:
            JSON string with navigation status
        """
        try:
            await self._ainitialize_browser()
            if self._async_page:
                await self._async_page.goto(url, wait_until="networkidle")
            title = await self._async_page.title() if self._async_page else ""
            return json.dumps({"status": "success", "title": title, "url": url})
        except Exception as e:
            await self._acleanup()
            return json.dumps({"status": "error", "message": str(e), "url": url})

    async def ago_back(self) -> str:
        """Navigates back in browser history asynchronously.

        Returns:
            JSON string with navigation status
        """
        try:
            await self._ainitialize_browser()
            if self._async_page:
                await self._async_page.go_back(wait_until="networkidle")
            url = self._async_page.url if self._async_page else ""
            title = await self._async_page.title() if self._async_page else ""
            return json.dumps({"status": "success", "title": title, "url": url})
        except Exception as e:
            await self._acleanup()
            return json.dumps({"status": "error", "message": str(e)})

    async def ascreenshot(self, path: str, full_page: bool = True) -> str:
        """Takes a screenshot asynchronously.

        Args:
            path: File path to save the screenshot
            full_page: Whether to capture the full scrollable page

        Returns:
            JSON string confirming screenshot was saved
        """
        try:
            await self._ainitialize_browser()
            if self._async_page:
                await self._async_page.screenshot(path=path, full_page=full_page)
            return json.dumps({"status": "success", "path": path})
        except Exception as e:
            await self._acleanup()
            return json.dumps({"status": "error", "message": str(e), "path": path})

    async def aget_page_content(self) -> str:
        """Gets the content of the current page asynchronously.

        Returns:
            JSON string with page content
        """
        try:
            await self._ainitialize_browser()
            if not self._async_page:
                return json.dumps({"status": "error", "message": "No page available"})

            raw_content = await self._async_page.content()
            url = self._async_page.url
            title = await self._async_page.title()

            if self.parse_html:
                content = self._extract_text_content(raw_content)
            else:
                content = raw_content

            content = self._truncate_content(content)
            return json.dumps({"status": "success", "url": url, "title": title, "content": content})
        except Exception as e:
            await self._acleanup()
            return json.dumps({"status": "error", "message": str(e)})

    async def aclose_session(self) -> str:
        """Closes the browser session asynchronously.

        Returns:
            JSON string with closure status
        """
        try:
            await self._acleanup()
            return json.dumps({"status": "closed", "message": "Browser session closed"})
        except Exception as e:
            return json.dumps({"status": "warning", "message": f"Cleanup completed with warning: {str(e)}"})

    async def aclick(self, selector: str) -> str:
        """Clicks an element asynchronously.

        Args:
            selector: CSS selector of element to click

        Returns:
            JSON string with click status
        """
        try:
            await self._ainitialize_browser()
            if self._async_page:
                await self._async_page.click(selector)
            return json.dumps({"status": "success", "selector": selector})
        except Exception as e:
            await self._acleanup()
            return json.dumps({"status": "error", "message": str(e), "selector": selector})

    async def atype_text(self, selector: str, text: str) -> str:
        """Types text into an input element asynchronously.

        Args:
            selector: CSS selector of input element
            text: Text to type

        Returns:
            JSON string with typing status
        """
        try:
            await self._ainitialize_browser()
            if self._async_page:
                await self._async_page.fill(selector, text)
            return json.dumps({"status": "success", "selector": selector, "text_length": len(text)})
        except Exception as e:
            await self._acleanup()
            return json.dumps({"status": "error", "message": str(e), "selector": selector})

    async def afill_form(self, form_data: Dict[str, str]) -> str:
        """Fills multiple form fields asynchronously.

        Args:
            form_data: Dictionary mapping CSS selectors to values

        Returns:
            JSON string with fill status
        """
        try:
            await self._ainitialize_browser()
            filled: List[str] = []
            if self._async_page:
                for selector, value in form_data.items():
                    await self._async_page.fill(selector, value)
                    filled.append(selector)
            return json.dumps({"status": "success", "filled_fields": filled})
        except Exception as e:
            await self._acleanup()
            return json.dumps({"status": "error", "message": str(e)})

    async def aget_element_text(self, selector: str) -> str:
        """Gets text content of a specific element asynchronously.

        Args:
            selector: CSS selector of element

        Returns:
            JSON string with the text content
        """
        try:
            await self._ainitialize_browser()
            if self._async_page:
                element = await self._async_page.query_selector(selector)
                if element:
                    text = await element.inner_text()
                    return json.dumps({"status": "success", "text": text, "selector": selector})
            return json.dumps({"status": "error", "message": f"Element not found: {selector}"})
        except Exception as e:
            await self._acleanup()
            return json.dumps({"status": "error", "message": str(e)})

    async def await_for(self, selector: str, timeout_ms: Optional[int] = None) -> str:
        """Waits for an element to appear asynchronously.

        Args:
            selector: CSS selector to wait for
            timeout_ms: Maximum time to wait in milliseconds

        Returns:
            JSON string with wait status
        """
        try:
            await self._ainitialize_browser()
            if self._async_page:
                await self._async_page.wait_for_selector(selector, timeout=timeout_ms or self.timeout_ms)
            return json.dumps({"status": "success", "selector": selector})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e), "selector": selector})

    async def aevaluate_js(self, expression: str) -> str:
        """Executes JavaScript asynchronously.

        Args:
            expression: JavaScript expression to evaluate

        Returns:
            JSON string with the evaluation result
        """
        try:
            await self._ainitialize_browser()
            if self._async_page:
                result = await self._async_page.evaluate(expression)
                return json.dumps({"status": "success", "result": result})
            return json.dumps({"status": "error", "message": "No page available"})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    async def asave_pdf(self, path: str) -> str:
        """Generates a PDF asynchronously. Chromium only.

        Args:
            path: File path to save the PDF

        Returns:
            JSON string confirming PDF was saved
        """
        try:
            await self._ainitialize_browser()
            if self._async_page:
                await self._async_page.pdf(path=path)
            return json.dumps({"status": "success", "path": path})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e), "path": path})

    async def aget_console_messages(self) -> str:
        """Gets console messages asynchronously.

        Returns:
            JSON string with console messages
        """
        try:
            return json.dumps({"status": "success", "messages": self._console_messages})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    async def aget_network_requests(self) -> str:
        """Gets recent network requests asynchronously.

        Returns:
            JSON string with network requests
        """
        try:
            return json.dumps({"status": "success", "requests": self._network_requests})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    async def aget_recording(self) -> str:
        """Gets the video recording path asynchronously.

        Returns:
            JSON string with the video file path
        """
        try:
            if not self.record_video_dir:
                return json.dumps({"status": "error", "message": "Video recording not enabled"})
            if not self._async_page:
                return json.dumps({"status": "error", "message": "No active session"})

            video = self._async_page.video
            if not video:
                return json.dumps({"status": "error", "message": "No video available"})

            path = await video.path()
            await self._acleanup()
            return json.dumps({"status": "success", "path": path})
        except Exception as e:
            await self._acleanup()
            return json.dumps({"status": "error", "message": str(e)})
