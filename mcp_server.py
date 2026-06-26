#!/usr/bin/env python3
"""Kundali MCP server (local-compute edition).

Exposes a person's Vedic horoscope as MCP tools that return *raw, structured
chart data* — sidereal positions, dignities, houses, the full Vimshottari dasha
tree (Maha → Antar → Pratyantar), Sade Sati, transits and detected yogas — so
the LLM can do the interpretation and advise on the present situation itself.

Everything is computed locally with Swiss Ephemeris (`jyotish.py`); the only
network call is optional place geocoding (`geocode_place`). Sidereal zodiac,
Lahiri ayanamsa, mean node, whole-sign houses.

Run (stdio transport, for Claude Desktop / Claude Code / any MCP client):
    python mcp_server.py
"""
from __future__ import annotations

import re
from datetime import datetime

from mcp.server.fastmcp import FastMCP

import jyotish as J
from geo import geocode, tz_for  # OpenStreetMap geocoder / timezone resolver

mcp = FastMCP(
    "kundali",
    instructions=(
        "Vedic (Jyotish) chart tools. They return RAW computed data (positions, "
        "dignities, houses, dasha periods, transits, yogas) — you supply the "
        "interpretation.\n"
        "Flow: call `get_birth_chart` first to ground the reading; for 'what's "
        "happening now / what should I do', add `get_dashas`, `get_sade_sati`, "
        "and `get_current_transits`; use `get_yogas` for innate strengths.\n"
        "Use `geocode_place` if you only have a city name and need coordinates.\n"
        "Every tool takes the same birth fields. Calculations use the Lahiri "
        "ayanamsa, mean lunar node, and whole-sign houses.\n"
        "Frame readings as traditional astrological interpretation, not "
        "deterministic fact, and be supportive and non-alarming."
    ),
)

_BIRTH_DOC = """

Birth fields (same for every chart tool):
- name: person's name.
- date: birth date, YYYY-MM-DD.
- time: birth time on a 24h local clock, HH:MM or HH:MM:SS.
- place: birth city/town (geocoded if latitude/longitude omitted).
- sex: "male" or "female".
- latitude/longitude: optional decimal degrees (+N/+E) to override geocoding.
- tz_offset: optional base (non-DST) UTC offset in hours, e.g. 5.5 for IST."""


def _desc(summary: str) -> str:
    return summary.strip() + "\n" + _BIRTH_DOC


# --------------------------------------------------------------------------- #
# Birth construction
# --------------------------------------------------------------------------- #
def _build_birth(name, date, time, place, sex, latitude, longitude, tz_offset):
    m = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$", date)
    if not m:
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    year, month, day = (int(x) for x in m.groups())
    tm = re.match(r"\s*(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\s*$", time)
    if not tm:
        raise ValueError(f"time must be HH:MM or HH:MM:SS (24h), got {time!r}")
    hh, mm = int(tm.group(1)), int(tm.group(2))
    ss = int(tm.group(3)) if tm.group(3) else 0

    resolved = place
    if latitude is None or longitude is None or tz_offset is None:
        matches = geocode(place, limit=1)
        if not matches:
            raise ValueError(
                f"could not geocode {place!r}; pass latitude/longitude/tz_offset")
        top = matches[0]
        latitude = top["lat"] if latitude is None else latitude
        longitude = top["lon"] if longitude is None else longitude
        tz_offset = top["tz_offset"] if tz_offset is None else tz_offset
        resolved = top["name"] or place
    if tz_offset is None:
        tz_offset, _ = tz_for(latitude, longitude)

    return J.Birth(name=name, sex=sex,
                   dt=datetime(year, month, day, hh, mm, ss),
                   tz_offset=float(tz_offset), lat=float(latitude),
                   lon=float(longitude), place=resolved)


def _parse_as_of(as_of: str | None) -> datetime:
    if not as_of:
        return datetime.now()
    m = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$", as_of)
    if not m:
        raise ValueError(f"as_of must be YYYY-MM-DD, got {as_of!r}")
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, 0)


def _ord(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _header(b: J.Birth) -> str:
    return (f"Chart for {b.name} ({b.sex}) — {b.dt:%Y-%m-%d %H:%M:%S} local at "
            f"{b.place} (lat {b.lat:.4f}, lon {b.lon:.4f}, UTC{b.tz_offset:+}). "
            f"Sidereal/Lahiri, mean node, whole-sign houses.")


# --------------------------------------------------------------------------- #
# Formatters
# --------------------------------------------------------------------------- #
def _fmt_chart(b: J.Birth) -> str:
    c = J.compute_chart(b)
    pan = J.panchang(c)
    lines = [_header(b), ""]
    lines.append(f"Ascendant (Lagna): {J.SIGNS[c.asc_sign]} "
                 f"{J.dms(c.asc_lon - 30 * c.asc_sign)}  | Lagna lord: "
                 f"{J.RULER[c.asc_sign]}")
    lines.append(f"Moon sign (Rashi): {pan['moon_sign']}  | Sun sign: "
                 f"{pan['sun_sign']}  | Ayanamsa: {pan['ayanamsa']}")
    lines.append(f"Panchang: {pan['weekday']}, {pan['tithi']} tithi, "
                 f"Nakshatra {pan['nakshatra']}, Yoga {pan['yoga']}, "
                 f"Karana {pan['karana']}")
    lines.append(f"Dasha balance at birth: {J.dasha_balance(c)}")
    lines.append("")
    lines.append("Planetary positions:")
    lines.append(f"  {'Planet':8} {'Sign':12} {'Degree':11} {'Nakshatra':16} "
                 f"{'Pada':4} {'Retro':5} {'House':5} {'Dignity':11} Aspects")
    for nm in J.PLANETS:
        p = c.planets[nm]
        asp = ",".join(str(h) for h in c.aspected_houses(nm))
        lines.append(f"  {nm:8} {p.sign_name:12} {J.dms(p.deg_in_sign):11} "
                     f"{p.nakshatra:16} {p.pada:<4} "
                     f"{'yes' if p.retrograde else '-':5} {p.house:<5} "
                     f"{p.dignity or '-':11} {asp}")
    lines.append("")
    lines.append("Houses (whole-sign): " + ", ".join(
        f"H{h} {J.SIGNS[(c.asc_sign + h - 1) % 12]}"
        f"[{','.join(c.planets_in_house(h)) or '—'}]" for h in range(1, 13)))
    return "\n".join(lines)


def _fmt_period(p: J.DashaPeriod) -> str:
    return f"{p.lord} ({p.start:%Y-%m-%d} → {p.end:%Y-%m-%d})"


def _fmt_dasha(b: J.Birth, as_of: datetime) -> str:
    c = J.compute_chart(b)
    mds, _ = J.vimshottari(c, levels=3)
    maha, antar, prat = J.current_dasha(c, as_of)
    out = [_header(b), "",
           f"Dasha balance at birth: {J.dasha_balance(c)}",
           f"As of {as_of:%Y-%m-%d}:"]
    if maha:
        out.append(f"  ▶ Running: {maha.lord} Mahadasha › "
                   f"{antar.lord} Antardasha › {prat.lord} Pratyantardasha")
    out.append("")
    out.append("Vimshottari Mahadasha timeline:")
    for md in mds:
        mark = "  ◀ current" if maha and md.lord == maha.lord and md.contains(as_of) else ""
        out.append(f"  • {_fmt_period(md)}{mark}")
    if maha:
        out.append("")
        out.append(f"Antardashas within {maha.lord} Mahadasha:")
        for ad in maha.sub:
            mark = "  ◀ current" if antar and ad is antar else ""
            out.append(f"  • {_fmt_period(ad)}{mark}")
    if antar:
        out.append("")
        out.append(f"Pratyantardashas within {maha.lord}/{antar.lord}:")
        for pd in antar.sub:
            mark = "  ◀ current" if prat and pd is prat else ""
            out.append(f"  • {_fmt_period(pd)}{mark}")
    return "\n".join(out)


def _fmt_sade_sati(b: J.Birth, as_of: datetime) -> str:
    c = J.compute_chart(b)
    s = J.sade_sati(c, as_of)
    out = [_header(b), "", f"As of {as_of:%Y-%m-%d}:",
           f"  Natal Moon sign: {s['moon_sign']}",
           f"  Transit Saturn sign: {s['transit_saturn_sign']} "
           f"({'retrograde' if s['saturn_retrograde'] else 'direct'}), "
           f"{_ord(s['house_from_moon'])} from natal Moon",
           f"  Status: {s['status']}",
           f"  Sade Sati active: {s['sade_sati_active']}  |  "
           f"Small Panoti (Kantaka/Ashtama): {s['small_panoti']}",
           f"  Saturn in this sign from {s['saturn_entered_sign']} "
           f"to {s['saturn_leaves_sign']}",
           "",
           "Note: Sade Sati = Saturn transiting the 12th, 1st, or 2nd from the "
           "natal Moon (≈7.5 yrs across three ~2.5-yr phases: Rising/Peak/"
           "Setting). Saturn's retrograde can revisit a sign, shifting exact "
           "dates."]
    return "\n".join(out)


def _fmt_transits(b: J.Birth, as_of: datetime) -> str:
    c = J.compute_chart(b)
    moon_sign = c.planets["Moon"].sign
    tr = J.transit_positions(as_of)
    out = [_header(b), "", f"Transits (gochar) as of {as_of:%Y-%m-%d}:",
           f"  {'Planet':8} {'Sign':12} {'Degree':11} {'Nakshatra':16} "
           f"{'Retro':5} {'fromLagna':9} fromMoon"]
    for nm in J.PLANETS:
        t = tr[nm]
        from_lagna = (t["sign"] - c.asc_sign) % 12 + 1
        from_moon = (t["sign"] - moon_sign) % 12 + 1
        out.append(f"  {nm:8} {J.SIGNS[t['sign']]:12} "
                   f"{J.dms(t['deg_in_sign']):11} {t['nakshatra']:16} "
                   f"{'yes' if t['retrograde'] else '-':5} {from_lagna:<9} {from_moon}")
    out.append("")
    out.append("'fromLagna'/'fromMoon' = house counted from the natal ascendant "
               "/ natal Moon sign (1 = same sign).")
    return "\n".join(out)


def _fmt_yogas(b: J.Birth) -> str:
    c = J.compute_chart(b)
    yogas = J.detect_yogas(c)
    out = [_header(b), ""]
    if not yogas:
        out.append("No yogas detected from the curated rule set (this set is "
                   "not exhaustive).")
    else:
        out.append("Detected yogas (each with the rule that triggered it):")
        for y in yogas:
            out.append(f"  • {y['yoga']} — {y['basis']}")
    out.append("")
    out.append("Note: a curated subset (Gaja-Kesari, Budha-Aditya, "
               "Chandra-Mangala, Pancha Mahapurusha, Kemadruma, Kendra–Trikona "
               "Raj Yoga, Neecha-Bhanga). Absence here is not proof of absence.")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Health check (so platform GET / probes pass; MCP itself lives at /mcp)
# --------------------------------------------------------------------------- #
@mcp.custom_route("/", methods=["GET"])
async def health(_request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok", "service": "kundali-mcp",
                         "mcp_endpoint": "/mcp"})


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool(description=
    "Resolve a place name to coordinates and timezone for use in a birth chart. "
    "Returns candidate locations (name, latitude, longitude, UTC offset, IANA "
    "timezone). Use to confirm an ambiguous birthplace before computing a "
    "chart.\n\nquery: a city/town/place name, e.g. \"New Delhi\".")
def geocode_place(query: str) -> str:
    """Resolve a place name to coordinates + timezone."""
    matches = geocode(query)
    if not matches:
        return f"No locations found for {query!r}."
    lines = [f"{len(matches)} match(es) for {query!r}:"]
    for m in matches:
        off = m["tz_offset"]
        offs = f"UTC{off:+}" if off is not None else "UTC?"
        lines.append(f"- {m['name']}: lat {m['lat']:.4f}, lon {m['lon']:.4f}, "
                     f"{offs} ({m['tzid']}) — {m['display_name']}")
    return "\n".join(lines)


@mcp.tool(description=_desc(
    "Core Vedic birth chart (raw data): ascendant, panchang, dasha balance, and "
    "a table of every planet's sign, exact degree, nakshatra+pada, retrograde "
    "state, whole-sign house, dignity (exalted/own/friendly/neutral/enemy/"
    "debilitated) and the houses it aspects, plus the house occupancy map. Call "
    "this first; it grounds every other reading."))
def get_birth_chart(
    name: str, date: str, time: str, place: str, sex: str = "male",
    latitude: float | None = None, longitude: float | None = None,
    tz_offset: float | None = None,
) -> str:
    """Raw birth chart: ascendant, panchang, planet table, houses."""
    return _fmt_chart(_build_birth(name, date, time, place, sex,
                                   latitude, longitude, tz_offset))


@mcp.tool(description=_desc(
    "Vimshottari dasha tree (raw periods, no interpretation): the lifetime "
    "Mahadasha timeline plus the Antardashas of the running Mahadasha and the "
    "Pratyantardashas of the running Antardasha, with the active periods "
    "flagged. Optional as_of (YYYY-MM-DD) to evaluate a past/future date; "
    "defaults to today."))
def get_dashas(
    name: str, date: str, time: str, place: str, sex: str = "male",
    latitude: float | None = None, longitude: float | None = None,
    tz_offset: float | None = None, as_of: str | None = None,
) -> str:
    """Vimshottari Maha/Antar/Pratyantar dasha tree with current periods flagged."""
    b = _build_birth(name, date, time, place, sex, latitude, longitude, tz_offset)
    return _fmt_dasha(b, _parse_as_of(as_of))


@mcp.tool(description=_desc(
    "Sade Sati / Panoti status (raw): natal Moon sign vs transiting Saturn, the "
    "house Saturn occupies from the Moon, whether Sade Sati or a small Panoti "
    "(Kantaka/Ashtama Shani) is active, the current phase (Rising/Peak/Setting), "
    "and the dates Saturn entered/leaves its current sign. Optional as_of "
    "(YYYY-MM-DD); defaults to today."))
def get_sade_sati(
    name: str, date: str, time: str, place: str, sex: str = "male",
    latitude: float | None = None, longitude: float | None = None,
    tz_offset: float | None = None, as_of: str | None = None,
) -> str:
    """Sade Sati status: Saturn vs natal Moon, phase, ingress/egress dates."""
    b = _build_birth(name, date, time, place, sex, latitude, longitude, tz_offset)
    return _fmt_sade_sati(b, _parse_as_of(as_of))


@mcp.tool(description=_desc(
    "Current planetary transits (gochar, raw): each planet's transiting sign, "
    "degree, nakshatra and retrograde state, plus its house counted from the "
    "natal ascendant and from the natal Moon. The most direct input for 'what is "
    "going on right now'. Optional as_of (YYYY-MM-DD); defaults to today."))
def get_current_transits(
    name: str, date: str, time: str, place: str, sex: str = "male",
    latitude: float | None = None, longitude: float | None = None,
    tz_offset: float | None = None, as_of: str | None = None,
) -> str:
    """Transit positions relative to the natal lagna and Moon."""
    b = _build_birth(name, date, time, place, sex, latitude, longitude, tz_offset)
    return _fmt_transits(b, _parse_as_of(as_of))


@mcp.tool(description=_desc(
    "Detected yogas (raw): scans a curated rule set (Gaja-Kesari, Budha-Aditya, "
    "Chandra-Mangala, the five Pancha Mahapurusha yogas, Kemadruma, "
    "Kendra–Trikona Raj Yoga, Neecha-Bhanga) and returns each yoga found with "
    "the exact placement rule that triggered it, for you to interpret."))
def get_yogas(
    name: str, date: str, time: str, place: str, sex: str = "male",
    latitude: float | None = None, longitude: float | None = None,
    tz_offset: float | None = None,
) -> str:
    """Detected yogas with the rule basis for each."""
    return _fmt_yogas(_build_birth(name, date, time, place, sex,
                                   latitude, longitude, tz_offset))


if __name__ == "__main__":
    import os

    # Transport is selected via env vars so the same file serves local stdio
    # (Claude Desktop/Code) and remote HTTP (behind ngrok for the Claude app):
    #   KUNDALI_TRANSPORT = stdio (default) | http | sse
    #   KUNDALI_HOST      = bind address   (default 127.0.0.1)
    #   KUNDALI_PORT      = bind port       (default 8000)
    transport = os.environ.get("KUNDALI_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http", "streamable_http", "sse"):
        from mcp.server.transport_security import TransportSecuritySettings
        # Cloud hosts (Render/Railway/Cloud Run/Fly) inject the listen port via
        # $PORT and route to 0.0.0.0; locally we keep 127.0.0.1 for safety.
        default_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
        mcp.settings.host = os.environ.get("KUNDALI_HOST", default_host)
        mcp.settings.port = int(os.environ.get("KUNDALI_PORT")
                                or os.environ.get("PORT") or 8000)
        # The SDK's DNS-rebinding guard only trusts localhost; when proxied
        # through a tunnel (ngrok) the inbound Host header is the tunnel domain,
        # so relax it. Default "*" disables the guard (fine behind your own
        # tunnel); set KUNDALI_ALLOWED_HOSTS=host1,host2 to restrict instead.
        allowed = os.environ.get("KUNDALI_ALLOWED_HOSTS", "*").strip()
        if allowed == "*":
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=False)
        else:
            hosts = [h.strip() for h in allowed.split(",") if h.strip()]
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=hosts + [f"{h}:*" for h in hosts],
                allowed_origins=[f"https://{h}" for h in hosts]
                + [f"http://{h}" for h in hosts])
        kind = "sse" if transport == "sse" else "streamable-http"
        print(f"Kundali MCP serving {kind} on "
              f"http://{mcp.settings.host}:{mcp.settings.port}"
              f"{mcp.settings.streamable_http_path}", flush=True)
        mcp.run(transport=kind)
    else:
        mcp.run()
