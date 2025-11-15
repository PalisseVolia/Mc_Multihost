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
from Utils.UtilsServer import get_servers, get_available_memory_gb, get_server_info
from Utils.McJava import resolve_java_for_server
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
    # INFO
    # ---------------------

    @bot.tree.command(name="info", description="Show server info")
    @guild_decorator
    async def info(interaction: discord.Interaction) -> None:
        # Build selectable server list (all discovered servers)
        choices = servers
        if not choices:
            await interaction.response.send_message("No servers found.", ephemeral=True)
            return

        MAX_OPTIONS = 25
        server_choices = choices[:MAX_OPTIONS]
        too_many = len(choices) > MAX_OPTIONS

        def build_embed_for(name: str) -> discord.Embed:
            info_map = get_server_info()
            entry = info_map.get(name) if isinstance(info_map, dict) else None
            title = f"{name} — Info"
            embed = discord.Embed(title=title, color=discord.Color.blurple())
            if not isinstance(entry, dict):
                embed.add_field(name="Description", value="No description set.", inline=False)
                embed.add_field(name="IP", value="N/A", inline=False)
                embed.add_field(name="Places of Interest", value="No places recorded yet.", inline=False)
                return embed
            desc = entry.get("description") or entry.get("desc") or "No description set."
            ip = entry.get("ip") or entry.get("address") or "N/A"
            places = entry.get("places") or entry.get("poi") or []
            embed.add_field(name="Description", value=str(desc), inline=False)
            embed.add_field(name="IP", value=str(ip), inline=False)
            poi_lines: list[str] = []
            if isinstance(places, list):
                for p in places:
                    if not isinstance(p, dict):
                        continue
                    pname = str(p.get("name") or p.get("label") or "Place")
                    x = p.get("x")
                    y = p.get("y")
                    z = p.get("z")
                    # Accept alternative key forms
                    if x is None and "xyz" in p and isinstance(p["xyz"], (list, tuple)) and len(p["xyz"]) >= 3:
                        x, y, z = p["xyz"][0], p["xyz"][1], p["xyz"][2]
                    try:
                        poi_lines.append(f"- {pname} ({int(x)}, {int(y)}, {int(z)})")
                    except Exception:
                        # Fallback to raw representation
                        poi_lines.append(f"- {pname}")
            value = "\n".join(poi_lines) if poi_lines else "No places recorded yet."
            # Discord limits field values to 1024 chars
            if len(value) > 1024:
                value = value[:1010] + "... (truncated)"
            embed.add_field(name="Places of Interest", value=value, inline=False)
            return embed

        class InfoSelect(discord.ui.Select):
            def __init__(self, servers_list: list) -> None:
                options = [
                    discord.SelectOption(label=srv.name or "(unnamed)", value=srv.name or "")
                    for srv in servers_list
                ]
                super().__init__(
                    placeholder="Select a server",
                    min_values=1,
                    max_values=1,
                    options=options,
                )

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                name = self.values[0]
                embed = build_embed_for(name)
                await i.response.edit_message(content=None, embed=embed, view=None)

        class InfoView(discord.ui.View):
            def __init__(self) -> None:
                super().__init__(timeout=60)
                self.add_item(InfoSelect(server_choices))

        note = " Showing first 25 servers." if too_many else ""
        await interaction.response.send_message(
            "Pick a server to view its info:" + note,
            view=InfoView(),
            ephemeral=True,
        )

    # ---------------------
    # MINECRAFT COMMANDS
    # ---------------------
    
    # Helper UI pieces reused by commands below
    def _server_select_options(servers_list: list) -> list[discord.SelectOption]:
        return [
            discord.SelectOption(label=srv.name or "(unnamed)", value=srv.name or "")
            for srv in servers_list
        ]

    # /whitelist (add <player>)
    @bot.tree.command(name="whitelist", description="Add a player to the server whitelist")
    @guild_decorator
    async def whitelist(interaction: discord.Interaction) -> None:
        choices = running_servers()
        if not choices:
            await interaction.response.send_message("No running servers available.", ephemeral=True)
            return

        class WhitelistView(discord.ui.View):
            def __init__(self, servers_list: list) -> None:
                super().__init__(timeout=120)
                self.servers_list = servers_list
                self.selected_server_name: Optional[str] = None
                self.player_name: Optional[str] = None
                self.add_item(ServerSelect(servers_list))
                self.add_item(SetPlayerButton())
                self.add_item(ExecuteButton())
                self.add_item(CancelButton())

            def resolve_selected_server(self):
                if not self.selected_server_name:
                    return None
                return next(
                    (s for s in self.servers_list if (s.name or "") == self.selected_server_name),
                    None,
                )

            async def on_timeout(self) -> None:  # pragma: no cover - best effort
                try:
                    for child in self.children:
                        if isinstance(child, (discord.ui.Select, discord.ui.Button)):
                            child.disabled = True
                except Exception:
                    pass

        class ServerSelect(discord.ui.Select):
            def __init__(self, servers_list: list) -> None:
                super().__init__(
                    placeholder="Select a running server",
                    min_values=1,
                    max_values=1,
                    options=_server_select_options(servers_list),
                )

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                if self.view and isinstance(self.view, WhitelistView):
                    self.view.selected_server_name = self.values[0]
                await i.response.defer()

        class PlayerModal(discord.ui.Modal, title="Whitelist Player"):
            def __init__(self, parent: 'WhitelistView') -> None:
                super().__init__()
                self.parent = parent
                self.player_input = discord.ui.TextInput(
                    label="Player name",
                    placeholder="e.g. Notch",
                    min_length=1,
                    max_length=32,
                    required=True,
                )
                self.add_item(self.player_input)

            async def on_submit(self, i: discord.Interaction) -> None:  # type: ignore[override]
                self.parent.player_name = str(self.player_input.value).strip()
                await i.response.edit_message(
                    content=f"Player set to: {self.parent.player_name}",
                    view=self.parent,
                )

        class SetPlayerButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Set Player", style=discord.ButtonStyle.secondary)

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                if not self.view or not isinstance(self.view, WhitelistView):
                    await i.response.edit_message(content="Internal error.", view=None)
                    return
                await i.response.send_modal(PlayerModal(self.view))

        class ExecuteButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Execute", style=discord.ButtonStyle.success)

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                if not self.view or not isinstance(self.view, WhitelistView):
                    await i.response.edit_message(content="Internal error.", view=None)
                    return
                view: WhitelistView = self.view
                srv = view.resolve_selected_server()
                if not srv:
                    await i.response.edit_message(content="Please select a server first.", view=view)
                    return
                if not view.player_name:
                    await i.response.edit_message(content="Please set a player name first.", view=view)
                    return
                cmd = f"whitelist add {view.player_name}"
                rc = srv.send_command(cmd)  # type: ignore[attr-defined]
                if rc == 0:
                    await i.response.edit_message(
                        content=f"Sent to {srv.name}: {cmd}",
                        view=None,
                    )
                else:
                    await i.response.edit_message(
                        content=f"Failed to send command to {srv.name}. Is it started by this bot?",
                        view=None,
                    )

        class CancelButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                await i.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            "Select a running server, then set a player and Execute.",
            view=WhitelistView(choices),
            ephemeral=True,
        )

    # /clean items
    clean = app_commands.Group(name="clean", description="Cleanup utilities")
    # Apply guild scoping to the group (subcommands cannot set default guilds)
    clean = guild_decorator(clean)

    @clean.command(name="items", description="Remove all dropped items on a server")
    async def clean_items(interaction: discord.Interaction) -> None:
        choices = running_servers()
        if not choices:
            await interaction.response.send_message("No running servers available.", ephemeral=True)
            return

        class CleanView(discord.ui.View):
            def __init__(self, servers_list: list) -> None:
                super().__init__(timeout=60)
                self.servers_list = servers_list
                self.selected_server_name: Optional[str] = None
                self.add_item(ServerSelect(servers_list))
                self.add_item(ExecuteButton())
                self.add_item(CancelButton())

            def resolve_selected_server(self):
                if not self.selected_server_name:
                    return None
                return next(
                    (s for s in self.servers_list if (s.name or "") == self.selected_server_name),
                    None,
                )

            async def on_timeout(self) -> None:  # pragma: no cover - best effort
                try:
                    for child in self.children:
                        if isinstance(child, (discord.ui.Select, discord.ui.Button)):
                            child.disabled = True
                except Exception:
                    pass

        class ServerSelect(discord.ui.Select):
            def __init__(self, servers_list: list) -> None:
                super().__init__(
                    placeholder="Select a running server",
                    min_values=1,
                    max_values=1,
                    options=_server_select_options(servers_list),
                )

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                if self.view and isinstance(self.view, CleanView):
                    self.view.selected_server_name = self.values[0]
                await i.response.defer()

        class ExecuteButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Clean Items", style=discord.ButtonStyle.success)

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                if not self.view or not isinstance(self.view, CleanView):
                    await i.response.edit_message(content="Internal error.", view=None)
                    return
                view: CleanView = self.view
                srv = view.resolve_selected_server()
                if not srv:
                    await i.response.edit_message(content="Please select a server first.", view=view)
                    return
                cmd = "kill @e[type=item]"
                rc = srv.send_command(cmd)  # type: ignore[attr-defined]
                if rc == 0:
                    await i.response.edit_message(
                        content=f"Sent to {srv.name}: {cmd}",
                        view=None,
                    )
                else:
                    await i.response.edit_message(
                        content=f"Failed to send command to {srv.name}. Is it started by this bot?",
                        view=None,
                    )

        class CancelButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                await i.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            "Select a running server, then Clean Items.",
            view=CleanView(choices),
            ephemeral=True,
        )

    # Register the clean group on the tree
    bot.tree.add_command(clean)

    # /clean mob
    @clean.command(name="mob", description="Kill mobs of a given type and clear their drops")
    async def clean_mob(interaction: discord.Interaction) -> None:
        choices = running_servers()
        if not choices:
            await interaction.response.send_message("No running servers available.", ephemeral=True)
            return

        class CleanMobView(discord.ui.View):
            def __init__(self, servers_list: list) -> None:
                super().__init__(timeout=120)
                self.servers_list = servers_list
                self.selected_server_name: Optional[str] = None
                self.mob_id: Optional[str] = None
                self.add_item(ServerSelect(servers_list))
                self.add_item(SetMobButton())
                self.add_item(ExecuteButton())
                self.add_item(CancelButton())

            def resolve_selected_server(self):
                if not self.selected_server_name:
                    return None
                return next(
                    (s for s in self.servers_list if (s.name or "") == self.selected_server_name),
                    None,
                )

            async def on_timeout(self) -> None:  # pragma: no cover - best effort
                try:
                    for child in self.children:
                        if isinstance(child, (discord.ui.Select, discord.ui.Button)):
                            child.disabled = True
                except Exception:
                    pass

        class ServerSelect(discord.ui.Select):
            def __init__(self, servers_list: list) -> None:
                super().__init__(
                    placeholder="Select a running server",
                    min_values=1,
                    max_values=1,
                    options=_server_select_options(servers_list),
                )

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                if self.view and isinstance(self.view, CleanMobView):
                    self.view.selected_server_name = self.values[0]
                await i.response.defer()

        class MobModal(discord.ui.Modal, title="Mob Type"):
            def __init__(self, parent: 'CleanMobView') -> None:
                super().__init__()
                self.parent = parent
                self.mob_input = discord.ui.TextInput(
                    label="Mob ID (e.g., minecraft:enderman)",
                    placeholder="minecraft:zombie",
                    min_length=1,
                    max_length=64,
                    required=True,
                )
                self.add_item(self.mob_input)

            async def on_submit(self, i: discord.Interaction) -> None:  # type: ignore[override]
                typed = str(self.mob_input.value).strip()
                self.parent.mob_id = typed
                await i.response.edit_message(
                    content=f"Mob type set to: {typed}",
                    view=self.parent,
                )

        class SetMobButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Set Mob", style=discord.ButtonStyle.secondary)

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                if not self.view or not isinstance(self.view, CleanMobView):
                    await i.response.edit_message(content="Internal error.", view=None)
                    return
                await i.response.send_modal(MobModal(self.view))

        class ExecuteButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Execute", style=discord.ButtonStyle.success)

            @staticmethod
            def _is_blocked_type(mob_type: str) -> bool:
                t = mob_type.strip().lower()
                return t == "player" or t == "minecraft:player"

            @staticmethod
            def _is_valid_type(mob_type: str) -> bool:
                if not mob_type or any(ch.isspace() for ch in mob_type):
                    return False
                if "@" in mob_type:
                    return False
                # allow namespace:id or id
                allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_:-")
                return all(ch in allowed for ch in mob_type.lower())

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                if not self.view or not isinstance(self.view, CleanMobView):
                    await i.response.edit_message(content="Internal error.", view=None)
                    return
                view: CleanMobView = self.view
                srv = view.resolve_selected_server()
                if not srv:
                    await i.response.edit_message(content="Please select a server first.", view=view)
                    return
                mob_type = (view.mob_id or "").strip()
                if not self._is_valid_type(mob_type):
                    await i.response.edit_message(content="Invalid mob type.", view=view)
                    return
                if self._is_blocked_type(mob_type):
                    await i.response.edit_message(content="Refused: type=player is not allowed.", view=view)
                    return

                cmd1 = f"kill @e[type={mob_type}]"
                rc1 = srv.send_command(cmd1)  # type: ignore[attr-defined]
                # Regardless of rc1, also clear item drops
                cmd2 = "kill @e[type=item]"
                rc2 = srv.send_command(cmd2)  # type: ignore[attr-defined]

                if rc1 == 0 and rc2 == 0:
                    await i.response.edit_message(
                        content=f"Sent to {srv.name}: {cmd1} and {cmd2}",
                        view=None,
                    )
                elif rc1 == 0:
                    await i.response.edit_message(
                        content=f"Sent to {srv.name}: {cmd1}. Failed to send: {cmd2}",
                        view=None,
                    )
                elif rc2 == 0:
                    await i.response.edit_message(
                        content=f"Failed to send: {cmd1}. Sent cleanup: {cmd2}",
                        view=None,
                    )
                else:
                    await i.response.edit_message(
                        content=f"Failed to send commands to {srv.name}. Is it started by this bot?",
                        view=None,
                    )

        class CancelButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                await i.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            "Select a running server, set a mob type, then Execute.",
            view=CleanMobView(choices),
            ephemeral=True,
        )

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
            def __init__(self, servers_list: list, available_gb: int) -> None:
                super().__init__(timeout=120)
                self.servers_list = servers_list
                self.selected_server_name: Optional[str] = None
                self.selected_xms: int = 2
                self.selected_xmx: int = 4
                self.available_gb: int = int(available_gb)

                self.add_item(ServerSelect(servers_list))
                self.add_item(XmsSelect())
                self.xmx_select = XmxSelect(self.available_gb)
                self.add_item(self.xmx_select)
                try:
                    # Align selected_xmx with the default shown in the select
                    default_opt = next((opt for opt in self.xmx_select.options if getattr(opt, 'default', False)), None)
                    if default_opt is not None:
                        self.selected_xmx = int(str(default_opt.value))
                    else:
                        # Fallback to the last option
                        self.selected_xmx = int(str(self.xmx_select.options[-1].value))
                except Exception:
                    pass
                self.add_item(StartButton())
                self.add_item(CancelButton())

                # If no memory is available, disable Xmx and Start buttons
                if self.available_gb <= 0:
                    try:
                        self.xmx_select.disabled = True
                        # Disable Start button
                        for child in self.children:
                            if isinstance(child, discord.ui.Button) and getattr(child, 'label', '') == 'Start':
                                child.disabled = True
                    except Exception:
                        pass

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
                xms_values = [1, 2, 4, 8, 16, 20]
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
            def __init__(self, available_gb: int) -> None:
                default_xmx = 4
                base_values = [1, 2, 4, 8, 16, 20]
                # Filter by available memory, keep at least one option to satisfy Discord
                filtered = [gb for gb in base_values if gb <= max(available_gb, 1)]
                if not filtered:
                    filtered = [1]
                default_choice = min(default_xmx, filtered[-1])
                options = [
                    discord.SelectOption(
                        label=f"Xmx {gb}G",
                        value=str(gb),
                        default=(gb == default_choice),
                    )
                    for gb in filtered
                ]
                super().__init__(
                    placeholder="Max heap Xmx (filtered by available)",
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
                # Re-check available memory at click time to avoid races
                current_avail = get_available_memory_gb(servers)
                if xmx > current_avail:
                    await i.response.edit_message(
                        content=f"Not enough memory. Available: {current_avail}G. Pick a smaller Xmx.",
                        view=view,
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
                # TODO: remove unecessary logging Report detected MC version, Java selection, and log file path
                java_exe, mc_ver, java_major = resolve_java_for_server(srv.path)
                log_info = f" log: {srv.log_path}" if getattr(srv, "log_path", None) else ""
                ver_info = f" MC={mc_ver or '?'} Java={java_major or '?'}"
                await i.response.edit_message(
                    content=(
                        f"Starting - {srv.name} (PID {pid}) with Xmx={xmx}G Xms={xms}G |"
                        f"{ver_info}{log_info}"
                    ),
                    view=None,
                )

        class CancelButton(discord.ui.Button):
            def __init__(self) -> None:
                super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

            async def callback(self, i: discord.Interaction) -> None:  # type: ignore[override]
                await i.response.edit_message(content="Cancelled.", view=None)

        available_gb = get_available_memory_gb(servers)
        tail = " Showing first 25 servers." if too_many else ""
        await interaction.response.send_message(
            f"Pick a server and memory, then Start:\nAvailable memory: {available_gb}G" + tail,
            view=StartView(server_choices, available_gb),
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
