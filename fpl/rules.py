"""The rules of the game, in one place.

Almost everything here is read from the live game rather than typed in:
`bootstrap-static` ships a `game_config.rules` block that *is* the
configuration the game runs on, so squad size, the per-club cap, the budget,
the sell-on fee and the free-transfer cap cannot drift out of date here without
drifting in the real game too.

One number is not in that payload — the points charged for a transfer beyond
your free ones — so it is pinned below as a named constant with its source. If
the game ever changes it, this is the single line to edit.

Reference: https://fantasy.premierleague.com/en/help/rules
"""

from dataclasses import dataclass

#: Points deducted for each transfer beyond the free ones. Not exposed by the
#: API; taken from the published transfer rules. Wildcard and Free Hit set this
#: to zero for the gameweek they are played in.
POINTS_PER_EXTRA_TRANSFER = 4

#: A squad may hold at most this many players from one Premier League club.
#: Overridden from the API when present.
DEFAULT_TEAM_LIMIT = 3

#: Formation floors and ceilings for the starting eleven. One keeper always;
#: the rest is any shape that fits inside these bounds and adds to eleven.
FORMATION = {
    1: (1, 1),   # GKP
    2: (3, 5),   # DEF
    3: (2, 5),   # MID
    4: (1, 3),   # FWD
}

#: Full-squad quota by position, which is fixed regardless of formation.
SQUAD_QUOTA = {1: 2, 2: 5, 3: 5, 4: 3}

POSITION_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


@dataclass(frozen=True)
class Rules:
    """The subset of `game_config.rules` this tool reasons about."""

    squad_size: int = 15
    starting_size: int = 11
    team_limit: int = DEFAULT_TEAM_LIMIT
    #: Tenths of a million, as the API stores prices. 1000 == £100.0m.
    total_spend: int = 1000
    sell_on_fee: float = 0.5
    #: Free transfers roll over up to 1 + max_extra_free_transfers.
    max_extra_free_transfers: int = 4
    transfers_cap: int = 20

    @property
    def max_free_transfers(self) -> int:
        return 1 + self.max_extra_free_transfers

    @classmethod
    def from_bootstrap(cls, bootstrap: dict) -> "Rules":
        cfg = bootstrap.get("game_config", {}).get("rules", {})
        settings = bootstrap.get("game_settings", {})

        def pick(key, fallback):
            for source in (cfg, settings):
                if source.get(key) is not None:
                    return source[key]
            return fallback

        return cls(
            squad_size=pick("squad_squadsize", 15),
            starting_size=pick("squad_squadplay", 11),
            team_limit=pick("squad_team_limit", DEFAULT_TEAM_LIMIT),
            total_spend=pick("squad_total_spend", 1000),
            sell_on_fee=pick("transfers_sell_on_fee", 0.5),
            max_extra_free_transfers=pick("max_extra_free_transfers", 4),
            transfers_cap=pick("transfers_cap", 20),
        )


def money(tenths: int) -> str:
    """Prices are stored in tenths of a million. Show them the way the game does."""
    return f"£{tenths / 10:.1f}m"


def selling_price(bought_for: int, now_cost: int, fee: float = 0.5) -> int:
    """What you actually receive for a player, in tenths.

    You keep the whole of any loss and half of any *profit*, rounded down to
    the nearest £0.1m — which is why a player who has risen £0.3m sells for
    £0.1m more than you paid, not £0.3m. A rise of £0.1m returns nothing extra.
    """
    if now_cost <= bought_for:
        return now_cost
    profit = now_cost - bought_for
    return bought_for + int(profit * (1 - fee))
