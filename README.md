# Kundali — Vedic Astrology MCP server

An MCP server that exposes a person's Vedic (Jyotish) horoscope as **raw,
structured chart data** — so an LLM can read the chart and do the interpretation
itself, advising on the present situation.

Everything is computed **locally with Swiss Ephemeris** (`jyotish.py`): no
scraping, works offline. The only network call is optional place geocoding
(OpenStreetMap). Calculations use the **sidereal zodiac, Lahiri ayanamsa, mean
lunar node, and whole-sign houses** — validated against a reference chart to
within arc-minutes.

> Design note: the tools return *facts* (positions, dignities, houses, dasha
> periods, transits, yogas), not predictions. The **LLM supplies the
> interpretation** — which makes readings conversational and tailored.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python mcp_server.py        # stdio transport
```

### Register with Claude Code
```bash
claude mcp add kundali -- /path/to/kundali-app/.venv/bin/python \
  /path/to/kundali-app/mcp_server.py
```

### Register with Claude Desktop
`~/Library/Application Support/Claude/claude_desktop_config.json` (absolute paths):
```json
{
  "mcpServers": {
    "kundali": {
      "command": "/path/to/kundali-app/.venv/bin/python",
      "args": ["/path/to/kundali-app/mcp_server.py"]
    }
  }
}
```

## Tools
| Tool | Returns (raw data) |
|---|---|
| `geocode_place` | City → candidate lat/lon + timezone (to confirm an ambiguous birthplace) |
| `get_birth_chart` | Ascendant, panchang, dasha balance, and a per-planet table: sign, exact degree, nakshatra+pada, retrograde, whole-sign house, dignity, aspected houses, + house-occupancy map |
| `get_dashas` | Vimshottari **Maha → Antar → Pratyantar** tree, lifetime Mahadasha timeline, with the periods running on `as_of` flagged |
| `get_sade_sati` | Transit Saturn vs natal Moon: house, Sade Sati/small-Panoti status, phase (Rising/Peak/Setting), Saturn's sign ingress/egress dates |
| `get_current_transits` | Each planet's transiting sign/degree/nakshatra/retro, with house from natal lagna and from natal Moon |
| `get_yogas` | Detected yogas with the placement rule that triggered each |

### Birth fields (same for every chart tool)
- `name` — person's name
- `date` — `YYYY-MM-DD`
- `time` — 24h local clock, `HH:MM` or `HH:MM:SS`
- `place` — birth city/town (geocoded if lat/lon omitted)
- `sex` — `male` / `female`
- `latitude` / `longitude` — optional decimal degrees (+N/+E) to override geocoding
- `tz_offset` — optional base (non-DST) UTC offset in hours, e.g. `5.5` for IST

`get_dashas`, `get_sade_sati`, `get_current_transits` also accept optional
`as_of` (`YYYY-MM-DD`) to evaluate a past or future date (defaults to today).

### Example prompt
> "I was born on 1990-01-15 at 14:30 in Mumbai. Which dasha am I in, am I under
> Sade Sati, and what does my chart suggest about my career right now?"

The model calls `get_birth_chart`, `get_dashas`, `get_sade_sati`,
`get_current_transits`, then interprets and advises.

> These are traditional astrological interpretations, not deterministic
> predictions — the server's instructions tell the model to frame them as such.

## Yoga coverage
Curated, each labelled with its rule: Gaja-Kesari, Budha-Aditya,
Chandra-Mangala, the five Pancha Mahapurusha (Ruchaka/Bhadra/Hamsa/Malavya/
Sasa), Kemadruma, Kendra–Trikona Raj Yoga, Neecha-Bhanga (partial). Not
exhaustive — absence isn't proof of absence.

## Validation
Validated against an independent Vedic reference for a sample birth chart: every
planet's sign/nakshatra/pada/retrograde/dignity/house matches; ascendant within
~20″, dasha balance within a few days, Sade Sati dates within a day, panchang
exact.

## Architecture
```
mcp_server.py   FastMCP tools + presentation (formats jyotish output)
jyotish.py      Swiss Ephemeris engine: positions, chart, panchang, dasha,
                sade sati, transits, yogas  (pure computation, no network)
geo.py          place -> coordinates + timezone (OpenStreetMap / timezonefinder)
```
