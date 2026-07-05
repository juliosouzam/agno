"""Discord Gateway interface for AgentOS.

This class does two things:
1. ``get_router()`` mounts the processing endpoint (``gateway_router``) into
   the AgentOS FastAPI app.
2. ``get_lifespan()`` starts/stops the gateway listener (``listener``) in a
   background thread with its own event loop. The listener relays Discord
   messages to the endpoint over plain HTTP — see ``__init__.py`` for the
   full architecture map.

Unlike DiscordInteractions, this requires the privileged Message Content
Intent (enable it under Bot settings in the Discord developer portal) and the
``discord.py`` package, but needs NO public URL, application id, or public
key. Set ``run_listener=False`` to mount only the endpoint and run the
listener as a separate process (same ``DISCORD_GATEWAY_SECRET`` on both
sides).
"""

from __future__ import annotations

import asyncio
import secrets as secrets_module
import threading
from contextlib import asynccontextmanager
from os import getenv
from typing import Any, AsyncIterator, Callable, List, Optional, Union

from fastapi import FastAPI
from fastapi.routing import APIRouter

from agno.agent import Agent, RemoteAgent
from agno.os.interfaces.base import BaseInterface
from agno.os.interfaces.discord.gateway_router import attach_gateway_routes
from agno.team import RemoteTeam, Team
from agno.utils.log import log_error, log_info, log_warning
from agno.workflow import RemoteWorkflow, Workflow


class DiscordGateway(BaseInterface):
    type = "discord_gateway"

    router: APIRouter

    def __init__(
        self,
        agent: Optional[Union[Agent, RemoteAgent]] = None,
        team: Optional[Union[Team, RemoteTeam]] = None,
        workflow: Optional[Union[Workflow, RemoteWorkflow]] = None,
        prefix: str = "/discord",
        tags: Optional[List[str]] = None,
        bot_token: Optional[str] = None,
        app_url: Optional[str] = None,
        gateway_secret: Optional[str] = None,
        reply_in_thread: bool = True,
        respond_to_dms: bool = True,
        run_listener: bool = True,
    ):
        self.agent = agent
        self.team = team
        self.workflow = workflow
        self.prefix = prefix
        self.tags = tags or ["Discord Gateway"]
        self.bot_token = bot_token or getenv("DISCORD_BOT_TOKEN")
        self.app_url = (app_url or getenv("DISCORD_GATEWAY_APP_URL") or "http://localhost:7777").rstrip("/")
        self.gateway_secret = gateway_secret or getenv("DISCORD_GATEWAY_SECRET") or secrets_module.token_urlsafe(32)
        self.reply_in_thread = reply_in_thread
        self.respond_to_dms = respond_to_dms
        self.run_listener = run_listener

        self._thread: Optional[threading.Thread] = None
        self._listener_loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[Any] = None

        if not (self.agent or self.team or self.workflow):
            raise ValueError("DiscordGateway requires an agent, team, or workflow")
        if not self.bot_token:
            raise ValueError("DISCORD_BOT_TOKEN is not set. Set the env var or pass bot_token.")
        if self.run_listener:
            # Fail fast at construction if the listener dependency is missing.
            # discord.py stays a lazy import so Interactions users never need it.
            try:
                import discord  # noqa: F401
            except (ImportError, ModuleNotFoundError):
                raise ImportError(
                    "`discord.py` is required for DiscordGateway with run_listener=True. "
                    "Install it with: pip install discord.py (or pip install 'agno[discord]')"
                )

    def get_router(self) -> APIRouter:
        self.router = attach_gateway_routes(
            router=APIRouter(prefix=self.prefix, tags=self.tags),  # type: ignore[arg-type]
            agent=self.agent,
            team=self.team,
            workflow=self.workflow,
            bot_token=self.bot_token,
            gateway_secret=self.gateway_secret,
            reply_in_thread=self.reply_in_thread,
            respond_to_dms=self.respond_to_dms,
        )
        return self.router

    def get_lifespan(self) -> Callable[[FastAPI], Any]:
        """App lifespan hook (collected by AgentOS): start the listener thread on
        startup, close the discord client and join the thread on shutdown."""

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            if self.run_listener:
                self._thread = threading.Thread(target=self._run_listener, name="discord-gateway", daemon=True)
                self._thread.start()
                log_info(f"Discord gateway listener started, relaying to {self._events_url()}")
            yield
            if self._client is not None and self._listener_loop is not None and self._listener_loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(self._client.close(), self._listener_loop).result(timeout=10)
                except Exception as e:
                    log_warning(f"Discord gateway client close failed: {e}")
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=10)
                if self._thread.is_alive():
                    log_warning("Discord gateway thread did not stop within 10s")

        return lifespan

    def _events_url(self) -> str:
        return f"{self.app_url}{self.prefix}/gateway/events"

    def _run_listener(self) -> None:
        """Thread target: run the listener on its own event loop until closed."""
        from agno.os.interfaces.discord.listener import DiscordGatewayListener

        assert self.bot_token is not None  # validated in __init__

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._listener_loop = loop
        try:
            self._client = DiscordGatewayListener(
                events_url=self._events_url(),
                gateway_secret=self.gateway_secret,
                respond_to_dms=self.respond_to_dms,
            )
            loop.run_until_complete(self._client.start(self.bot_token))
        except Exception as e:
            log_error(f"Discord gateway listener stopped: {e}")
        finally:
            # client.start() returns as soon as the connection drops, but the
            # close() task scheduled from the lifespan thread (and aiohttp's
            # internal teardown) may still be running on this loop — drain
            # before closing or they get destroyed mid-flight
            try:
                pending = asyncio.all_tasks(loop)
                if pending:
                    loop.run_until_complete(
                        asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=10)
                    )
                loop.run_until_complete(loop.shutdown_asyncgens())
                # Give aiohttp's SSL transports a beat to run their close callbacks
                loop.run_until_complete(asyncio.sleep(0.25))
            except Exception:
                pass
            loop.close()
