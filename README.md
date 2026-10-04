# Pocket Bridge

**Haal je Pocket-opnames naar je eigen computer, automatisch geordend per klant, en praat erover met Claude.**

[English version → README.en.md](README.en.md)

Pocket Bridge is een klein programma dat:

1. **inlogt op Pocket** via de officiële Pocket API (met jouw API-key);
2. **alle transcripten ophaalt** en als nette Markdown-bestanden opslaat in een map op jouw computer;
3. **per klant een map maakt** en elke opname automatisch in de juiste map zet: eerst op basis van jouw regels (trefwoorden, Pocket-tags), en optioneel met Claude als de regels niets vinden;
4. **per klant een dossier bijhoudt** (`_Dossier.md`) met alle gesprekken, laatste contact en openstaande actiepunten;
5. **Claude toegang geeft** tot al je transcripten via een lokale MCP-server, zodat je in Claude Desktop of Claude Code kunt vragen: *"Wat hebben we vorige maand met Acme afgesproken?"*

Alles draait lokaal. Je transcripten en sleutels blijven op je eigen computer.

![Overzicht](docs/img/dashboard.png)

---

## Snel starten

### 1. Downloaden

Klik op GitHub op de groene knop **Code → Download ZIP** en pak de ZIP uit. Zet de map op een vaste plek, bijvoorbeeld in je thuismap (`~/PocketBridge` op Mac of `C:\Users\<jij>\PocketBridge` op Windows). **Niet in Downloads laten staan**: Claude Desktop start het programma later vanuit deze map.

Of met git:

```bash
git clone https://github.com/strik88/pocket.git PocketBridge
```

### 2. Starten

| Mac | Windows |
|---|---|
| Dubbelklik op **`start-mac.command`** | Dubbelklik op **`start-windows.bat`** |

De eerste keer installeert het script automatisch [uv](https://docs.astral.sh/uv/) (dat regelt Python voor je) en de benodigde onderdelen. Dat duurt ongeveer een minuut. Daarna opent je browser vanzelf op **http://127.0.0.1:8765**.

> **Mac: "kan niet worden geopend"?** Klik met de rechtermuisknop op `start-mac.command` → **Open** → **Open**. Lukt het nog steeds niet, open dan Terminal in de map en typ `chmod +x start-mac.command`.
>
> **Windows: SmartScreen-melding?** Klik op **Meer informatie → Toch uitvoeren**.

### 3. De installatiehulp volgen

De webpagina leidt je door vijf stappen:

1. **Pocket koppelen.** In Pocket: *Settings → Developer → API Keys*, maak een key aan (begint met `pk_`), plak hem en klik op *Test & opslaan*.
2. **Map kiezen** waar je transcripten komen. Standaard: `Documenten/Pocket Transcripten`.
3. **Klanten toevoegen** met herkenningswoorden (bedrijfsnaam, contactpersonen, projectnamen).
4. **Optioneel: Anthropic API-key** voor slim sorteren en vragen stellen in de app zelf.
5. **Koppelen aan Claude Desktop** met één klik. Herstart daarna Claude Desktop.

Klik op **Nu synchroniseren** en je opnames verschijnen.

---

## Hoe het eruitziet op je schijf

```
Pocket Transcripten/
├── Klanten/
│   ├── Acme/
│   │   ├── _Dossier.md                       ← overzicht, wordt automatisch bijgewerkt
│   │   └── 2026/
│   │       ├── 2026-09-01 0930 Kickoff Acme.md
│   │       └── 2026-09-14 1400 Voortgang Q4.md
│   └── Betafabriek/
│       └── ...
├── _Ongesorteerd/                            ← opnames zonder duidelijke klant
│   └── 2026/...
└── .pocket-bridge/                           ← index en cache (mag je negeren)
```

Elk bestand bevat bovenaan metadata (datum, duur, klant, tags), daarna de **samenvatting**, de **actiepunten** als afvinkbare lijst, en het volledige **transcript** met sprekers en tijden. De bestanden werken in elke teksteditor en zijn ook direct bruikbaar als [Obsidian](https://obsidian.md)-vault: de klantnamen zijn links (`[[Acme]]`).

**Zelf herindelen mag gewoon.** Sleep een bestand in Finder of Verkenner naar een andere klantmap. Pocket Bridge ziet dat de map de klant bepaalt en houdt er rekening mee bij volgende updates. Je kunt een opname ook verplaatsen via de webpagina (*Opnames → Verplaats naar*) of door het aan Claude te vragen.

In `_Dossier.md` kun je onderaan onder **Notities** je eigen aantekeningen kwijt. Die blijven bewaard als het dossier wordt bijgewerkt.

---

## Praten met je transcripten in Claude

Na het koppelen (stap 5) heeft Claude Desktop de tools van **pocket-transcripts**. Voorbeelden van wat je kunt vragen:

- *"Welke klanten heb ik en wanneer sprak ik ze voor het laatst?"*
- *"Zoek in mijn gesprekken waar het over de begroting van 2027 ging."*
- *"Geef me alle openstaande actiepunten voor Acme."*
- *"Vat de laatste drie gesprekken met Betafabriek samen en stel een follow-upmail op."*
- *"Het gesprek 'Losse gedachten' hoort bij Acme, zet het daar neer."*
- *"Haal de nieuwste opnames op uit Pocket."*

| Tool | Wat het doet |
|---|---|
| `list_clients` | Alle klanten met aantal gesprekken, laatste contact en open actiepunten |
| `list_recordings` | Opnames per klant of periode |
| `search_transcripts` | Zoeken in titels, samenvattingen en transcripten (ook `"exacte zinnen"`) |
| `get_transcript` | Het volledige gesprek |
| `get_client_dossier` | Het klantdossier |
| `open_action_items` | Alle niet-afgevinkte actiepunten |
| `assign_recording` | Opname naar een (nieuwe) klant verplaatsen |
| `add_client` | Klant toevoegen of trefwoorden aanvullen |
| `sync_now` | Direct nieuwe opnames ophalen |
| `status` | Controleren of alles gekoppeld is |

Tip: in [docs/claude-instructies.md](docs/claude-instructies.md) staan instructies die je in een Claude-project kunt plakken, zodat Claude deze tools op een vaste manier gebruikt.

**Claude Code** (terminal): het exacte commando staat in de installatiehulp, onder stap 5. Het ziet er zo uit:

```bash
claude mcp add pocket-transcripts --scope user -- /pad/naar/PocketBridge/.venv/bin/python -m pocket_bridge mcp
```

**Ook de officiële Pocket MCP?** Pocket heeft een eigen MCP-server (`https://public.heypocketai.com/mcp`) die live in je Pocket-account zoekt. Die kun je naast Pocket Bridge gebruiken: in Claude via *Instellingen → Connectors → Custom connector toevoegen*. Pocket Bridge werkt ook zonder Pocket Pro en offline, en is de enige van de twee die je bestanden lokaal en per klant ordent.

---

## Automatisch synchroniseren

Standaard haalt Pocket Bridge elke 15 minuten nieuwe opnames op, zolang **de webapp óf Claude Desktop** openstaat (Claude Desktop start de MCP-server, en die synchroniseert ook). Opnames die Pocket nog aan het verwerken is, worden de volgende keer meegenomen. Je past het interval aan onder *Instellingen*.

Liever vanaf de terminal of via een geplande taak?

```bash
.venv/bin/python -m pocket_bridge sync          # nieuwe opnames
.venv/bin/python -m pocket_bridge sync --full   # alles opnieuw controleren
```

---

## Hoe het sorteren werkt

Voor elke **nieuwe** opname, in deze volgorde:

1. **Pocket-tag**: heeft de opname een tag die je bij een klant hebt ingevuld? Dan die klant.
2. **Titel**: staat de klantnaam of een trefwoord in de titel? Dan die klant.
3. **Inhoud**: komen de trefwoorden van één klant minstens 2× voor in samenvatting en transcript (en duidelijk vaker dan die van andere klanten)? Dan die klant. Het minimum stel je in.
4. **Claude** (alleen met Anthropic API-key en als het aanstaat): Claude leest het gesprek en kiest een bestaande klant, maar alleen als het zeker genoeg is. Optioneel mag Claude ook nieuwe klanten aanmaken.
5. Anders: **`_Ongesorteerd`**.

Bestaande opnames worden nooit automatisch verplaatst; als jij iets verplaatst, blijft het daar.

---

## Privacy en kosten

- Je Pocket- en Anthropic-keys staan alleen in je gebruikersmap (`~/.pocket-bridge/config.json` op Mac/Linux, `%APPDATA%\PocketBridge\config.json` op Windows), nooit in deze map of op GitHub.
- De webpagina is alleen bereikbaar vanaf je eigen computer (127.0.0.1).
- Zonder Anthropic API-key gaat er niets naar Anthropic vanuit Pocket Bridge. Gebruik je Claude Desktop, dan leest Claude de transcripten die het via de tools opvraagt, net als elk ander document dat je deelt.
- Met Anthropic API-key: sorteren met Claude kost per opname een paar cent; vragen in de app afhankelijk van hoeveel gesprekken worden meegelezen. Het model is instelbaar (standaard `claude-opus-5-5`).

---

## Problemen oplossen

| Probleem | Oplossing |
|---|---|
| *"Pocket weigert de API-key"* | Maak een nieuwe key aan in Pocket en plak hem opnieuw. Let op spaties. |
| Claude Desktop ziet de tools niet | Herstart Claude Desktop volledig (Mac: Cmd+Q). Controleer onder *Instellingen → Developer* of `pocket-transcripts` draait. |
| Map verplaatst na koppelen | Open de app en klik opnieuw op *Koppel Claude Desktop*; het pad naar het programma is veranderd. |
| Opname staat er niet | Pocket is mogelijk nog aan het verwerken. Wacht even en synchroniseer opnieuw, of kies *Alles opnieuw controleren*. |
| Bestanden handmatig verplaatst of hernoemd | *Instellingen → Index & dossiers opnieuw opbouwen*. |
| Iets ziet er vreemd uit in een transcript | De ruwe Pocket-data staat in `.pocket-bridge/raw/<id>.json`. Handig om mee te sturen bij een bugmelding. |

---

## Delen met anderen

Stuur iemand de link naar deze GitHub-repository. Ze downloaden de ZIP, dubbelklikken op het startscript en volgen de installatiehulp met hun eigen Pocket-key. Er zitten geen persoonlijke gegevens in de code.

## Voor ontwikkelaars

```bash
uv sync --extra dev
uv run pytest
uv run pocket-bridge            # webapp
uv run pocket-bridge mcp        # MCP-server (stdio)
```

Opbouw: `pocket_api.py` (Pocket-client, tolerant voor variaties in de API), `sync.py` (ophalen en wegschrijven), `classify.py` + `ai.py` (sorteren, Claude), `storage.py` (Markdown), `index.py` (SQLite-zoekindex), `dossier.py`, `mcp_server.py`, `web/` (FastAPI + statische pagina, geen build-stap). Ideeën voor uitbreidingen staan in [docs/ideeen.md](docs/ideeen.md).

Pocket Bridge is een onafhankelijk project en niet verbonden aan Pocket of Anthropic. Licentie: MIT.
