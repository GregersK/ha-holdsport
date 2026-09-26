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
  translations/    da.json + en.json (ingen strings.json – custom integration)
tests/             pytest-homeassistant-custom-component, FakeClient mocker API'et
```

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
