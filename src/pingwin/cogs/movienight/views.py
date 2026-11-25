"""The movie night buttons, menus and forms. They reach the cog with get_cog("MovieNight")."""

from __future__ import annotations

import re
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import discord

from ... import franchise, movies, rankedvote, store, voicewatch

if TYPE_CHECKING:
    pass
from .common import POLL_MAX, STATE, _movie_label, _same_title, is_franchise, series_label

if TYPE_CHECKING:
    from .cog import MovieNight


# --------------------------------------------------------------------- buttons
class Decision(str):
    """What a movie mod's decision did, as the text for its card. `ok` is False when nothing was decided (it
    was handled already, or a movie still needs picking): the card then stays as it is."""

    ok: bool = True

    def __new__(cls, text: str, ok: bool = True) -> Decision:
        obj = super().__new__(cls, text)
        obj.ok = ok
        return obj


async def show_decision(interaction: discord.Interaction, msg: str, **edit: Any) -> None:
    """Put a decision on its card, or, when nothing was decided, tell only the mod who clicked."""
    try:
        if not getattr(msg, "ok", True):
            await interaction.followup.send(msg, ephemeral=True)
        elif interaction.message is not None:
            await interaction.message.edit(content=msg, view=None, **edit)
        else:
            await interaction.edit_original_response(content=msg, view=None, **edit)
    except discord.HTTPException:
        pass


class RequestButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:mvq:(?P<rid>[0-9a-f]+):(?P<act>ok|no)"
):
    def __init__(self, rid: str, act: str, label: str | None = None) -> None:
        ok = act == "ok"
        super().__init__(
            discord.ui.Button(
                label=label or ("Approve" if ok else "Deny"),
                style=discord.ButtonStyle.success if ok else discord.ButtonStyle.danger,
                custom_id=f"pingwin:mvq:{rid}:{act}",
            )
        )
        self.rid, self.act = rid, act

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["rid"], match["act"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        if cog is None:
            return
        if not cog.is_movie_mod(interaction.user):
            await interaction.response.send_message("Only movie mods can approve these.", ephemeral=True)
            return
        await interaction.response.defer()
        await show_decision(interaction, await cog.decide(self.rid, self.act == "ok", interaction.user))


class BreakButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:mvb:(?P<key>[a-z0-9-]{1,40}):(?P<weeks>\d{1,2})"
):
    """On the "franchise finished" card: how many weeks off before the next franchise (0 = carry on)."""

    def __init__(self, key: str, weeks: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="No break" if weeks == 0 else f"{weeks} weeks off",
                style=discord.ButtonStyle.secondary if weeks == 0 else discord.ButtonStyle.primary,
                custom_id=f"pingwin:mvb:{key}:{weeks}",
            )
        )
        self.key, self.weeks = key, weeks

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["key"], int(match["weeks"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        if cog is None:
            return
        if not cog.is_movie_mod(interaction.user):
            await interaction.response.send_message("Only movie mods can pick this.", ephemeral=True)
            return
        s = cog.series(self.key)
        if s is None:
            await interaction.response.send_message("That series isn't set up anymore.", ephemeral=True)
            return
        who = getattr(interaction.user, "display_name", interaction.user.name)
        if self.weeks:
            msg = cog.add_break(s, self.weeks, None, "between franchises", who)
        else:
            nxt = cog.next_showing(s)
            when = f" on <t:{int(nxt.timestamp())}:F>" if nxt else ""
            msg = f"No break: {series_label(s)} carries on{when}. ({who})"
        await interaction.response.edit_message(content=msg, embed=None, view=None)


class NightPick(discord.ui.DynamicItem[discord.ui.Select], template=r"pingwin:mvd:(?P<rid>[0-9a-f]+)"):
    """On a "movie night noticed in voice" card: movie mods pick which movie it was."""

    def __init__(self, rid: str, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=f"pingwin:mvd:{rid}",
                placeholder="Which movie did we watch?",
                options=options or [discord.SelectOption(label="Something else…", value="other")],
            )
        )
        self.rid = rid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Select, match):
        return cls(match["rid"], list(getattr(item, "options", []) or []))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        if cog is None:
            return
        if not cog.is_movie_mod(interaction.user):
            await interaction.response.send_message("Only movie mods can pick this.", ephemeral=True)
            return
        choice = (interaction.data.get("values") or ["other"])[0]
        if choice == "other":
            await interaction.response.send_modal(OtherNightModal(self.rid))
            return
        # The menu's entry, so a list that changed since this card was drawn can't log a different movie.
        label = next((o.label for o in getattr(self.item, "options", []) if o.value == choice), None)
        await interaction.response.defer()
        msg = await cog.decide(self.rid, True, interaction.user, choice=int(choice), choice_label=label)
        await show_decision(interaction, msg, embed=None)


class StarterPick(discord.ui.DynamicItem[discord.ui.UserSelect], template=r"pingwin:mvf:(?P<rid>[0-9a-f]+)"):
    """On a "did movie night happen?" card: movie mods can pick who rates first (optional)."""

    def __init__(self, rid: str) -> None:
        super().__init__(
            discord.ui.UserSelect(
                custom_id=f"pingwin:mvf:{rid}", placeholder="Who rates first? (optional)", min_values=0, max_values=1
            )
        )
        self.rid = rid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.UserSelect, match):
        return cls(match["rid"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        if cog is None:
            return
        if not cog.is_movie_mod(interaction.user):
            await interaction.response.send_message("Only movie mods can pick this.", ephemeral=True)
            return
        # The picked member comes with the interaction (the member list may not be kept without the intent).
        picked = list(getattr(self.item, "values", []) or [])
        member = picked[0] if picked else None
        problem, card = cog.set_night_starter(self.rid, member, picked=bool(picked))
        if problem or card is None:
            await interaction.response.send_message(problem or "This one was already handled.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=card[0], view=card[1])


class StarterButton(discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:rst:(?P<hid>[0-9a-f]+)"):
    """On a Rate it post nobody has rated yet: pick who goes first (optional)."""

    def __init__(self, hid: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Pick who starts", emoji="🎬", style=discord.ButtonStyle.secondary, custom_id=f"pingwin:rst:{hid}"
            )
        )
        self.hid = hid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["hid"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        if cog is None:
            return
        problem = cog.starter_problem(self.hid)
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        view = ExpiringView(interaction, "That menu expired. Tap Pick who starts again.", timeout=300)
        view.add_item(StarterMenu(self.hid))
        await interaction.response.send_message("Who should rate first? I'll ping them.", view=view, ephemeral=True)


class ExpiringView(discord.ui.View):
    """A private menu that says so when it times out, instead of failing when clicked later."""

    def __init__(self, interaction: discord.Interaction, expired: str, timeout: float) -> None:
        super().__init__(timeout=timeout)
        self.interaction, self.expired = interaction, expired

    async def on_timeout(self) -> None:
        try:
            await self.interaction.edit_original_response(content=self.expired, view=None)
        except discord.HTTPException:
            pass


class StarterMenu(discord.ui.UserSelect):
    """The member picker the "Pick who starts" button shows (only to whoever clicked it)."""

    def __init__(self, hid: str) -> None:
        super().__init__(placeholder="Pick someone", min_values=1, max_values=1)
        self.hid = hid

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        if cog is None or not self.values:
            return
        if self.view is not None:
            self.view.stop()  # done: it won't say it expired
        await interaction.response.defer()
        msg = await cog.pick_starter(self.hid, self.values[0], interaction.user)
        try:
            await interaction.edit_original_response(content=msg, view=None)
        except discord.HTTPException:
            pass


ORDINALS = ("1st", "2nd", "3rd", "4th", "5th")


class RankedVoteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:rcv:(?P<pid>[0-9a-f]+)"):
    """The Vote button on a ranked-choice poll."""

    def __init__(self, pid: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Vote", emoji="🗳️", style=discord.ButtonStyle.primary, custom_id=f"pingwin:rcv:{pid}"
            )
        )
        self.pid = pid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["pid"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        found = cog.find_poll(self.pid) if cog else None
        if (
            found is None
            or found[1]["status"] != "open"
            or discord.utils.utcnow() >= discord.utils.parse_time(found[1]["ends"])
        ):
            await interaction.response.send_message("This poll has closed.", ephemeral=True)
            return
        s, p = found
        await interaction.response.send_modal(RankedModal(p, str(interaction.user.id), is_franchise(s)))


class PollVoteButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:pv:(?P<pid>[0-9a-f]+):(?P<i>[0-9]{1,2})"
):
    """A movie's button on a one-vote or approval poll: tap it to vote, tap it again to take the vote back."""

    def __init__(self, pid: str, i: int, label: str = "Vote") -> None:
        super().__init__(
            discord.ui.Button(
                label=(label or "Vote")[:80], style=discord.ButtonStyle.secondary, custom_id=f"pingwin:pv:{pid}:{i}"
            )
        )
        self.pid, self.i = pid, i

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["pid"], int(match["i"]), item.label or "Vote")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        if cog is None:
            return
        text = cog.tap_vote(self.pid, str(interaction.user.id), self.i)
        await interaction.response.send_message(text, ephemeral=True)
        cog.refresh_poll_soon(self.pid)


def poll_view(p: dict[str, Any]) -> discord.ui.View:
    """The buttons under one of Pingwin's polls: Vote on a ranked poll (it opens the ranking form), else a button
    for each movie."""
    view = discord.ui.View(timeout=None)
    if p.get("kind") == "ranked":
        view.add_item(RankedVoteButton(p["pid"]))
        return view
    labels = p.get("labels", {})
    for i, c in enumerate(p["options"][:25]):
        view.add_item(PollVoteButton(p["pid"], i, labels.get(c, "?")))
    return view


class RankedModal(discord.ui.Modal):
    """Rank a ranked-choice poll's options: 1st choice, 2nd choice, and so on (only the first is needed)."""

    def __init__(self, p: dict[str, Any], uid: str, franchises: bool = False) -> None:
        super().__init__(title="Rank your picks")
        self.pid, self.uid = p["pid"], uid
        mine = p.get("ballots", {}).get(uid, [])
        labels = p.get("labels", {})
        ranks = max(1, min(int(p.get("ranks") or 3), rankedvote.MAX_RANKS, len(p["options"])))
        thing = "franchise" if franchises else "movie"
        self.picks: list[discord.ui.Select] = []
        for i in range(ranks):
            current = mine[i] if i < len(mine) else None
            pick = discord.ui.Select(
                placeholder=f"Pick a {thing}" if i == 0 else "Optional",
                min_values=1 if i == 0 else 0,
                max_values=1,
                required=i == 0,
                options=[
                    discord.SelectOption(label=labels.get(c, "?")[:100], value=c, default=c == current)
                    for c in p["options"][:25]
                ],
            )
            self.picks.append(pick)
            self.add_item(
                discord.ui.Label(
                    text=f"{ORDINALS[i]} choice", description="Your favorite" if i == 0 else None, component=pick
                )
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        chosen = [p.values[0] for p in self.picks if p.values]
        if cog is None:
            return
        problem, p = cog.save_ballot(self.pid, self.uid, chosen)
        if problem or p is None:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        ballot = p["ballots"][self.uid]
        labels = p.get("labels", {})
        text = "🗳️ Got it! Your ranking: " + " · ".join(f"{i}. {labels.get(c, '?')}" for i, c in enumerate(ballot, 1))
        if len(ballot) < len(chosen):
            text += "\n-# You picked the same one twice, so it counts once, in its highest spot."
        text += "\n-# Vote again any time before the poll closes to change it."
        await interaction.response.send_message(text, ephemeral=True)
        cog.refresh_poll_soon(self.pid)


class OtherNightModal(discord.ui.Modal, title="Which movie was it?"):
    movie = discord.ui.TextInput(label="Title", max_length=100, placeholder="Like The Thing (1982)")
    year = discord.ui.TextInput(label="Year (optional)", max_length=4, required=False)

    def __init__(self, rid: str) -> None:
        super().__init__()
        self.rid = rid

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        if cog is None:
            return
        title, year = voicewatch.split_year(str(self.movie.value))
        y = str(self.year.value or "").strip()
        custom = {"title": title, "year": int(y) if y.isdigit() else year}
        await interaction.response.defer()
        await show_decision(interaction, await cog.decide(self.rid, True, interaction.user, custom=custom), embed=None)


class RateButton(discord.ui.DynamicItem[discord.ui.Button], template=r"pingwin:rate:(?P<hid>[0-9a-f]+)"):
    def __init__(self, hid: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="Rate it", emoji="⭐", style=discord.ButtonStyle.primary, custom_id=f"pingwin:rate:{hid}"
            )
        )
        self.hid = hid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["hid"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return getattr(interaction.client, "is_active", True)

    async def callback(self, interaction: discord.Interaction) -> None:
        await open_rating(interaction, self.hid)


async def open_rating(interaction: discord.Interaction, hid: str) -> None:
    lib = movies.load()
    entry = next((e for e in lib["history"] if e["id"] == hid), None)
    if entry is None:
        await interaction.response.send_message("I can't find that movie night anymore.", ephemeral=True)
        return
    if entry.get("prompt", {}).get("closed"):
        await interaction.response.send_message("Ratings for this one have closed.", ephemeral=True)
        return
    movie = lib["movies"].get(entry["movie_id"], {"title": "the movie"})
    await interaction.response.send_modal(
        RateModal(hid, movie, entry, str(interaction.user.id), pick_person=interaction.guild is not None)
    )


PICKED = re.compile(r"(?P<id>(?:tt\d{6,10})?)\|(?P<title>[^|]+)\|(?P<year>\d{4})?")


COMMENT_LIMIT, ANSWER_LIMIT, ASK_LIMIT = 1500, 500, 300  # as on the movie pages


class RateModal(discord.ui.Modal):
    def __init__(
        self,
        hid: str,
        movie: dict[str, Any],
        entry: dict[str, Any],
        uid: str,
        draft: dict[str, Any] | None = None,
        draft_id: str | None = None,
        pick_person: bool = True,
    ):
        """The Rate it form. `draft` fills in a voice message's score and words (`draft_id` is its card, which
        this form then replaces); `pick_person` is off in DMs, where Discord can't list the server's members."""
        super().__init__(title=f"Rate {movie['title']}"[:45])
        self.hid, self.uid, self.draft_id = hid, uid, draft_id
        mine = {**entry.get("ratings", {}).get(uid, {}), **(draft or {})}  # a voice message's score and words
        self.question, self.asker = movies.question_for(entry, uid)

        def fill(key: str, limit: int) -> str | None:
            # Discord refuses a form whose saved text is longer than its box (reviews from the movie pages
            # and imports can be): the same limits as the movie pages, and anything longer is cut.
            return (str(mine.get(key) or ""))[:limit] or None

        score = mine.get("score")
        self.score = discord.ui.TextInput(
            placeholder="Like 7 or 7.5",
            max_length=5,
            default=None if not isinstance(score, (int, float)) else f"{round(score, 2):g}",
        )
        self.comment = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=COMMENT_LIMIT,
            default=fill("comment", COMMENT_LIMIT),
        )
        self.add_item(discord.ui.Label(text="Your score out of 10", component=self.score))
        self.add_item(discord.ui.Label(text="What did you think?", component=self.comment))
        self.answer = None
        if self.question:
            asker = (entry.get("ratings", {}).get(self.asker or "") or {}).get("name") or "Someone"
            q = self.question if len(self.question) <= 100 else self.question[:99] + "…"
            self.answer = discord.ui.TextInput(
                required=False, max_length=ANSWER_LIMIT, placeholder="Your answer", default=fill("answer", ANSWER_LIMIT)
            )
            self.add_item(discord.ui.Label(text=f"{asker} asked you"[:45], description=q, component=self.answer))
        self.ask = discord.ui.TextInput(
            placeholder="How many ___ out of ___?",
            required=False,
            max_length=ASK_LIMIT,
            default=fill("ask", ASK_LIMIT),
        )
        self.add_item(discord.ui.Label(text="Ask someone a question", component=self.ask))
        self.who = None
        if not pick_person:
            return
        self.who = discord.ui.UserSelect(
            placeholder="Whoever rates next",
            min_values=0,
            max_values=1,
            required=False,
            default_values=[discord.Object(int(mine["ask_for"]))] if str(mine.get("ask_for") or "").isdigit() else [],
        )
        self.add_item(
            discord.ui.Label(
                text="Who should answer it?",
                description="I'll ping them. Leave it empty for whoever rates next.",
                component=self.who,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        score = movies.parse_score(str(self.score.value))
        if score is None:
            await interaction.response.send_message("The score needs to be a number from 0 to 10.", ephemeral=True)
            return
        cog: MovieNight | None = interaction.client.get_cog("MovieNight")  # type: ignore[union-attr]
        lib = movies.load()
        entry = next((e for e in lib["history"] if e["id"] == self.hid), None)
        if entry is None or entry.get("prompt", {}).get("closed"):
            await interaction.response.send_message("Ratings for this one have closed.", ephemeral=True)
            return
        user = interaction.user
        ask = str(self.ask.value or "").strip()
        picked = self.who.values[0] if self.who is not None and self.who.values else None
        if self.who is None:  # no picker (a DM): whoever they asked before stays
            mine = entry.get("ratings", {}).get(self.uid) or {}
            picked = NamedMember(mine["ask_for"], mine.get("ask_for_name", "")) if mine.get("ask_for") else None
        ask_for, note = pick_asked(entry, self.uid, ask, picked)
        answer = str(self.answer.value or "").strip() if self.answer else ""
        old = dict(entry.get("ratings", {}).get(self.uid) or {})
        movies.add_rating(
            entry,
            self.uid,
            name=getattr(user, "display_name", user.name),
            user_id=self.uid,
            score=score,
            comment=str(self.comment.value or ""),
            answered=self.question if answer else "",
            answered_from=self.asker,
            answer=answer,
            ask=ask,
            ask_for=str(ask_for.id) if ask_for else None,
            ask_for_name=getattr(ask_for, "display_name", "") if ask_for else "",
        )
        if old:
            entry["ratings"][self.uid]["at"] = old.get("at", time.time())  # keep its place in the question game
        movies.save(lib)
        thanks = interaction.client.persona.say("movie_rated", score=f"{score:g}") + note
        if self.draft_id:  # from a voice message's card: the card becomes the thank-you
            voice = interaction.client.get_cog("VoiceRatings")  # type: ignore[union-attr]
            if voice is not None:
                voice.forget(self.draft_id)
            await interaction.response.edit_message(content=thanks, embed=None, view=None)
        else:
            await interaction.response.send_message(thanks, ephemeral=True)
        if cog:
            await cog.after_rating(self.hid, self.uid, old)


class NamedMember(SimpleNamespace):
    """Who a rater asked before, when the form had no picker to ask again."""

    def __init__(self, uid: str, name: str) -> None:
        super().__init__(id=int(uid), display_name=name or "them", bot=False)


def pick_asked(entry: dict[str, Any], uid: str, ask: str, who) -> tuple[Any, str]:
    """The member a rater's question goes to (None: whoever rates next), and a note when it can't go to the
    one they picked."""
    if who is None or not ask:
        return None, ""
    problem = "bots don't rate movies" if getattr(who, "bot", False) else movies.ask_problem(entry, uid, str(who.id))
    if problem:
        name = getattr(who, "display_name", "them")
        return None, f"\n-# Your question didn't go to {name}: {problem}. It's waiting for whoever rates next."
    return who, ""


class FranchiseModal(discord.ui.Modal, title="Franchise queue"):
    def __init__(self, cog: MovieNight, s) -> None:
        super().__init__()
        self.cog, self.s = cog, s
        q = cog._data(store.load(STATE), s).get("queue") or {}
        self.name = discord.ui.TextInput(label="Franchise", max_length=100, default=(q.get("name") or "")[:100] or None)
        lines = [
            ("✅ " if i.get("done") else "") + franchise.label(i) for i in q.get("items", []) if not i.get("skipped")
        ]
        self.items = discord.ui.TextInput(
            label="Movies in order, one per line (✅ = watched)",
            style=discord.TextStyle.paragraph,
            max_length=4000,
            default="\n".join(lines)[:4000] or None,
            placeholder="Alien (1979)\nAliens (1986)\nAlien 3 (1992)",
        )
        self.add_item(self.name)
        self.add_item(self.items)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        items = franchise.parse_items(str(self.items.value))
        if not items:
            await interaction.response.send_message("I couldn't find any movies in that list.", ephemeral=True)
            return
        state = store.load(STATE)
        data = self.cog._data(state, self.s)
        old = data.get("queue") or {}
        keep = {k: old[k] for k in ("announced_for", "logged_for", "postponed_for", "paused") if k in old}
        data["queue"] = {**franchise.new_queue(str(self.name.value), items), **keep}
        store.save(STATE, state)
        nxt = franchise.next_part(data["queue"])
        await interaction.response.send_message(
            f"Saved **{data['queue']['name']}** ({len(items)} movies)."
            + (f" Next up: **{franchise.label(nxt)}**." if nxt else ""),
            ephemeral=True,
        )


class TypeMoviesModal(discord.ui.Modal, title="Type movies for the poll"):
    """Movies typed one per line (as many as the poll holds); open it again to change or add more."""

    def __init__(self, picker: ChooseView) -> None:
        super().__init__(timeout=600)
        self.picker = picker
        self.box = discord.ui.TextInput(
            label=f"Movies, one per line (up to {picker.limit})",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=2000,
            placeholder="Alien (1979)\nBrazil\nhttps://www.imdb.com/title/tt0078748/",
            default="\n".join(_movie_label(t) for t in picker.typed) or None,
        )
        self.add_item(self.box)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.picker.typed_in(interaction, self.box.value)


class ChooseView(discord.ui.View):
    """Lets whoever started the poll pick its movies: from the watchlist, typed in, or both. Typed movies join the
    watchlist when the poll is posted, so the winner is handled like any other."""

    interaction: discord.Interaction | None = None  # the command's, to say so when the menu expires

    async def on_timeout(self) -> None:
        if self.interaction is not None:
            try:
                await self.interaction.edit_original_response(
                    content="That menu expired. Run `/pingwin movie poll` again.", view=None
                )
            except discord.HTTPException:
                pass

    def __init__(
        self,
        cog: MovieNight,
        s,
        unwatched: list[dict],
        hours: int,
        question: str,
        owner: int,
        closes=None,
        ranked: bool = False,
        multiple: bool | None = None,
        ranks: int | None = None,
    ) -> None:
        super().__init__(timeout=600)
        self.cog, self.s, self.hours, self.question, self.owner = cog, s, hours, question, owner
        self.closes, self.ranked, self.multiple, self.ranks = closes, ranked, multiple, ranks
        self.noms = {n["id"]: n for n in unwatched}
        self.limit = POLL_MAX  # picked by hand: as many as Discord allows (How many is for oldest/random)
        self.chosen: list[str] = []  # watchlist movies picked (ids)
        self.typed: list[dict[str, Any]] = []  # typed movies that aren't on the watchlist yet
        self.missed: list[str] = []  # typed lines that couldn't be found
        self.render()

    def picks(self) -> list[str]:
        return [_movie_label(self.noms[i]) for i in self.chosen] + [_movie_label(t) for t in self.typed]

    def render(self) -> str:
        """Lay out the menu for what's picked so far, and return the message text that goes with it."""
        self.clear_items()
        listed = sorted(self.noms.values(), key=lambda n: n["added"])[:25]  # a menu holds 25
        self.listed = {n["id"] for n in listed}
        if listed:
            select = discord.ui.Select(
                placeholder="Choose from the watchlist…",
                min_values=0,
                max_values=len(listed),
                options=[
                    discord.SelectOption(label=_movie_label(n)[:100], value=n["id"], default=n["id"] in self.chosen)
                    for n in listed
                ],
            )
            select.callback = self.picked
            self.add_item(select)
        write = discord.ui.Button(label="Type movies", emoji="✏️", style=discord.ButtonStyle.secondary)
        write.callback = self.open_form
        self.add_item(write)
        picks = self.picks()
        post = discord.ui.Button(
            label="Post poll", style=discord.ButtonStyle.primary, disabled=not 2 <= len(picks) <= self.limit
        )
        post.callback = self.post
        self.add_item(post)
        where = "choose them from the watchlist, type them, or both" if listed else "type them with **Type movies**"
        lines = [f"Pick 2 to {self.limit} movies for the poll: {where}."]
        if picks:
            lines.append(f"**In the poll ({len(picks)}):** " + " · ".join(picks))
        if len(picks) > self.limit:
            lines.append(f"That's more than {self.limit}. Take out {len(picks) - self.limit} to post it.")
        if self.typed:
            lines.append("-# Typed movies are added to the watchlist when you post the poll.")
        if self.missed:
            lines.append("Couldn't find " + ", ".join(self.missed) + ". Type the title instead.")
        return "\n".join(lines)

    async def _owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner:
            await interaction.response.send_message("This menu belongs to whoever started the poll.", ephemeral=True)
            return False
        return True

    async def picked(self, interaction: discord.Interaction) -> None:
        if not await self._owner(interaction):
            return
        values = interaction.data.get("values", [])
        # Keep picks the menu doesn't list (typed ones that turned out to be on the watchlist past the first 25).
        self.chosen = [i for i in values if i in self.noms] + [i for i in self.chosen if i not in self.listed]
        await interaction.response.edit_message(content=self.render(), view=self)

    async def open_form(self, interaction: discord.Interaction) -> None:
        if not await self._owner(interaction):
            return
        await interaction.response.send_modal(TypeMoviesModal(self))

    async def typed_in(self, interaction: discord.Interaction, text: str) -> None:
        """The form came back: look each movie up (for its proper title and year), and use the watchlist's own
        entry when it's already there."""
        await interaction.response.defer()  # looking movies up can take a few seconds
        found, self.missed = await self.cog.look_up_typed(text)
        self.typed = []
        for t in found:
            n = next((n for n in self.noms.values() if _same_title(n, t["title"], t.get("year"))), None)
            if n is None:
                self.typed.append(t)
            elif n["id"] not in self.chosen:
                self.chosen.append(n["id"])
        await interaction.edit_original_response(content=self.render(), view=self)

    async def post(self, interaction: discord.Interaction) -> None:
        if not await self._owner(interaction):
            return
        if not 2 <= len(self.picks()) <= self.limit:
            await interaction.response.send_message(f"Pick 2 to {self.limit} movies.", ephemeral=True)
            return
        self.stop()  # done: it won't say it expired
        await interaction.response.edit_message(content="Posting the poll…", view=None)
        picks = [self.noms[i] for i in self.chosen]
        for t in self.typed:
            n = self.cog.add_to_watchlist(self.s, t, interaction.user.id)
            if all(n["id"] != p["id"] for p in picks):
                picks.append(n)
        msg = await self.cog.start_poll(
            interaction,
            self.s,
            picks,
            self.hours,
            self.question,
            self.closes,
            self.ranked,
            self.multiple,
            ranks=self.ranks,
        )
        if msg:
            await interaction.edit_original_response(content=f"Poll posted: {msg.jump_url}")
