"""
Playwright Tools
================

Local browser automation via Playwright. No API keys needed.

Requires:
    pip install playwright
    playwright install chromium
"""

from agno.agent import Agent
from agno.models.openai import OpenAIResponses
from agno.tools.playwright import PlaywrightTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

# Example 1: Enable all browser tools
agent_all = Agent(
    model=OpenAIResponses(id="gpt-5.5"),
    tools=[PlaywrightTools(headless=True, all=True)],
    instructions=["Always close the browser session when done."],
    markdown=True,
)

# Example 2: Read-only agent (no form interaction)
agent_readonly = Agent(
    model=OpenAIResponses(id="gpt-5.5"),
    tools=[
        PlaywrightTools(
            headless=True,
            enable_navigate_to=True,
            enable_screenshot=True,
            enable_get_page_content=True,
            enable_close_session=True,
            enable_click=False,
            enable_type=False,
        )
    ],
    instructions=["Always close the browser session when done."],
    markdown=True,
)

# Example 3: Form automation agent
agent_forms = Agent(
    model=OpenAIResponses(id="gpt-5.5"),
    tools=[
        PlaywrightTools(
            headless=True,
            enable_navigate_to=True,
            enable_click=True,
            enable_type=True,
            enable_fill_form=True,
            enable_screenshot=True,
            enable_close_session=True,
        )
    ],
    instructions=["Always close the browser session when done."],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Extract information from a website
    agent_all.print_response(
        "Go to https://news.ycombinator.com and tell me the top 3 stories",
        stream=True,
    )

    # Take a screenshot
    # agent_readonly.print_response(
    #     "Go to https://example.com and take a screenshot at /tmp/example.png",
    #     stream=True,
    # )

    # Fill out a form
    # agent_forms.print_response(
    #     "Go to https://httpbin.org/forms/post and fill out the form with test data",
    #     stream=True,
    # )
