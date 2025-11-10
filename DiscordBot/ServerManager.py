"""
Basic Discord bot bringing the client online and exposing a single
slash command: `/hello` -> replies with "Hello world!".

Usage:
  - Install dependency: pip install discord.py
  - Put credentials in `config/.env`
  - Run: python -m DiscordBot.ServerManager

Config keys (config/.env):
  - DISCORD_TOKEN=...               # required
  - DISCORD_GUILD_IDS=1,2,3         # optional; single or multiple guild (server) IDs
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands
from Utils.env import get_env, load_env_from_file, parse_int_ids


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )


def run_bot() -> None:
    _configure_logging()

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

    @bot.tree.command(name="hello", description="Replies with Hello world!")
    @guild_decorator
    async def hello(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Hello world!", ephemeral=True)

    bot.run(token)


if __name__ == "__main__":
    run_bot()
