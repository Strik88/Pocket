"""Tiny NL/EN string table for the files Pocket Bridge writes."""

STRINGS = {
    "date": {"nl": "Datum", "en": "Date"},
    "duration": {"nl": "Duur", "en": "Duration"},
    "client": {"nl": "Klant", "en": "Client"},
    "project": {"nl": "Project", "en": "Project"},
    "meeting": {"nl": "Afspraak", "en": "Meeting"},
    "attendees": {"nl": "Deelnemers", "en": "Attendees"},
    "unsorted": {"nl": "Ongesorteerd", "en": "Unsorted"},
    "summary": {"nl": "Samenvatting", "en": "Summary"},
    "action_items": {"nl": "Actiepunten", "en": "Action items"},
    "transcript": {"nl": "Transcript", "en": "Transcript"},
    "no_transcript": {"nl": "Nog geen transcript beschikbaar.", "en": "No transcript available yet."},
    "dossier": {"nl": "Klantdossier", "en": "Client dossier"},
    "dossier_note": {
        "nl": "Dit bestand wordt automatisch bijgewerkt door Pocket Bridge. Eigen aantekeningen kun je kwijt onder 'Notities'; die blijven bewaard.",
        "en": "This file is updated automatically by Pocket Bridge. Put your own notes under 'Notes'; they are preserved.",
    },
    "conversations": {"nl": "Gesprekken", "en": "Conversations"},
    "last_contact": {"nl": "Laatste contact", "en": "Last contact"},
    "open_actions": {"nl": "Openstaande actiepunten", "en": "Open action items"},
    "all_conversations": {"nl": "Alle gesprekken", "en": "All conversations"},
    "notes": {"nl": "Notities", "en": "Notes"},
    "none": {"nl": "Geen.", "en": "None."},
    "status": {"nl": "Stand van zaken", "en": "Current status"},
    "status_note": {"nl": "Bijgehouden door Claude, laatst bijgewerkt {date}.", "en": "Maintained by Claude, last updated {date}."},
    "general": {"nl": "Algemeen", "en": "General"},
    "briefing": {"nl": "Briefing", "en": "Briefing"},
    "followup": {"nl": "Follow-up", "en": "Follow-up"},
    "week": {"nl": "Week", "en": "Week"},
    "weekly_title": {"nl": "Weekoverzicht {week}", "en": "Weekly overview {week}"},
    "weekly_review": {"nl": "Terugblik", "en": "Review"},
    "weekly_empty": {"nl": "Geen gesprekken deze week.", "en": "No conversations this week."},
    "weekly_dir": {"nl": "_Weekoverzichten", "en": "_Weekly"},
    "briefings_dir": {"nl": "_Briefings", "en": "_Briefings"},
    "followups_dir": {"nl": "_Follow-ups", "en": "_Follow-ups"},
    "decisions_actions": {"nl": "Actiepunten", "en": "Action items"},
    "sync_fetching": {"nl": "Opnames ophalen uit Pocket…", "en": "Fetching recordings from Pocket…"},
    "sync_found": {"nl": "{n} opnames gevonden", "en": "{n} recordings found"},
    "sync_done": {
        "nl": "Klaar: {new} nieuw, {updated} bijgewerkt, {pending} nog in verwerking bij Pocket.",
        "en": "Done: {new} new, {updated} updated, {pending} still processing in Pocket.",
    },
    "sync_no_key": {"nl": "Nog geen Pocket API-key ingesteld.", "en": "No Pocket API key set yet."},
    "semantic_indexed": {"nl": "Zoeken op betekenis: {n} stukken tekst geïndexeerd", "en": "Search on meaning: indexed {n} text chunks"},
    "sync_busy": {"nl": "Er loopt al een synchronisatie.", "en": "A sync is already running."},
}


def t(lang: str, key: str, **kwargs: object) -> str:
    entry = STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en") or key
    return text.format(**kwargs) if kwargs else text
