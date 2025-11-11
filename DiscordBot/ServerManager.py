"""
Discord bot bringing the client online and exposing commands:
`/hello` -> replies with "Hello world!".

Usage:
  - Install dependency: pip install discord.py
  - Put credentials in `config/.env`
  - Run: python -m DiscordBot.ServerManager

Config keys (config/.env):
  - DISCORD_TOKEN=...               # required
  - DISCORD_GUILD_IDS=1,2,3         # single or multiple guild (server) IDs
"""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands
from Utils.env import get_env, load_env_from_file, parse_int_ids
from Utils.UtilsServer import get_servers
from typing import Optional


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )


def run_bot() -> None:
    _configure_logging()
    
    # Create a list of all available servers (single shared instances)
    servers = get_servers()
    # Create lists of started / stopped servers
    def running_servers() -> list:
        return [s for s in servers if s.is_running()]
    def stopped_servers() -> list:
        return [s for s in servers if not s.is_running()]

    # Load env from config/.env before reading values
    load_env_from_file()

    token = get_env("DISCORD_TOKEN", required=True)
    # Support one or many IDs via DISCORD_GUILD_IDS (comma-separated)
    guild_ids_env = get_env("DISCORD_GUILD_IDS")
    guild_ids = parse_int_ids(guild_ids_env)
    guild_objs = [discord.Object(id=g) for g in guild_ids]

    # No privileged intents are required for this simple example.
    intents = discord.Intents.default()

    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
        logging.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "?")
        try:
            for g in guild_objs:
                synced = await bot.tree.sync(guild=g)
                logging.info("Synced %d command(s) to guild %s", len(synced), g.id)
            logging.info("Completed sync across %d guild(s).", len(guild_objs))
        except Exception:  # pragma: no cover - defensive logging
            logging.exception("Failed to sync application commands")

    # Restrict command registration to provided guilds
    guild_decorator = app_commands.guilds(*guild_objs) if guild_objs else (lambda x: x)

    # ====================================================
    # COMMANDS
    # ====================================================

    # ---------------------
    # TEST
    # ---------------------
    
    # TODO: Example, delete later
    @bot.tree.command(name="hello", description="Replies with Hello world!")
    @guild_decorator
    async def hello(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello world!", ephemeral=True)

    # ---------------------
    # START & STOP
    # ---------------------
    
    @bot.tree.command(name="start", description="Start a server")
    @guild_decorator
    async def start(interaction: discord.Interaction) -> None:
        choices = stopped_servers()
        if not choices:
            await interaction.response.send_message("No stopped servers available.", ephemeral=True)
            return

        # Discord select menus support up to 25 options
        MAX_OPTIONS = 25
        server_choices = choices[:MAX_OPTIONS]
        too_many = len(choices) > MAX_OPTIONS

        # One view that contains: server dropdown + Xms + Xmx + Start/Cancel
        class StartView(discord.ui.View):
            def __init__(self, servers_list: list) -> None:
                super().__init__(timeout=120)
                self.servers_list = servers_list
                self.selected_server_name: Optional[str] = None
                self.selected_xms: int = 2
                self.selected_xmx: int = 4

                self.add_item(ServerSelect(servers_list))
                self.add_item(XmsSelect())
                self.add_item(XmxSelect())
                self.add_item(StartButton())
                self.add_item(CancelButton())

            async def on_timeout(self) -> None:  # pragma: no cover - best effort
                try:
                    for child in self.children:
                        if isinstance(child, (discord.ui.Select, discord.ui.Button)):
                            child.disabled = True
                except Exception:
                    pass

            def resolve_selected_server(self):
                if not self.selected_server_name:
                    return None
                return next(
                    (s for s in self.servers_list if (s.name or "") == self.selected_server_name),
                    None,
                )

        class ServerSelect(discord.ui.Select):
            def __init__(self, servers_list: list) -> None:
                options = [
                    discord.SelectOption(
                        label=srv.name or "(unnamed)",
                        value=srv.name or "",
                    )
                    for srv in servers_list
                ]
                super().__init__(
                    placeholder="Server",
                    min_values=1,
                    max_values=1,
                    options=options,
                )

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                if self.view and isinstance(self.view, StartView):
                    self.view.selected_server_name = self.values[0]
                await i.response.defer()

        class XmsSelect(discord.ui.Select):
            def __init__(self) -> None:
                default_xms = 2
                xms_values = list(range(1, 17)) + [20, 24]
                options = [
                    discord.SelectOption(
                        label=f"Xms {gb}G",
                        value=str(gb),
                        default=(gb == default_xms),
                    )
                    for gb in xms_values
                ]
                super().__init__(
                    placeholder="Initial heap Xms",
                    min_values=1,
                    max_values=1,
                    options=options,
                )

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                try:
                    val = int(self.values[0])
                except Exception:
                    await i.response.edit_message(content="Invalid Xms value.", view=self.view)
                    return
                if self.view and isinstance(self.view, StartView):
                    self.view.selected_xms = val
                await i.response.defer()

        class XmxSelect(discord.ui.Select):
            def __init__(self) -> None:
                default_xmx = 4
                xmx_values = list(range(1, 17)) + [20, 24, 28, 32, 40, 48, 64]
                options = [
                    discord.SelectOption(
                        label=f"Xmx {gb}G",
                        value=str(gb),
                        default=(gb == default_xmx),
                    )
                    for gb in xmx_values
                ]
                super().__init__(
                    placeholder="Max heap Xmx",
                    min_values=1,
                    max_values=1,
                    options=options,
                )

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                try:
                    val = int(self.values[0])
                except Exception:
                    await i.response.edit_message(content="Invalid Xmx value.", view=self.view)
                    return
                if self.view and isinstance(self.view, StartView):
                    self.view.selected_xmx = val
                await i.response.defer()

        class StartButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Start", style=discord.ButtonStyle.success)

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                if not self.view or not isinstance(self.view, StartView):
                    await i.response.edit_message(content="Internal error.", view=None)
                    return
                view: StartView = self.view
                srv = view.resolve_selected_server()
                if not srv:
                    await i.response.edit_message(
                        content="Please select a server before starting.", view=view
                    )
                    return
                xms = view.selected_xms
                xmx = view.selected_xmx
                if xms <= 0 or xmx <= 0:
                    await i.response.edit_message(
                        content="Xmx/Xms must be positive.", view=view
                    )
                    return
                if xmx < xms:
                    await i.response.edit_message(
                        content="Xmx must be greater than or equal to Xms.", view=view
                    )
                    return

                # Apply and start
                srv.xms = xms
                srv.xmx = xmx
                pid = srv.start()
                if pid <= 0:
                    await i.response.edit_message(
                        content=f"Failed to start {srv.name} with Xmx={xmx}G Xms={xms}G.",
                        view=view,
                    )
                    return
                await i.response.edit_message(
                    content=f"Starting - {srv.name} (PID {pid}) with Xmx={xmx}G Xms={xms}G",
                    view=None,
                )

        class CancelButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                await i.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            ("Pick a server and memory, then Start:" + (" Showing first 25 servers." if too_many else "")),
            view=StartView(server_choices),
            ephemeral=True,
        )

    @bot.tree.command(name="stop", description="Stop a server")
    @guild_decorator
    async def stop(interaction: discord.Interaction) -> None:
        choices = running_servers()
        if not choices:
            await interaction.response.send_message("No running servers to stop.", ephemeral=True)
            return

        class StopSelect(discord.ui.Select):
            def __init__(self) -> None:
                options = [
                    discord.SelectOption(label=srv.name or "(unnamed)", value=srv.name or "")
                    for srv in choices
                ]
                super().__init__(
                    placeholder="Select a server to stop",
                    min_values=1,
                    max_values=1,
                    options=options,
                )

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                name = self.values[0]
                srv = next((s for s in servers if (s.name or "") == name), None)
                rc = srv.stop()
                await i.response.edit_message(content=f"Stopping - {srv.name}", view=None)

        class StopView(discord.ui.View):
            def __init__(self) -> None:
                super().__init__(timeout=60)
                self.add_item(StopSelect())

        await interaction.response.send_message("Pick a server to stop:", view=StopView(), ephemeral=True)

    bot.run(token)


if __name__ == "__main__":
    run_bot()
