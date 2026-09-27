# CLAUDE.md

Custom Home Assistant-integration for Holdsport (dansk klub-/holdsportssystem).
Viser træning, kampe m.m. pr. familiemedlem og kan tilmelde/afmelde.

## Struktur

```
custom_components/holdsport/
  __init__.py      setup, runtime_data, services (attend/decline), oprydning af fravalgte enheder
  api.py           aiohttp-klient, basic auth, fejlklasser (Auth/NotFound/Rejected)
  coordinator.py   Profile/Activity/ProfileData, parse_activity, polling, fetch_range, respond
  entity.py        base-entity: én enhed pr. profil
  calendar.py      kalender pr. profil (historik via async_fetch_range)
  sensor.py        next_activity / next_training / next_match (timestamp) + unanswered
  config_flow.py   login -> vælg profiler; reauth; options (til/fravalg af profiler)
  const.py         konstanter, status-/eventtype-koder, device_identifier()
  websocket.py     holdsport/subscribe: skubber en profils aktiviteter til kortet
  frontend/holdsport-card.js  dashboard-kort (vanilla web component, ingen build-step)
  translations/    da.json + en.json (ingen strings.json – custom integration)
tests/             pytest-homeassistant-custom-component, FakeClient mocker API'et
.github/workflows/tests.yml  kører pytest på Linux ved hvert push
```

## Dashboard-kortet

- Serveres af `HoldsportCardView` (card.py) på den faste URL `/holdsport_static/holdsport-card.js`
  med `Cache-Control: no-cache` + ETag. **Ingen version i URL'en**: med `?v=` importerede en
  browser med en gammel side den gamle URL, og da et custom element kun kan defineres én gang,
  vandt den gamle kode (sås i 0.6.0: ingen deltagerliste før hård genindlæsning).
  Indlæses via `add_extra_js_url`, som Lovelace-resource og som panelets `module_url`.
- Samme modul definerer `holdsport-panel`, registreret med `panel_custom` som sidemenu-punktet
  "Holdsport" (`/holdsport`). Panelet finder profilerne via `hass.entities` (platform holdsport,
  domæne calendar) og viser et `holdsport-card` pr. profil.
- Registreringen springes over, når `hass.http`/`frontend` ikke er sat op (tests).
- Kortet registreres også som Lovelace-resource (`_async_ensure_lovelace_resource`, storage-mode),
  fordi `add_extra_js_url` alene ikke nåede at definere kortet før dashboard-editoren gav op
  ("Custom element doesn't exist"). Posten opdateres til ny `?v=` ved hver version.
- Opgaver (`GET/POST /v1/activities/{id}/activity_tasks`) er ikke med i aktivitetslisten; kortet henter
  dem via `holdsport/tasks`, når en aktivitet foldes ud. Profil-id = `user_id` i `activity_tasks`.
  API'et har ingen afmelding fra en opgave.
- Kortet abonnerer med `entity_id` (typisk kalenderen) → entity registry → device → `resolve_device()`.
  Til/afmelding går gennem de almindelige services, så validering og fejltekster er ét sted.
- Tekst fra Holdsport (navne, steder, beskrivelser) skal gennem `esc()` før den sættes i innerHTML.
- Tider formateres altid med `da-DK` (kortets tekster er danske), uanset HA-brugerens sprog.
- Mødetid: options `match_meeting_minutes`/`match_meeting_places` → `match_meeting_start()` i
  coordinatoren sætter `Activity.meeting_start` for kampe uden `pickup_time`. Vises i kort og kalender.
- Kan ikke unit-testes her (ingen node); afprøv i en browser med en stub af `hass.connection`.

## Holdsport API – verificerede fakta

Dokumentation: https://github.com/Holdsport/holdsport-api (README + openapi.yml).
Demo-login `demo:demo` virker mod `https://api.holdsport.dk/v1/` (read-only brug – det er en delt konto).

- Auth: HTTP basic. Forkert login = 401.
- `GET /v1/profiles`: første element er kontoen selv, resten er administrerede profiler.
  Administreret profil: login = profil-id, password = hovedkontoens.
- `GET /v1/teams/{id}/activities?date=YYYY-MM-DD&page=&per_page=`: fra dato og frem (default i dag, 20/side).
- Felter ud over README: `event_type`, `event_type_id`, `status` (tal), `registration_type`, `activities_users[].status_code`.
- `event_type_id`: 1 Kamp, 2 Træning, 4 Stævne, 9 Medlemsaktivitet (kan være null).
- `status`: 0 ikke svaret, 1 Tilmeldt, 2 Afmeldt, 4 Udvalgt, 5 Ukendt. README viser ældre tekstværdier – `parse_status` håndterer begge.
- Svar: send `action_method` til `action_path` med `{"activities_user": {"joined_status": N, "picked": 1}}`.
  POST når der ikke er svaret, PUT når der er. `action_method: GET` = betalingsaktivitet med web-URL → kan ikke besvares.
- Ændring af fortidig aktivitet giver 422.
- `actions`-arrayet er i praksis tomt – brug det ikke til at afgøre mulige handlinger.
- Aktiviteter kl. 00:00 uden `endtime` behandles som heldag.
- `comments[]` er tom på rigtige konti (kun demo-kontoens gamle data har indhold). Holdsports chat
  findes ikke i API'et – kun i web-appens interne API, som vi ikke bruger. Beskeder blev fjernet i 0.4.0.
- `max_attendees` 999 = ingen grænse.
- `status` 3 = "Til rådighed" (udledt af web-appens statusliste, ikke bekræftet i API-docs). 5 = "Ukendt".
- `activities_users[].status_code` bruges til antal tilmeldte (1 Tilmeldt + 4 Udvalgt).

## Ikke verificeret

- `JOINED_DECLINE = 2` (afmelding) er udledt af status_code 2 = "Afmeldt". Demo-kontoen har ingen fremtidige
  aktiviteter, så det er ikke testet live. Skal bekræftes på en rigtig aktivitet.

## Konventioner

- Kodekommentarer og brugerrettede fejlbeskeder på dansk; entity-/service-tekster via translations.
- Unique ids: `{account_id}_{profile_id}_{key}`; device identifier `(holdsport, "{account_id}_{profile_id}")`.
- Services registreres i `async_setup` og slår profil op via device_id → config entry → runtime_data.
- Ingen eksterne requirements; brug HA's aiohttp-session.
- Minimum HA: 2025.3 (AddConfigEntryEntitiesCallback, OptionsFlow uden __init__, data_updates i reauth).

## Test

```bash
uv venv -p 3.13 venv && . venv/bin/activate
uv pip install pytest-homeassistant-custom-component
python -m pytest tests -q
```

Testet grønt mod HA 2026.2.3.
