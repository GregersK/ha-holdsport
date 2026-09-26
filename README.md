# Holdsport til Home Assistant

Custom integration der viser træning, kampe og andre aktiviteter fra [Holdsport](https://holdsport.dk) for hele familien – og kan tilmelde/afmelde.

## Funktioner

- **Én enhed pr. familiemedlem.** Log ind med forælderens konto og vælg de profiler (børn) du administrerer. Voksne med egen konto tilføjes som en ekstra integration.
- **Kalender pr. person** (`calendar.<navn>`) med alle aktiviteter på tværs af hold – også historik, når man bladrer tilbage.
- **Sensorer pr. person:** Næste aktivitet, Næste træning, Næste kamp (timestamp + attributter som hold, sted, mødetid, status og `activity_id`) samt **Mangler svar** (antal kommende aktiviteter uden svar).
- **Handlinger:** `holdsport.attend` (tilmeld) og `holdsport.decline` (afmeld).

## Installation

HACS → Custom repositories → tilføj repo'et som *Integration*, eller kopiér `custom_components/holdsport` til `/config/custom_components/`. Genstart og tilføj *Holdsport* under Enheder og tjenester.

Familiemedlemmer kan til- og fravælges senere under integrationens **Konfigurer**.

## Eksempler

Tilmeld til næste træning (script, der kan lægges på en knap):

```yaml
script:
  emma_tilmeld_traening:
    alias: Tilmeld Emma til næste træning
    sequence:
      - action: holdsport.attend
        data:
          device_id: <Emmas enheds-id>   # vælges nemmest i UI-editoren
          activity_id: "{{ state_attr('sensor.emma_naeste_traening', 'activity_id') }}"
```

Påmindelse når der mangler svar:

```yaml
automation:
  - alias: Holdsport mangler svar
    triggers:
      - trigger: numeric_state
        entity_id: sensor.emma_mangler_svar
        above: 0
    actions:
      - action: notify.mobile_app_telefon
        data:
          message: >
            Emma mangler at svare på:
            {{ state_attr('sensor.emma_mangler_svar', 'activities') | map(attribute='name') | join(', ') }}
```

Entity-id'erne afhænger af profilnavn og sprog – tjek dem under enheden.

## Noter

- Opdaterer hvert 15. minut og efter hver til-/afmelding.
- Betalingsaktiviteter kan ikke besvares via API'et og tælles ikke med i *Mangler svar*.
- Aktiviteter kl. 00:00 uden sluttid vises som heldagsbegivenheder.
