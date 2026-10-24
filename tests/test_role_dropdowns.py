"""Big role menus show as a dropdown; picks from it set exactly those roles (and the time zone)."""

import asyncio
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import discord

from pingwin import zones
from pingwin.cogs import roles
from pingwin.config import MenuConfig, MenuRole


def tz_menu(n=None):
    rows = tuple(MenuRole(name, emoji, zone=z) for name, emoji, z in zones.MENU_PRESET[:n])
    return MenuConfig("timezone", "What's your time zone?", "", "one", rows)


def test_presets_are_real_zones():
    assert len(zones.MENU_PRESET) >= 18
    for _name, _emoji, z in zones.MENU_PRESET:
        ZoneInfo(z)
    assert zones.zone("ist").key == "Asia/Kolkata" and zones.zone("aest").key == "Australia/Sydney"


def test_big_menus_become_a_dropdown():
    async def go():
        big, small = tz_menu(), tz_menu(4)
        resolved = [(m, NS(id=100 + i)) for i, m in enumerate(big.roles)]
        view = roles.menu_view(resolved, big)
        (dyn,) = view.children
        item = dyn.item
        assert isinstance(item, discord.ui.Select) and len(item.options) == len(big.roles)
        assert item.custom_id == "pingwin:rsel:timezone" and item.max_values == 1
        small_view = roles.menu_view(resolved[:4], small)
        assert all(isinstance(c.item, discord.ui.Button) for c in small_view.children)
        assert "list below" in roles.build_menu_embed(big, "x").description

    asyncio.run(go())


class FakeMember:
    def __init__(self, guild, have):
        self.guild, self.roles, self.id, self.display_name = guild, list(have), 7, "Sam"

    async def add_roles(self, *rs, reason=None):
        self.roles += list(rs)

    async def remove_roles(self, *rs, reason=None):
        self.roles = [r for r in self.roles if r not in rs]


def test_dropdown_pick_swaps_zone_roles(monkeypatch):
    menu = tz_menu()
    rs = [NS(id=100 + i, name=m.name) for i, m in enumerate(menu.roles)]
    guild = NS(roles=rs)
    member = FakeMember(guild, [rs[0]])  # had Eastern
    saved = {}
    monkeypatch.setattr(zones, "set_for", lambda uid, z: saved.__setitem__(uid, z))
    persona = NS(say=lambda key, **kw: f"{key}:{kw.get('role')}")
    uk = next(r for r in rs if r.name == "UK Time")
    msg = asyncio.run(roles.apply_picks(member, menu, {uk.id}, persona))
    assert member.roles == [uk] and saved[7] == "Europe/London" and "UK Time" in msg
