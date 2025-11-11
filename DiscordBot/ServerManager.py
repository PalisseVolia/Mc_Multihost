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

        class StartSelect(discord.ui.Select):
            def __init__(self) -> None:
                options = [
                    discord.SelectOption(label=srv.name or "(unnamed)", value=srv.name or "")
                    for srv in choices
                ]
                super().__init__(
                    placeholder="Select a server to start",
                    min_values=1,
                    max_values=1,
                    options=options,
                )

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                name = self.values[0]
                srv = next((s for s in servers if (s.name or "") == name), None)
                srv.start()
                await i.response.edit_message(content=f"Starting - {srv.name}", view=None)

        class StartView(discord.ui.View):
            def __init__(self) -> None:
                super().__init__(timeout=60)
                self.add_item(StartSelect())

        await interaction.response.send_message("Pick a server to start:", view=StartView(), ephemeral=True)

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
