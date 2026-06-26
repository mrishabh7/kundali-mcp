#!/usr/bin/env python3
"""Local Vedic-astrology computation engine (Swiss Ephemeris).

Computes the *raw* chart facts — sidereal planet positions, ascendant, nakshatra/
pada, dignities, houses, aspects, Vimshottari dasha tree, Sade Sati, transits and
yogas — so an LLM can do the interpretation itself. No network, no scraping.

Conventions: sidereal zodiac with the **Lahiri** ayanamsa, **mean** lunar node
for Rahu/Ketu, **whole-sign** houses (house 1 = ascendant sign) — the common
Vedic defaults; positions come straight from Swiss Ephemeris.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import swisseph as swe

swe.set_sid_mode(swe.SIDM_LAHIRI)
_FLAGS = swe.FLG_MOSEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra",
         "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]

NAKSHATRAS = [
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra",
    "Punarvasu", "Pushya", "Ashlesha", "Magha", "Purva Phalguni",
    "Uttara Phalguni", "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha",
    "Jyeshtha", "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana",
    "Dhanishta", "Shatabhisha", "Purva Bhadrapada", "Uttara Bhadrapada",
    "Revati"]

# Vimshottari order and mahadasha lengths (years); total 120.
DASHA_ORDER = ["Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter",
               "Saturn", "Mercury"]
DASHA_YEARS = {"Ketu": 7, "Venus": 20, "Sun": 6, "Moon": 10, "Mars": 7,
               "Rahu": 18, "Jupiter": 16, "Saturn": 19, "Mercury": 17}
YEAR_DAYS = 365.25  # civil-year convention used by most Vimshottari software

PLANETS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn",
           "Rahu", "Ketu"]
_SWE_ID = {"Sun": swe.SUN, "Moon": swe.MOON, "Mars": swe.MARS,
           "Mercury": swe.MERCURY, "Jupiter": swe.JUPITER, "Venus": swe.VENUS,
           "Saturn": swe.SATURN, "Rahu": swe.MEAN_NODE}

RULER = {0: "Mars", 1: "Venus", 2: "Mercury", 3: "Moon", 4: "Sun", 5: "Mercury",
         6: "Venus", 7: "Mars", 8: "Jupiter", 9: "Saturn", 10: "Saturn",
         11: "Jupiter"}
OWN_SIGNS = {"Sun": [4], "Moon": [3], "Mars": [0, 7], "Mercury": [2, 5],
             "Jupiter": [8, 11], "Venus": [1, 6], "Saturn": [9, 10]}
EXALT_SIGN = {"Sun": 0, "Moon": 1, "Mars": 9, "Mercury": 5, "Jupiter": 3,
              "Venus": 11, "Saturn": 6}
FRIENDS = {"Sun": {"Moon", "Mars", "Jupiter"}, "Moon": {"Sun", "Mercury"},
           "Mars": {"Sun", "Moon", "Jupiter"}, "Mercury": {"Sun", "Venus"},
           "Jupiter": {"Sun", "Moon", "Mars"}, "Venus": {"Mercury", "Saturn"},
           "Saturn": {"Mercury", "Venus"}}
ENEMIES = {"Sun": {"Venus", "Saturn"}, "Moon": set(), "Mars": {"Mercury"},
           "Mercury": {"Moon"}, "Jupiter": {"Mercury", "Venus"},
           "Venus": {"Sun", "Moon"}, "Saturn": {"Sun", "Moon", "Mars"}}
# Special graha drishti (full aspects), counted inclusively from the planet.
ASPECTS = {"Mars": [4, 7, 8], "Jupiter": [5, 7, 9], "Saturn": [3, 7, 10],
           "Rahu": [5, 7, 9], "Ketu": [5, 7, 9]}

NAK_LEN = 360.0 / 27.0      # 13°20'
PADA_LEN = NAK_LEN / 4.0    # 3°20'


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #
def julian_day(year, month, day, hour, minute, sec, tz_offset):
    """Julian Day (UT) from a local civil date/time and base UTC offset."""
    ut = datetime(year, month, day, hour, minute, sec) - timedelta(hours=tz_offset)
    return swe.julday(ut.year, ut.month, ut.day,
                      ut.hour + ut.minute / 60 + ut.second / 3600)


def dms(deg_in_sign: float) -> str:
    d = int(deg_in_sign)
    mfull = (deg_in_sign - d) * 60
    m = int(mfull)
    s = int(round((mfull - m) * 60))
    if s == 60:
        s = 0
        m += 1
    return f"{d:02d}°{m:02d}'{s:02d}\""


def nakshatra_of(lon: float):
    idx = int(lon // NAK_LEN) % 27
    pada = int((lon % NAK_LEN) // PADA_LEN) + 1
    lord = DASHA_ORDER[idx % 9]
    return NAKSHATRAS[idx], pada, lord, idx


def dignity(planet: str, sign: int) -> str:
    if planet in ("Rahu", "Ketu"):
        return ""
    if sign == EXALT_SIGN[planet]:
        return "Exalted"
    if sign == (EXALT_SIGN[planet] + 6) % 12:
        return "Debilitated"
    if sign in OWN_SIGNS[planet]:
        return "Own"
    lord = RULER[sign]
    if lord in FRIENDS[planet]:
        return "Friendly"
    if lord in ENEMIES[planet]:
        return "Enemy"
    return "Neutral"


def sidereal_lon(jd: float, planet: str) -> tuple[float, float]:
    """Return (sidereal longitude, longitude speed °/day). Ketu = Rahu + 180°."""
    if planet == "Ketu":
        lon, sp = sidereal_lon(jd, "Rahu")
        return (lon + 180.0) % 360.0, sp
    pos, _ = swe.calc_ut(jd, _SWE_ID[planet], _FLAGS)
    return pos[0] % 360.0, pos[3]


def ascendant_lon(jd: float, lat: float, lon: float) -> float:
    """Sidereal ascendant longitude (tropical ascendant minus ayanamsa)."""
    _, ascmc = swe.houses(jd, lat, lon, b"W")
    return (ascmc[0] - swe.get_ayanamsa_ut(jd)) % 360.0


# --------------------------------------------------------------------------- #
# Birth + chart model
# --------------------------------------------------------------------------- #
@dataclass
class Birth:
    name: str
    sex: str
    dt: datetime          # local civil birth datetime
    tz_offset: float      # base UTC offset, hours
    lat: float
    lon: float
    place: str
    jd: float = field(init=False)

    def __post_init__(self):
        self.jd = julian_day(self.dt.year, self.dt.month, self.dt.day,
                             self.dt.hour, self.dt.minute, self.dt.second,
                             self.tz_offset)


@dataclass
class PlanetPos:
    name: str
    lon: float          # sidereal longitude 0..360
    sign: int           # 0..11
    deg_in_sign: float
    nakshatra: str
    pada: int
    nak_lord: str
    retrograde: bool
    house: int          # whole-sign house from ascendant
    dignity: str

    @property
    def sign_name(self):
        return SIGNS[self.sign]


@dataclass
class Chart:
    birth: Birth
    asc_lon: float
    asc_sign: int
    planets: dict[str, PlanetPos]

    @property
    def lagna_sign(self):
        return self.asc_sign

    def house_of_sign(self, sign: int) -> int:
        return (sign - self.asc_sign) % 12 + 1

    def aspected_houses(self, planet: str) -> list[int]:
        h = self.planets[planet].house
        return [((h - 1) + (a - 1)) % 12 + 1 for a in ASPECTS.get(planet, [7])]

    def planets_in_house(self, house: int) -> list[str]:
        return [p for p, pp in self.planets.items() if pp.house == house]

    def lord_of_house(self, house: int) -> str:
        sign = (self.asc_sign + house - 1) % 12
        return RULER[sign]


def compute_chart(birth: Birth) -> Chart:
    asc = ascendant_lon(birth.jd, birth.lat, birth.lon)
    asc_sign = int(asc // 30)
    planets = {}
    for name in PLANETS:
        lon, speed = sidereal_lon(birth.jd, name)
        sign = int(lon // 30)
        nak, pada, nlord, _ = nakshatra_of(lon)
        planets[name] = PlanetPos(
            name=name, lon=lon, sign=sign, deg_in_sign=lon - 30 * sign,
            nakshatra=nak, pada=pada, nak_lord=nlord,
            retrograde=(speed < 0 and name not in ("Rahu", "Ketu")),
            house=(sign - asc_sign) % 12 + 1, dignity=dignity(name, sign))
    return Chart(birth=birth, asc_lon=asc, asc_sign=asc_sign, planets=planets)


# --------------------------------------------------------------------------- #
# Panchang (instantaneous, at the birth moment)
# --------------------------------------------------------------------------- #
_TITHI_NAMES = ["Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami",
                "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami",
                "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi"]
_YOGA_NAMES = ["Vishkambha", "Priti", "Ayushman", "Saubhagya", "Shobhana",
               "Atiganda", "Sukarman", "Dhriti", "Shula", "Ganda", "Vriddhi",
               "Dhruva", "Vyaghata", "Harshana", "Vajra", "Siddhi", "Vyatipata",
               "Variyana", "Parigha", "Shiva", "Siddha", "Sadhya", "Shubha",
               "Shukla", "Brahma", "Indra", "Vaidhriti"]
_KARANA_CHARA = ["Bava", "Balava", "Kaulava", "Taitila", "Gara", "Vanija",
                 "Vishti"]
_KARANA_FIXED = {0: "Kimstughna", 57: "Shakuni", 58: "Chatushpada",
                 59: "Naga"}


def panchang(chart: Chart) -> dict:
    sun = chart.planets["Sun"].lon
    moon = chart.planets["Moon"].lon
    diff = (moon - sun) % 360
    t = int(diff // 12)            # 0..29
    paksha = "Shukla" if t < 15 else "Krishna"
    tithi = _TITHI_NAMES[t % 15] if (t % 15) < 14 else _TITHI_NAMES[13]
    if t % 15 == 14:
        tithi = "Purnima" if t < 15 else "Amavasya"
    half = int(diff // 6)          # 0..59
    karana = _KARANA_FIXED.get(half) or _KARANA_CHARA[(half - 1) % 7]
    yoga = _YOGA_NAMES[int(((sun + moon) % 360) // NAK_LEN)]
    mnak, mpada, mlord, _ = nakshatra_of(moon)
    return {
        "weekday": chart.birth.dt.strftime("%A"),
        "tithi": f"{paksha} {tithi}",
        "nakshatra": f"{mnak} (pada {mpada}, lord {mlord})",
        "yoga": yoga,
        "karana": karana,
        "sun_sign": SIGNS[chart.planets["Sun"].sign],
        "moon_sign": SIGNS[chart.planets["Moon"].sign],
        "ayanamsa": dms(swe.get_ayanamsa_ut(chart.birth.jd)),
    }


# --------------------------------------------------------------------------- #
# Vimshottari dasha (Maha -> Antar -> Pratyantar)
# --------------------------------------------------------------------------- #
@dataclass
class DashaPeriod:
    lord: str
    start: datetime
    end: datetime
    level: int                       # 1 maha, 2 antar, 3 pratyantar
    sub: list = field(default_factory=list)

    def contains(self, when: datetime) -> bool:
        return self.start <= when < self.end


def _rotate(order, lord):
    i = order.index(lord)
    return order[i:] + order[:i]


def _subperiods(lord: str, start: datetime, total_days: float, level: int):
    """Split a period of `lord` into the 9 sub-periods (proportional)."""
    out, t = [], start
    for sub in _rotate(DASHA_ORDER, lord):
        days = total_days * DASHA_YEARS[sub] / 120.0
        out.append(DashaPeriod(sub, t, t + timedelta(days=days), level))
        t = out[-1].end
    return out


def vimshottari(chart: Chart, levels: int = 3, span_years: int = 120):
    """Build the Vimshottari mahadasha list (each with antar/pratyantar)."""
    moon = chart.planets["Moon"].lon
    frac = (moon % NAK_LEN) / NAK_LEN
    _, _, start_lord, _ = nakshatra_of(moon)
    elapsed_days = frac * DASHA_YEARS[start_lord] * YEAR_DAYS
    notional_start = chart.birth.dt - timedelta(days=elapsed_days)

    mds, t, i = [], notional_start, 0
    order = _rotate(DASHA_ORDER, start_lord)
    while (t - notional_start).days < span_years * YEAR_DAYS:
        lord = order[i % 9]
        dur = DASHA_YEARS[lord] * YEAR_DAYS
        md = DashaPeriod(lord, t, t + timedelta(days=dur), 1)
        if levels >= 2:
            md.sub = _subperiods(lord, md.start, dur, 2)
            if levels >= 3:
                for ad in md.sub:
                    ad.sub = _subperiods(
                        ad.lord, ad.start, (ad.end - ad.start).days
                        + (ad.end - ad.start).seconds / 86400, 3)
        mds.append(md)
        t = md.end
        i += 1
    return mds, notional_start


def dasha_balance(chart: Chart) -> str:
    moon = chart.planets["Moon"].lon
    frac = (moon % NAK_LEN) / NAK_LEN
    _, _, lord, _ = nakshatra_of(moon)
    rem_days = (1 - frac) * DASHA_YEARS[lord] * YEAR_DAYS
    y = int(rem_days // YEAR_DAYS)
    rem = rem_days - y * YEAR_DAYS
    mo = int(rem // 30.4375)
    d = int(rem - mo * 30.4375)
    return f"{lord} {y}y {mo}m {d}d"


def current_dasha(chart: Chart, when: datetime | None = None):
    """Return the (maha, antar, pratyantar) periods active at `when`."""
    when = when or datetime.now()
    mds, _ = vimshottari(chart, levels=3)
    maha = next((m for m in mds if m.contains(when)), None)
    if not maha:
        return None, None, None
    antar = next((a for a in maha.sub if a.contains(when)), None)
    prat = next((p for p in (antar.sub if antar else []) if p.contains(when)), None)
    return maha, antar, prat


# --------------------------------------------------------------------------- #
# Transits (gochar) + Sade Sati
# --------------------------------------------------------------------------- #
def transit_positions(when: datetime | None = None) -> dict:
    """Current sidereal positions of all planets (UT = `when` treated as UTC)."""
    when = when or datetime.utcnow()
    jd = swe.julday(when.year, when.month, when.day,
                    when.hour + when.minute / 60 + when.second / 3600)
    out = {}
    for name in PLANETS:
        lon, speed = sidereal_lon(jd, name)
        sign = int(lon // 30)
        nak, pada, nlord, _ = nakshatra_of(lon)
        out[name] = {"lon": lon, "sign": sign, "deg_in_sign": lon - 30 * sign,
                     "nakshatra": nak, "pada": pada,
                     "retrograde": speed < 0 and name not in ("Rahu", "Ketu")}
    return out


def _saturn_sign_on(d: date) -> int:
    jd = swe.julday(d.year, d.month, d.day, 12.0)
    lon, _ = sidereal_lon(jd, "Saturn")
    return int(lon // 30)


def _sign_change_dates(target_sign: int, around: date, forward: bool):
    """Find the date Saturn enters/leaves `target_sign`, scanning daily."""
    step = timedelta(days=1)
    d = around
    # walk to a boundary (limit ~4 years either way)
    for _ in range(1500):
        nxt = d + step if forward else d - step
        if _saturn_sign_on(nxt) != target_sign:
            return nxt if forward else d
        d = nxt
    return None


def sade_sati(chart: Chart, when: datetime | None = None) -> dict:
    """Sade Sati / Panoti status from transit Saturn vs natal Moon sign."""
    when = when or datetime.now()
    moon_sign = chart.planets["Moon"].sign
    today = when.date()
    sat_sign = _saturn_sign_on(today)
    rel = (sat_sign - moon_sign) % 12 + 1     # house of transit Saturn from Moon

    phase = {12: "Sade Sati — Rising phase (Saturn in 12th from Moon)",
             1: "Sade Sati — Peak phase (Saturn over Moon, 1st)",
             2: "Sade Sati — Setting phase (Saturn in 2nd from Moon)",
             4: "Ardha Ashtama / Small Panoti (Kantaka Shani, 4th from Moon)",
             8: "Ashtama Shani / Small Panoti (8th from Moon)"}
    active = rel in (12, 1, 2)
    info = {
        "moon_sign": SIGNS[moon_sign],
        "transit_saturn_sign": SIGNS[sat_sign],
        "house_from_moon": rel,
        "sade_sati_active": active,
        "small_panoti": rel in (4, 8),
        "status": phase.get(rel, f"No Sade Sati (Saturn {rel}th from Moon)"),
        "saturn_entered_sign": None,
        "saturn_leaves_sign": None,
        "saturn_retrograde": transit_positions(when)["Saturn"]["retrograde"],
    }
    entered = _sign_change_dates(sat_sign, today, forward=False)
    leaves = _sign_change_dates(sat_sign, today, forward=True)
    if entered:
        info["saturn_entered_sign"] = entered.isoformat()
    if leaves:
        info["saturn_leaves_sign"] = leaves.isoformat()
    return info


# --------------------------------------------------------------------------- #
# Yogas (curated, each labelled with the rule used)
# --------------------------------------------------------------------------- #
def detect_yogas(chart: Chart) -> list[dict]:
    p = chart.planets
    yogas = []

    def add(name, rule):
        yogas.append({"yoga": name, "basis": rule})

    # Gaja-Kesari: Jupiter in a kendra (1/4/7/10) from the Moon.
    jh = (p["Jupiter"].sign - p["Moon"].sign) % 12 + 1
    if jh in (1, 4, 7, 10):
        add("Gaja-Kesari Yoga",
            f"Jupiter is in the {jh}th (a kendra) from the Moon")

    # Budha-Aditya: Sun and Mercury in the same sign.
    if p["Sun"].sign == p["Mercury"].sign:
        add("Budha-Aditya Yoga",
            f"Sun and Mercury conjoin in {SIGNS[p['Sun'].sign]}")

    # Chandra-Mangala: Moon and Mars conjunct.
    if p["Moon"].sign == p["Mars"].sign:
        add("Chandra-Mangala Yoga",
            f"Moon and Mars conjoin in {SIGNS[p['Moon'].sign]}")

    # Pancha Mahapurusha: Mars/Mercury/Jupiter/Venus/Saturn in own or
    # exaltation sign AND in a kendra from the lagna.
    maha = {"Mars": "Ruchaka", "Mercury": "Bhadra", "Jupiter": "Hamsa",
            "Venus": "Malavya", "Saturn": "Sasa"}
    for planet, yname in maha.items():
        pp = p[planet]
        if pp.house in (1, 4, 7, 10) and pp.dignity in ("Own", "Exalted"):
            add(f"{yname} Yoga (Pancha Mahapurusha)",
                f"{planet} is {pp.dignity} in {pp.sign_name} and in the "
                f"{pp.house}th (a kendra) from lagna")

    # Kemadruma (affliction): no planet (besides Sun) in 2nd or 12th from Moon,
    # and no planet conjunct the Moon (excluding nodes).
    occ = {pp.sign for nm, pp in p.items() if nm not in ("Sun", "Rahu", "Ketu")}
    m = p["Moon"].sign
    if not ({(m + 1) % 12, (m - 1) % 12} & (occ - {m})) and \
            not any(pp.sign == m for nm, pp in p.items()
                    if nm not in ("Moon", "Sun", "Rahu", "Ketu")):
        add("Kemadruma Yoga (affliction)",
            "no planets flank or join the Moon (2nd/12th/with Moon empty)")

    # Kendra-Trikona Raj Yoga: a kendra lord and a trikona lord conjoin.
    kendra_lords = {chart.lord_of_house(h) for h in (1, 4, 7, 10)}
    trikona_lords = {chart.lord_of_house(h) for h in (1, 5, 9)}
    for a in kendra_lords:
        for b in trikona_lords:
            if a != b and p[a].sign == p[b].sign:
                add("Raj Yoga (Kendra–Trikona)",
                    f"kendra lord {a} conjoins trikona lord {b} in "
                    f"{SIGNS[p[a].sign]}")

    # Neecha-Bhanga (basic): a debilitated planet whose dispositor or the lord
    # of its exaltation sign is in a kendra from lagna.
    for nm, pp in p.items():
        if pp.dignity == "Debilitated":
            disp = RULER[pp.sign]
            if p[disp].house in (1, 4, 7, 10):
                add("Neecha-Bhanga Raja Yoga (partial)",
                    f"debilitated {nm}'s dispositor {disp} sits in a kendra")
    return yogas
