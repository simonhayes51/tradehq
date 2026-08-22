from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

BASE_URL = "https://www.fut.gg"
CARD_URL_RE = re.compile(
    r"^/players/(?P<player_id>\d+)-(?P<slug>[^/]+)/"
    r"(?P<game_year>\d+)-(?P<card_id>\d+)/?$"
)
RELATIVE_TIME_RE = re.compile(
    r"^\s*(?P<amount>\d+)\s+(?P<unit>second|minute|hour|day)s?\s+ago\s*$",
    re.IGNORECASE,
)
POSITIONS = {
    "GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM",
    "LM", "RM", "LW", "RW", "CF", "ST",
}


class CircuitBreaker:
    """Trips after `threshold` consecutive blocked/failed navigations, so
    a degraded or actively-blocking FUT.GG stops a run from continuing
    to hammer it. Any success resets the streak. Shared by
    futgg_player_sync.py and futgg_price_sync.py - originally defined
    only in the latter."""

    def __init__(self, threshold: int) -> None:
        self.threshold = threshold
        self._consecutive = 0
        self.tripped = False
        self.trip_reason: str | None = None

    def record_success(self) -> None:
        self._consecutive = 0

    def record_failure(self, reason: str) -> None:
        self._consecutive += 1
        if self._consecutive >= self.threshold:
            self.tripped = True
            self.trip_reason = reason


@dataclass
class SaleObservation:
    age_text: str
    age_seconds: int
    sold_price: int
    approximate_sold_at: datetime
    row_position: int
    occurrence_index: int
    fingerprint: str


@dataclass
class FutggCard:
    source_card_id: int
    source_player_id: int
    source_slug: str
    game_year: int
    source_url: str

    # Listing-only FUT.GG quality score. This is the value observed now,
    # not a claim about what FUT.GG displayed historically.
    gg_rating: Decimal | None = None

    name: str | None = None
    rating: int | None = None
    primary_position: str | None = None
    alternate_positions: list[str] = field(default_factory=list)
    rarity: str | None = None
    squad: str | None = None

    club: str | None = None
    league: str | None = None
    nation: str | None = None

    height_cm: int | None = None
    weight_kg: int | None = None
    foot: str | None = None
    skill_moves: int | None = None
    weak_foot: int | None = None
    accelerate_type: str | None = None
    body_type: str | None = None
    real_face: bool | None = None
    shirt_number: int | None = None
    age: int | None = None

    pace: int | None = None
    shooting: int | None = None
    passing: int | None = None
    dribbling: int | None = None
    defending: int | None = None
    physicality: int | None = None

    diving: int | None = None
    handling: int | None = None
    kicking: int | None = None
    reflexes: int | None = None
    speed: int | None = None
    positioning: int | None = None

    # ---- Display vs full name -------------------------------------------
    # `name` is left exactly as it always was: the Player Information
    # panel's "Name" row, which FUT.GG renders as the player's FULL name
    # ("Bruno Miguel Borges Fernandes"). 97 call sites across the backend
    # read futgg_players.name, and there is a lower(name) search index on
    # the market snapshot, so renaming it would be a breaking change for
    # no gain. Instead:
    #   full_name    - an explicit copy of that same Player Information
    #                  value, so the semantics are legible without having
    #                  to know the history of the `name` column.
    #   display_name - the SHORT name FUT.GG uses in its own player-profile
    #                  anchor ("Bruno Fernandes"), which is what a user
    #                  actually wants to read.
    # User-facing surfaces prefer COALESCE(display_name, name).
    display_name: str | None = None
    full_name: str | None = None

    # FUT.GG's own "Added On" timestamp from the Player Information panel -
    # when the CARD was added to the game. Deliberately distinct from
    # futgg_players.first_seen_at, which only records when OUR scraper
    # first discovered the card and is therefore useless for
    # reconstructing a content-release timeline.
    #
    # released_at_raw preserves the exact source string. Storing it costs
    # almost nothing and means that if we later establish FUT.GG renders
    # this in a timezone other than the one we assumed, every row can be
    # re-derived without re-scraping the entire catalogue.
    released_at: datetime | None = None
    released_at_raw: str | None = None

    player_image_url: str | None = None
    card_design_image_url: str | None = None
    card_image_url: str | None = None
    club_image_url: str | None = None
    league_image_url: str | None = None
    nation_image_url: str | None = None
    club_source_id: int | None = None
    league_source_id: int | None = None
    nation_source_id: int | None = None

    lowest_bin: int | None = None
    lowest_bin_age: str | None = None
    price_range_low: int | None = None
    price_range_high: int | None = None
    recent_sales: list[SaleObservation] = field(default_factory=list)
    price_outcome: str = "no_active_market"

    parse_warnings: list[str] = field(default_factory=list)


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def parse_price(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = clean_text(value).lower().replace(",", "")
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*([km]?)\b", cleaned)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return int(number)


def relative_age_seconds(value: str) -> int | None:
    match = RELATIVE_TIME_RE.fullmatch(value)
    if not match:
        return None
    amount = int(match.group("amount"))
    multiplier = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
    }[match.group("unit").lower()]
    return amount * multiplier


def _image_id(url: str | None, asset_type: str) -> int | None:
    if not url:
        return None
    match = re.search(rf"/{re.escape(asset_type)}/(\d+)[.\-]", url)
    return int(match.group(1)) if match else None


# FUT.GG serves the composited player-item-card image through a Cloudflare
# Image Resizing path (`/cdn-cgi/image/quality=85,format=auto,width=300/...`)
# - the width in that path is a resize request, not the source image's real
# size. The listing/search grid asks for a 300px thumbnail; we want a larger
# source before permanently re-hosting our own copy (see
# futgg_card_image_mirror.py in backend), so bump it here at parse time
# rather than storing the 300px URL and hoping every future consumer
# remembers to upsize it.
_CDN_WIDTH_RE = re.compile(r"(/cdn-cgi/image/[^/]*?width=)(\d+)")
CARD_IMAGE_MIRROR_WIDTH = 512


def upsize_card_image(url: str, width: int = CARD_IMAGE_MIRROR_WIDTH) -> str:
    return _CDN_WIDTH_RE.sub(lambda m: f"{m.group(1)}{width}", url, count=1)


def _find_exact(soup: BeautifulSoup, text: str):
    wanted = text.casefold()
    return soup.find(
        lambda tag: tag.name in {"h1", "h2", "h3", "h4", "span", "div", "td", "dt"}
        and clean_text(tag.get_text(" ", strip=True)).casefold() == wanted
    )


# FUT.GG renders "Added On" as e.g. "Mar 18, 2026, 6:00 PM". No timezone
# is present in the markup, so the timezone this represents is decided by
# how the page is rendered, not by anything in the string.
#
# WHY WE TREAT IT AS UTC (and why that is not a blind assumption):
# futgg_player_sync.py pins the Playwright browser context to
# timezone_id=FUTGG_BROWSER_TIMEZONE (default "UTC"). If FUT.GG renders
# this value client-side in the viewer's local timezone - which is the
# common pattern - then pinning the browser makes the rendered string UTC
# by construction. If instead it is server-rendered in some fixed zone,
# pinning changes nothing and the assumption is simply recorded. Either
# way the result is deterministic rather than dependent on whatever
# timezone the container host happens to have.
#
# released_at_raw preserves the source string, so if we later establish
# FUT.GG uses a different fixed zone, every stored row can be re-derived
# without re-scraping.
# Which timezone the rendered "Added On" string represents. Kept as a
# module constant (rather than inlined) so it is a single, documented,
# overridable decision - see parse_added_on's docstring for why UTC is
# the correct default given the pinned browser timezone.
ADDED_ON_ASSUMED_TIMEZONE = timezone.utc

_ADDED_ON_FORMATS = (
    "%b %d, %Y, %I:%M %p",   # Mar 18, 2026, 6:00 PM
    "%B %d, %Y, %I:%M %p",   # March 18, 2026, 6:00 PM
    "%b %d, %Y %I:%M %p",    # no comma before the time
    "%B %d, %Y %I:%M %p",
    "%b %d, %Y",             # date only
    "%B %d, %Y",
)


def parse_added_on(value: str | None, assume_timezone: timezone = timezone.utc) -> datetime | None:
    """Parse a FUT.GG "Added On" string into an aware datetime.

    Returns None (never a fabricated timestamp) when the value is absent
    or unparseable - the caller is expected to record a parse warning so
    a format change surfaces as a visible warning rather than as silently
    missing release data.
    """
    text = clean_text(value)
    if not text:
        return None
    # Normalise the narrow/non-breaking spaces some renderers emit around
    # the meridiem, which would otherwise defeat an exact strptime match.
    text = text.replace("\u202f", " ").replace("\xa0", " ")
    text = " ".join(text.split())
    for fmt in _ADDED_ON_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=assume_timezone)
    return None


def _find_display_name(soup: BeautifulSoup, source_player_id: int | None) -> str | None:
    """The SHORT player name, from FUT.GG's own player-profile anchor.

    Identified structurally, never by Tailwind class: the profile URL is
    /players/{source_player_id}-{slug}/ - note it has NO card/item id
    segment, which is what distinguishes it from the card-version URL
    (/players/{player_id}-{slug}/{game_year}-{card_id}/). Matching on the
    known source_player_id also stops a navigation or "similar players"
    link from being picked up by accident.
    """
    if not source_player_id:
        return None
    pattern = re.compile(rf"^/players/{source_player_id}-[^/]+/$")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        # Tolerate absolute URLs as well as root-relative ones.
        if href.startswith(BASE_URL):
            href = href[len(BASE_URL):]
        if not href.startswith("/"):
            href = "/" + href
        if not pattern.match(href):
            continue
        text = clean_text(anchor.get_text(" ", strip=True))
        # Guard against an anchor that wraps an image/badge rather than
        # the name, and against absurdly long text that is clearly not a
        # display name.
        if text and 1 < len(text) <= 60:
            return text
    return None


def _find_label_value(soup: BeautifulSoup, label: str) -> tuple[str | None, str | None]:
    node = _find_exact(soup, label)
    if node is None:
        return None, None

    current = node
    label_text = clean_text(node.get_text(" ", strip=True))
    for _ in range(5):
        parent = current.parent
        if parent is None:
            break

        direct = parent.find_all(["div", "span", "td", "dd"], recursive=False)
        candidates: list[str] = []
        for child in direct:
            text = clean_text(child.get_text(" ", strip=True))
            if text and text.casefold() != label_text.casefold() and not text.casefold().startswith(label_text.casefold() + " "):
                candidates.append(text)
        if candidates:
            img = parent.find("img", src=True)
            return candidates[-1], urljoin(BASE_URL, img["src"]) if img else None

        full = clean_text(parent.get_text(" ", strip=True))
        if full.casefold().startswith(label_text.casefold() + " "):
            value = full[len(label_text):].strip()
            if value and len(value) <= 150:
                img = parent.find("img", src=True)
                return value, urljoin(BASE_URL, img["src"]) if img else None
        current = parent

    return None, None


def parse_card_url(url: str) -> tuple[int, int, str, int]:
    match = CARD_URL_RE.match(urlparse(url).path)
    if not match:
        raise ValueError(f"Unexpected FUT.GG card URL: {url}")
    return (
        int(match.group("card_id")),
        int(match.group("player_id")),
        match.group("slug"),
        int(match.group("game_year")),
    )


def _parse_int_field(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\d+", value.replace(",", ""))
    return int(match.group(0)) if match else None


def _parse_bool(value: str | None) -> bool | None:
    text = clean_text(value).casefold()
    if text in {"yes", "true"}:
        return True
    if text in {"no", "false"}:
        return False
    return None


def _parse_card_visual(soup: BeautifulSoup, card: FutggCard) -> None:
    visual = soup.select_one(".fc-card")
    if visual is None:
        card.parse_warnings.append("fc-card not found")
        return

    for node in visual.select(".font-cruyff-bold, [class*='font-cruyff-bold']"):
        text = clean_text(node.get_text(" ", strip=True))
        if text and not text.isdigit():
            card.name = text
            break

    texts = [clean_text(node.get_text(" ", strip=True)) for node in visual.find_all(["div", "span"])]
    for index, text in enumerate(texts):
        if re.fullmatch(r"\d{2}", text) and 40 <= int(text) <= 99:
            following = texts[index + 1] if index + 1 < len(texts) else ""
            if following in POSITIONS:
                card.rating = int(text)
                card.primary_position = following
                break

    stat_map = {
        "PAC": "pace", "SHO": "shooting", "PAS": "passing",
        "DRI": "dribbling", "DEF": "defending", "PHY": "physicality",
        "DIV": "diving", "HAN": "handling", "KIC": "kicking",
        "REF": "reflexes", "SPD": "speed", "POS": "positioning",
    }
    for label, field_name in stat_map.items():
        node = visual.find(lambda tag: clean_text(tag.get_text(" ", strip=True)) == label)
        if node and node.parent:
            nums = [int(x) for x in re.findall(r"\b(\d{1,2})\b", clean_text(node.parent.get_text(" ", strip=True)))]
            nums = [x for x in nums if 0 <= x <= 99]
            if nums:
                setattr(card, field_name, nums[-1])

    positions: list[str] = []
    for node in visual.find_all(["div", "span"]):
        text = clean_text(node.get_text(" ", strip=True))
        if text in POSITIONS:
            positions.append(text)
    positions = list(dict.fromkeys(positions))
    if card.primary_position in positions:
        positions.remove(card.primary_position)
    card.alternate_positions = positions

    # card.card_image_url (the single pre-composited full-card render -
    # background + cutout + rating/price badge, see upsize_card_image's
    # docstring) is deliberately NOT captured here. It was originally
    # assumed this detail page's own .fc-card would carry the same
    # "player-item-card/" image the /players/new/ LISTING grid tiles do -
    # confirmed wrong via a live production backfill that reached 100%
    # coverage on player_image_url/card_design_image_url below (proving
    # this loop runs correctly) while card_image_url stayed at 0%
    # coverage across the whole catalogue. FUT.GG simply doesn't render
    # that composited image on the individual card page at all - only
    # futgg_player_sync.py's collect_listing_urls()/_read_card_hrefs()
    # can capture it, from the listing grid.
    for image in visual.find_all("img", src=True):
        src = urljoin(BASE_URL, image["src"])
        alt = clean_text(image.get("alt")).casefold()
        if "/player-item/" in src:
            card.player_image_url = src
        elif "/rarities-" in src:
            card.card_design_image_url = src
        elif alt == "club":
            card.club_image_url = src
            card.club_source_id = _image_id(src, "club")
        elif alt == "league":
            card.league_image_url = src
            card.league_source_id = _image_id(src, "league")
        elif alt == "nation":
            card.nation_image_url = src
            card.nation_source_id = _image_id(src, "nation")


def _parse_player_information(soup: BeautifulSoup, card: FutggCard) -> None:
    fields = {
        "Name": "name", "Club": "club", "League": "league",
        "Nation": "nation", "Rarity": "rarity", "Squad": "squad",
        "Foot": "foot", "AcceleRATE": "accelerate_type", "Body Type": "body_type",
    }
    for label, attr in fields.items():
        value, image = _find_label_value(soup, label)
        if value:
            setattr(card, attr, value)
        if image:
            if label == "Club":
                card.club_image_url = image
                card.club_source_id = _image_id(image, "club")
            elif label == "League":
                card.league_image_url = image
                card.league_source_id = _image_id(image, "league")
            elif label == "Nation":
                card.nation_image_url = image
                card.nation_source_id = _image_id(image, "nation")

    height, _ = _find_label_value(soup, "Height")
    weight, _ = _find_label_value(soup, "Weight")
    skills, _ = _find_label_value(soup, "Skill Moves")
    weak_foot, _ = _find_label_value(soup, "Weak Foot")
    real_face, _ = _find_label_value(soup, "Real Face")
    shirt_number, _ = _find_label_value(soup, "Shirt Number")
    age, _ = _find_label_value(soup, "Age")

    card.height_cm = _parse_int_field(height)
    card.weight_kg = _parse_int_field(weight)
    card.skill_moves = _parse_int_field(skills)
    card.weak_foot = _parse_int_field(weak_foot)
    card.real_face = _parse_bool(real_face)
    card.shirt_number = _parse_int_field(shirt_number)
    card.age = _parse_int_field(age)

    # ---- full_name / display_name ---------------------------------------
    # full_name mirrors the Player Information "Name" row we just read into
    # card.name (kept in sync deliberately - see FutggCard's own comment on
    # why `name` itself is not repurposed).
    if card.name:
        card.full_name = card.name
    display = _find_display_name(soup, card.source_player_id)
    if display:
        card.display_name = display
    else:
        # Not fatal: user-facing surfaces fall back to `name`. Warned so a
        # markup change shows up rather than silently degrading every
        # card's name back to the long legal form.
        card.parse_warnings.append("display_name_not_found")

    # ---- released_at (FUT.GG "Added On") ---------------------------------
    added_on, _ = _find_label_value(soup, "Added On")
    if added_on:
        card.released_at_raw = added_on
        parsed = parse_added_on(added_on, assume_timezone=ADDED_ON_ASSUMED_TIMEZONE)
        if parsed is not None:
            card.released_at = parsed
        else:
            # Never invent a timestamp - a release date we cannot parse
            # must stay NULL so the promo/event layer does not silently
            # build a timeline on a guess.
            card.parse_warnings.append(f"released_at_unparseable:{added_on[:40]}")
    else:
        card.parse_warnings.append("released_at_not_found")


UNTRADEABLE_MARKERS = (
    "not tradeable",
    "untradeable",
    "this item cannot be sold",
    "sbc reward",
    "objective reward",
)


def detect_price_outcome(soup: BeautifulSoup, card: "FutggCard") -> str:
    """Classifies why a card page did/did not yield a price, so an SBC or
    objective-only card isn't treated the same as a genuine scrape
    failure (see futgg_price_sync.py's status handling)."""
    if card.lowest_bin is not None or card.recent_sales:
        return "success"

    page_text = clean_text(soup.get_text(" ", strip=True)).casefold()
    if any(marker in page_text for marker in UNTRADEABLE_MARKERS):
        return "untradeable"

    if soup.select_one("#prices-overview") is None:
        return "price_section_missing"

    return "no_active_market"


def _find_recent_sales_table(soup: BeautifulSoup):
    heading = _find_exact(soup, "Recent Sales")
    if heading is None:
        return None
    current = heading
    for _ in range(5):
        current = current.parent
        if current is None:
            return None
        table = current.find("table")
        if table is not None:
            return table
    return None


def _parse_prices(soup: BeautifulSoup, card: FutggCard, captured_at: datetime) -> None:
    overview = soup.select_one("#prices-overview")
    if overview is not None:
        heading = _find_exact(overview, "Lowest BIN")
        if heading:
            current = heading
            for _ in range(4):
                current = current.parent
                if current is None:
                    break
                for value_node in current.select("span.tabular-nums"):
                    price = parse_price(value_node.get_text(" ", strip=True))
                    if price is not None:
                        card.lowest_bin = price
                        break
                if card.lowest_bin is not None:
                    age_node = current.find(string=RELATIVE_TIME_RE)
                    if age_node:
                        card.lowest_bin_age = clean_text(str(age_node))
                    break

        for node in overview.find_all(["div", "span"]):
            text = clean_text(node.get_text(" ", strip=True))
            match = re.fullmatch(r"(\d+(?:\.\d+)?[kKmM]?)\s*-\s*(\d+(?:\.\d+)?[kKmM]?)", text)
            if match:
                card.price_range_low = parse_price(match.group(1))
                card.price_range_high = parse_price(match.group(2))
                break

    table = _find_recent_sales_table(soup)
    if table is None or table.find("tbody") is None:
        return

    occurrence_counts: dict[tuple[int, int], int] = {}
    for row_position, row in enumerate(table.find("tbody").find_all("tr"), start=1):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        age_text = clean_text(cells[0].get_text(" ", strip=True))
        age_seconds = relative_age_seconds(age_text)
        sold_price = parse_price(cells[1].get_text(" ", strip=True))
        if age_seconds is None or sold_price is None:
            continue

        # WARNING - do not revive this path without fixing the drift.
        #
        # age_seconds comes from a whole-unit label ("1 hour ago"), so it
        # stays constant while real time moves on, but captured_at does
        # not. The derived timestamp therefore slides forward on every
        # re-scrape of the SAME sale, and since the fingerprint below is
        # built from it, each slide stores a NEW row. Measured: one real
        # sale observed six times became three distinct rows, each
        # claiming a different sale time.
        #
        # Harmless today only because nothing live calls this - the
        # deployed worker is futgg_sales_sync.py, which builds rows from
        # the API's exact soldDate (see its build_sale_rows) and does not
        # have this problem. futgg_price_sync.py, the only caller, is not
        # deployed. Fix by flooring to the label's own unit before
        # fingerprinting if this is ever brought back.
        approx = captured_at - timedelta(seconds=age_seconds)
        rounded = (approx + timedelta(seconds=30)).replace(second=0, microsecond=0)
        key = (int(rounded.timestamp()), sold_price)
        occurrence_index = occurrence_counts.get(key, 0) + 1
        occurrence_counts[key] = occurrence_index
        raw = f"{card.source_card_id}|{rounded.isoformat()}|{sold_price}|{occurrence_index}"
        fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        card.recent_sales.append(
            SaleObservation(
                age_text=age_text,
                age_seconds=age_seconds,
                sold_price=sold_price,
                approximate_sold_at=rounded,
                row_position=row_position,
                occurrence_index=occurrence_index,
                fingerprint=fingerprint,
            )
        )


def parse_futgg_card(html: str, url: str, captured_at: datetime | None = None) -> FutggCard:
    captured_at = captured_at or datetime.now(timezone.utc)
    source_card_id, source_player_id, slug, game_year = parse_card_url(url)
    card = FutggCard(
        source_card_id=source_card_id,
        source_player_id=source_player_id,
        source_slug=slug,
        game_year=game_year,
        source_url=url,
    )
    soup = BeautifulSoup(html, "html.parser")
    _parse_card_visual(soup, card)
    _parse_player_information(soup, card)
    _parse_prices(soup, card, captured_at)
    card.price_outcome = detect_price_outcome(soup, card)
    return card


def classify_price_tier(card: FutggCard | dict[str, Any]) -> str:
    rating = card.rating if isinstance(card, FutggCard) else card.get("rating")
    rarity = card.rarity if isinstance(card, FutggCard) else card.get("rarity")
    rarity_text = clean_text(rarity).casefold()
    base_values = {
        "common", "rare", "gold", "silver", "bronze", "gold common", "gold rare",
        "silver common", "silver rare", "bronze common", "bronze rare",
    }
    if rarity_text and rarity_text not in base_values:
        return "special"
    if rating is not None and rating >= 75 and "rare" in rarity_text:
        return "gold_rare"
    if rating is not None and rating >= 75:
        return "gold_common"
    if rating is not None and rating >= 65:
        return "silver"
    return "bronze"
