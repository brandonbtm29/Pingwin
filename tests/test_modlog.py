"""The mod log stays quiet: no typo fixes, no Pingwin tidying up, no mod channels."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from pingwin.cogs import modlog
from pingwin.cogs.modlog import minor_edit

T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def test_minor_edits_are_skipped():
    long_ago = T0 - timedelta(hours=2)
    assert minor_edit("same", "same", long_ago, T0)
    assert minor_edit("teh cat", "the cat totally different", T0 - timedelta(minutes=3), T0)  # fresh post
    assert minor_edit("I made grilled cheese for dinner", "I made grilled cheese for dinner tonight", long_ago, T0)
    assert not minor_edit("Meet at 8", "Actually never mind, I'm not coming and here's why at length", long_ago, T0)


class Perms:
    def __init__(self, view):
        self.view_channel = view


def channel(cid, name, public=True):
    return NS(id=cid, name=name, mention=f"#{name}", permissions_for=lambda role: Perms(public))


def make(cfg):
    sent = []
    log_ch = NS(id=1, name="mod-log")

    async def send(embed=None, **kw):
        sent.append(embed)

    log_ch.send = send
    guild = NS(default_role=NS(name="@everyone"))
    bot = NS(
        features=NS(modlog=cfg),
        is_active=True,
        home_guild=guild,
        text_channel=lambda c: log_ch,
        intents=NS(message_content=True),
        role_named=lambda g, n: None,
        config=NS(roles=NS(required_role="")),
    )
    cog = modlog.ModLog(bot)
    return cog, guild, sent


def msg(guild, ch, text="hello there", bot=False, age=timedelta(hours=1)):
    return NS(
        guild=guild,
        channel=ch,
        content=text,
        attachments=[],
        author=NS(bot=bot, mention="@someone"),
        created_at=T0 - age,
        jump_url="https://discord.com/x",
    )


def test_what_gets_logged():
    cfg = NS(enabled=True, channel=1, messages=True, edits=False, skip_channels="music")

    async def go():
        cog, guild, sent = make(cfg)
        await cog.on_message_delete(msg(guild, channel(2, "casual-chat")))
        assert len(sent) == 1
        await cog.on_message_delete(msg(guild, channel(3, "🎵-music")))  # skipped by name
        await cog.on_message_delete(msg(guild, channel(4, "approvals", public=False)))  # a mod channel
        await cog.on_message_delete(msg(guild, channel(2, "casual-chat"), bot=True))  # Pingwin or another bot
        assert len(sent) == 1
        before, after = msg(guild, channel(2, "casual-chat"), "a"), msg(guild, channel(2, "casual-chat"), "b" * 80)
        await cog.on_message_edit(before, after)  # edits are off by default
        assert len(sent) == 1
        cfg.edits = True
        await cog.on_message_edit(before, after)
        assert len(sent) == 2

    asyncio.run(go())


def test_raw_deletes_are_not_logged():
    assert not hasattr(modlog.ModLog, "on_raw_message_delete")
