"""Movie night: watchlists, polls that close at showtime, a history of every movie watched, and ratings.

* Each series (like MMM or FFF) has its own watchlist, role, channel and weekly showtime.
* Members' changes (nominations, removals, watched marks, history fixes) can need a mod's OK: Pingwin posts
  a card with Approve / Deny in the mods' channel. Mods' own changes go straight through.
* After a movie, Pingwin posts a "Rate it" button. Everyone gives a score out of 10, a comment, and can ask
  a silly question of someone they pick (who gets pinged) or of whoever rates next. Ratings can also come in
  as voice messages (see cogs.voicerating). When ratings close, Pingwin posts the group average.
* Movie details (year, runtime, IMDb rating, director, stars, poster) come from OMDb, looked up once.

The watchlists live in state/movienight.json and the library (history, ratings, details, requests) in
state/movielib.json (see pingwin.movies).

The code: cog.py is the cog itself (settings, background loops, slash commands); its other methods are in the
mixins beside it (details, queues, changes, nights, weeks_off, ratings, polls). common.py has the shared helpers
and views.py the buttons, menus and forms. Everything other code imports is re-exported here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .cog import MovieNight
from .common import (
    DEFAULT_RUNTIME,
    GENERAL,
    GOLD,
    PINGWIN_POLLS,
    POLL_MAX,
    POLL_NAMES,
    POLL_TYPES,
    SOONEST,
    STATE,
    TYPED_YEAR,
    candidate_label,
    describe_request,
    imdb_url,
    is_franchise,
    log,
    movie_card,
    parse_from_date,
    parse_typed_movie,
    parse_watch_date,
    pick_movies,
    pick_options,
    poll_close,
    poll_details_embed,
    series_label,
    tallies,
    voter_count,
    winners,
)
from .views import (
    ANSWER_LIMIT,
    ASK_LIMIT,
    COMMENT_LIMIT,
    ORDINALS,
    PICKED,
    BreakButton,
    ChooseView,
    Decision,
    ExpiringView,
    FranchiseModal,
    NamedMember,
    NightPick,
    OtherNightModal,
    PollVoteButton,
    RankedModal,
    RankedVoteButton,
    RateButton,
    RateModal,
    RequestButton,
    StarterButton,
    StarterMenu,
    StarterPick,
    TypeMoviesModal,
    open_rating,
    pick_asked,
    poll_view,
    show_decision,
)

if TYPE_CHECKING:
    from ...bot import Pingwin

__all__ = [
    "ANSWER_LIMIT",
    "ASK_LIMIT",
    "BreakButton",
    "COMMENT_LIMIT",
    "ChooseView",
    "DEFAULT_RUNTIME",
    "Decision",
    "ExpiringView",
    "FranchiseModal",
    "GENERAL",
    "GOLD",
    "MovieNight",
    "NamedMember",
    "NightPick",
    "ORDINALS",
    "OtherNightModal",
    "PICKED",
    "PINGWIN_POLLS",
    "POLL_MAX",
    "POLL_NAMES",
    "POLL_TYPES",
    "PollVoteButton",
    "RankedModal",
    "RankedVoteButton",
    "RateButton",
    "RateModal",
    "RequestButton",
    "SOONEST",
    "STATE",
    "StarterButton",
    "StarterMenu",
    "StarterPick",
    "TYPED_YEAR",
    "TypeMoviesModal",
    "candidate_label",
    "describe_request",
    "imdb_url",
    "is_franchise",
    "log",
    "movie_card",
    "open_rating",
    "parse_from_date",
    "parse_typed_movie",
    "parse_watch_date",
    "pick_asked",
    "pick_movies",
    "pick_options",
    "poll_close",
    "poll_details_embed",
    "poll_view",
    "series_label",
    "show_decision",
    "tallies",
    "voter_count",
    "winners",
]


async def setup(bot: Pingwin) -> None:
    bot.add_dynamic_items(
        RequestButton, RateButton, NightPick, BreakButton, StarterPick, StarterButton, RankedVoteButton, PollVoteButton
    )
    await bot.add_cog(MovieNight(bot))
