# Ideeën voor uitbreidingen

Wat er in versie 0.1 zit: lokale sync, mappen en dossiers per klant, sorteren met regels en Claude, zoeken, vragen stellen in de app, MCP-server voor Claude Desktop/Code, automatisch synchroniseren, NL/EN.

Mogelijke volgende stappen, grofweg van meeste naar minste waarde per moeite:

## Werkstroom
- **Voorbereidingsbriefing per klant**: één knop die vóór een afspraak een briefing maakt uit het dossier en de laatste gesprekken.
- **Follow-up-mail na elk gesprek**: automatisch een conceptmail met samenvatting en afspraken, klaar om te versturen (bijv. als concept in Gmail).
- **Weekoverzicht**: elke vrijdag een Markdown-bestand met alle gesprekken van de week per klant, beslissingen en actiepunten.
- **Actiepunten synchroniseren** met een takenlijst (Todoist, Things, Microsoft To Do, Notion) en afvinken in beide richtingen.
- **AI-samenvatting in het dossier**: "stand van zaken" per klant die Claude bij elk nieuw gesprek bijwerkt.

## Sorteren
- **Leren van verplaatsingen**: als je een opname handmatig verplaatst, stelt het systeem voor om trefwoorden aan die klant toe te voegen.
- **Agenda-koppeling**: klant bepalen aan de hand van de agenda-afspraak op het moment van opnemen (Google/Outlook), inclusief deelnemers.
- **Projecten binnen klanten**: submappen per project of traject.
- **Sprekers herkennen**: "Spreker 1" vervangen door echte namen per klant.

## Zoeken en vragen
- **Semantisch zoeken** (op betekenis in plaats van woorden) met lokale embeddings.
- **Antwoorden streamen** in de app, zodat je het antwoord ziet verschijnen.
- **Bronverwijzingen op zinsniveau** met de citations-functie van de Claude API.

## Delen en installeren
- **Echte installers** (.dmg / .exe) en een icoon in de menubalk/systeemvak met een "synchroniseren"-knop.
- **Automatisch starten bij inloggen**, zodat sync ook draait zonder dat Claude of de app open is.
- **Pocket-webhooks** (`summary.completed`) zodat opnames binnen seconden binnenkomen; vereist een publiek bereikbare URL (bijv. via een kleine cloudfunctie).
- **Export** van een klantdossier naar PDF of Word voor de klant zelf.
- **Teamversie**: gedeelde map (SharePoint, Google Drive) met meerdere Pocket-accounts.
