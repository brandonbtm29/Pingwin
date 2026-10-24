"""Pingwin: a friendly Discord bot for friends' servers."""

__version__ = "0.21.2"
REPO_URL = "https://github.com/brandonbtm29/Pingwin"
# Manage Roles, Change Nickname, View Channels, Send Messages, Send Messages in Threads, Embed Links,
# Read Message History, Mention All Roles (to ping roles), Create Polls, Pin Messages
INVITE_PERMISSIONS = (
    (1 << 28)
    | (1 << 26)
    | (1 << 10)
    | (1 << 11)
    | (1 << 38)
    | (1 << 14)
    | (1 << 16)
    | (1 << 17)
    | (1 << 49)
    | (1 << 51)
)


def invite_url(application_id: int) -> str:
    return (
        f"https://discord.com/oauth2/authorize?client_id={application_id}"
        f"&scope=bot+applications.commands&permissions={INVITE_PERMISSIONS}"
    )
