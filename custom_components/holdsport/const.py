"""Konstanter for Holdsport."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "holdsport"
API_BASE = "https://api.holdsport.dk"

CONF_PROFILES = "profiles"
# Beregnet mødetid før kampe (når Holdsport ikke selv har en mødetid)
CONF_MATCH_MEETING_MINUTES = "match_meeting_minutes"
CONF_MATCH_MEETING_PLACES = "match_meeting_places"
DEFAULT_MATCH_MEETING_MINUTES = 0  # 0 = slået fra

UPDATE_INTERVAL = timedelta(minutes=15)
ACTIVITIES_PER_PAGE = 50
CALENDAR_MAX_PAGES = 10

# event_type_id fra API'et (verificeret mod demo-kontoen)
EVENT_TYPE_MATCH = 1  # Kamp
EVENT_TYPE_TRAINING = 2  # Træning
EVENT_TYPE_TOURNAMENT = 4  # Stævne
EVENT_TYPE_MEMBER_ACTIVITY = 9  # Medlemsaktivitet

# Brugerens status på en aktivitet (felterne status / status_code)
STATUS_NONE = 0
STATUS_ATTENDING = 1
STATUS_DECLINED = 2
STATUS_AVAILABLE = 3  # "Til rådighed" – udledt af web-appens statusliste
STATUS_SELECTED = 4
STATUS_UNKNOWN = 5

# Tæller med i "antal tilmeldte" på kortet
ATTENDING_STATUSES = (STATUS_ATTENDING, STATUS_SELECTED)

STATUS_TEXT = {
    STATUS_NONE: "Ikke svaret",
    STATUS_ATTENDING: "Tilmeldt",
    STATUS_DECLINED: "Afmeldt",
    STATUS_AVAILABLE: "Til rådighed",
    STATUS_SELECTED: "Udvalgt",
    STATUS_UNKNOWN: "Ukendt",
}

# Ældre API-svar (README) bruger tekst i stedet for tal
STATUS_FROM_TEXT = {
    "ej tilkendegivet": STATUS_NONE,
    "tilmeldt": STATUS_ATTENDING,
    "afmeldt": STATUS_DECLINED,
    "til rådighed": STATUS_AVAILABLE,
    "udvalgt": STATUS_SELECTED,
    "ukendt": STATUS_UNKNOWN,
}

# joined_status der sendes ved tilmelding/afmelding.
# 1 = tilmeld er dokumenteret. 2 = afmeld svarer til status_code 2 ("Afmeldt").
JOINED_ATTEND = 1
JOINED_DECLINE = 2

CARD_URL_BASE = "/holdsport_static"
CARD_URL = f"{CARD_URL_BASE}/holdsport-card.js"

SERVICE_ATTEND = "attend"
SERVICE_DECLINE = "decline"
SERVICE_TAKE_TASK = "take_task"
ATTR_TASK_ID = "task_id"
ATTR_DEVICE_ID = "device_id"
ATTR_ACTIVITY_ID = "activity_id"


def device_identifier(account_id: int, profile_id: int) -> tuple[str, str]:
    """Enheds-id for en profil under en given Holdsport-konto."""
    return (DOMAIN, f"{account_id}_{profile_id}")
