"""
Real Estate Scout - Multi-Context Research Agent
Combines Browser, Web Search, and Database providers for apartment hunting.
Requires: OPENAI_API_KEY, Node.js 18+
"""

import asyncio
import tempfile
from pathlib import Path

from agno.agent import Agent
from agno.context.browser import BrowserContextProvider, PlaywrightMCPBackend
from agno.context.database import DatabaseContextProvider
from agno.context.web import ExaMCPBackend, WebContextProvider
from agno.models.openai import OpenAIResponses
from sqlalchemy import create_engine, text

DB_PATH = Path(tempfile.gettempdir()) / "apartment_scout.sqlite"
if DB_PATH.exists():
    DB_PATH.unlink()

engine = create_engine(f"sqlite:///{DB_PATH}")
with engine.begin() as conn:
    conn.execute(
        text("""
        CREATE TABLE apartments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            price INTEGER,
            location TEXT,
            listing_url TEXT,
            bedrooms INTEGER,
            description TEXT,
            walk_score INTEGER,
            transit_score INTEGER,
            neighborhood_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    )

provider_model = OpenAIResponses(id="gpt-5.5")

browser = BrowserContextProvider(
    backend=PlaywrightMCPBackend(headless=True),
    model=provider_model,
)

web = WebContextProvider(
    backend=ExaMCPBackend(),
    model=provider_model,
)

db = DatabaseContextProvider(
    id="apartments",
    name="Apartment Database",
    sql_engine=engine,
    readonly_engine=engine,
    model=provider_model,
)

tools = [*browser.get_tools(), *web.get_tools(), *db.get_tools()]

agent = Agent(
    model=OpenAIResponses(id="gpt-5.5"),
    tools=tools,
    instructions=(
        "You are an apartment hunting assistant with three tools:\n"
        "- query_browser: Navigate Craigslist, view listings, take screenshots\n"
        "- query_web: Search for neighborhood info, Walk Scores, safety data\n"
        "- query_apartments: Save and query apartment findings\n\n"
        "For each apartment search:\n"
        "1. Browse listings on Craigslist\n"
        "2. For promising listings, research the neighborhood\n"
        "3. Save findings to the database\n"
        "4. Recommend the best options\n"
    ),
    markdown=True,
)

SEARCH_PROMPT = """
Help me find an apartment in Jersey City, NJ. Budget is $2000-3000/month.

1. Use query_browser to go to https://newjersey.craigslist.org/search/jersey-city-nj/apa
   and find 2 apartment listings in my budget range.

2. Use query_browser to go to https://www.padmapper.com/apartments/jersey-city-nj
   and find 1 more apartment listing in my budget range.

3. For each apartment, use query_web to find the Walk Score for that neighborhood.

4. Save each apartment to the database with the neighborhood walkability info.

5. Recommend the best option for a young professional commuting to Manhattan.
"""


async def main() -> None:
    print("Setting up context providers...")
    await browser.asetup()
    await web.asetup()

    try:
        await agent.aprint_response(SEARCH_PROMPT)

        print("\nDatabase contents:")
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT title, price, location, walk_score, neighborhood_notes FROM apartments"
                )
            )
            rows = result.fetchall()
            if rows:
                for row in rows:
                    print(f"- {row[0]}: ${row[1]}/mo | {row[2]} | Walk Score: {row[3]}")
            else:
                print("(No apartments saved)")

    finally:
        await browser.aclose()
        await web.aclose()


if __name__ == "__main__":
    asyncio.run(main())
