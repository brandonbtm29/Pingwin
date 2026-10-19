"""Pingwin: a friendly Discord bot for friends' servers."""

__version__ = "0.6.0"
REPO_URL = "https://github.com/brandonbtm29/Pingwin"
# Manage Roles, Change Nickname, View Channels, Send Messages, Embed Links, Read Message History
INVITE_PERMISSIONS = 335629312


def invite_url(application_id: int) -> str:
    return (
        f"https://discord.com/oauth2/authorize?client_id={application_id}"
        f"&scope=bot+applications.commands&permissions={INVITE_PERMISSIONS}"
    )
