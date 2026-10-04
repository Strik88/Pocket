# Ideeën voor uitbreidingen

## Gebouwd in versie 0.2

- Voorbereidingsbriefing per klant (Claude)
- Follow-up-mail na een gesprek, te openen in je mailprogramma
- Weekoverzicht, automatisch elke vrijdagmiddag, met terugblik van Claude
- Centrale actiepuntenlijst met afvinken (ook terug vanuit het dossier)
- "Stand van zaken" per klant in het dossier, bijgehouden door Claude
- Trefwoorden voorstellen na het handmatig verplaatsen van een opname
- Agenda-koppeling via een geheime iCal-link (Google Agenda, Outlook), met e-maildomeinen per klant
- Projecten binnen klanten, met eigen submappen
- Sprekers een naam geven, handmatig of door Claude, en onthouden bij updates
- Zoeken op betekenis met een lokaal meertalig taalmodel
- Antwoorden die live verschijnen, met bronverwijzingen per zin
- Icoon in menubalk/systeemvak en automatisch starten bij inloggen

## Bewust (nog) niet gebouwd

- **Koppeling met een takenapp** (Todoist, Microsoft To Do, Notion). Gekozen voor een centrale lijst in de app zelf. Todoist is het makkelijkst toe te voegen (alleen een API-token).
- **Agenda via inloggen met Google/Microsoft** in plaats van een iCal-link. Netter, maar vereist dat elke gebruiker een eigen Google Cloud- en Azure-app aanmaakt.
- **Echte installers** (.dmg / .exe). Zonder betaald ontwikkelaarscertificaat geven Mac en Windows bij het openen een waarschuwing; de startscripts werken even makkelijk.
- **Pocket-webhooks** voor opnames binnen seconden. Vereist een publiek bereikbaar adres; synchroniseren elke paar minuten is in de praktijk bijna even snel.
- **Export naar Word/PDF** van dossiers en briefings.
- **Teamversie**: meerdere Pocket-accounts in één gedeelde map.

## Verdere ideeën

- Briefing automatisch klaarzetten de ochtend vóór een afspraak (de agenda is al gekoppeld).
- Follow-up-mail direct als concept in Gmail of Outlook zetten.
- Per klant een vaste lijst "mensen" bijhouden, zodat sprekers vanzelf herkend worden.
- Signaleren als een klant al lang niet gesproken is, of als actiepunten blijven liggen.
