# Holdsport til Home Assistant

> **English:** Home Assistant integration for [Holdsport](https://holdsport.dk), the Danish team sports platform. Shows training sessions, matches and other activities for every family member, lets you attend/decline, take tasks, and see who is coming – via a calendar, sensors, a sidebar panel and a dashboard card. Log in with your Holdsport account; profiles you manage (e.g. your children) can be selected.

Custom integration der viser træning, kampe og andre aktiviteter fra [Holdsport](https://holdsport.dk) for hele familien – og kan tilmelde/afmelde.

## Funktioner

- **Én enhed pr. familiemedlem.** Log ind med forælderens konto og vælg de profiler (børn) du administrerer. Voksne med egen konto tilføjes som en ekstra integration.
- **Kalender pr. person** (`calendar.<navn>`) med alle aktiviteter på tværs af hold – også historik, når man bladrer tilbage.
- **Sensorer pr. person:** Næste aktivitet, Næste træning, Næste kamp (timestamp + attributter som hold, sted, mødetid, status og `activity_id`) samt **Mangler svar** (antal kommende aktiviteter uden svar).
- **Handlinger:** `holdsport.attend` (tilmeld), `holdsport.decline` (afmeld) og `holdsport.take_task` (tag en opgave).
- **Holdsport-kort** i sidemenuen og til dashboards med kommende aktiviteter, ✓/✗-knapper og mødetid (se nedenfor).
- **Mødetid før kampe** kan beregnes automatisk, fx 90 min. før hjemmekampe (se *Indstillinger*).

## Holdsport i sidemenuen

Integrationen tilføjer selv **Holdsport** i HA's sidemenu med ét kort pr. familiemedlem – ingen opsætning. Efter opdatering: genstart HA og genindlæs browseren (Ctrl+F5).

## Dashboard-kort

Samme kort kan også lægges på et dashboard. Integrationen registrerer selv kortet under *Indstillinger → Dashboards → Resources*. Rediger dashboard → *Tilføj kort* → søg efter **Holdsport**, eller i YAML (kortet skal stå under en visnings `cards:`):

```yaml
type: custom:holdsport-card
entity: calendar.emma      # personens Holdsport-kalender
days: 14                   # valgfri, standard 14
title: Emma                # valgfri, standard er profilens navn
```

- Aktiviteter grupperet pr. dag, farvet efter type (kamp blå, træning grøn, stævne rød, medlemsaktivitet pink).
- ✓/✗ tilmelder/afmelder med det samme. Betalingsaktiviteter vises som *Svar i appen*.
- Er der en mødetid, står den som den primære tid med starttidspunktet under, fx **11.15** / start 12.45.
- Tryk på en aktivitet for mødested, beskrivelse og **deltagere** – hvem der er tilmeldt, udvalgt, til rådighed, afmeldt og mangler svar.
- **Opgaver** (fx kiosk eller billetsalg under hjemmekampe): tryk på en aktivitet for at se dem og hvem der har taget dem, og tag en opgave med *Tag opgaven*. Man kan ikke melde sig fra en opgave via API'et – det skal ske i Holdsport-appen.
- Holdsports chat er ikke tilgængelig via API'et og vises derfor ikke.

## Indstillinger

Under integrationens **Konfigurer**:

- **Familiemedlemmer** der skal vises.
- **Mødetid før kampe (minutter)** – fx 90. Bruges kun på kampe, hvor Holdsport ikke selv har en mødetid. 0 = fra.
- **Kun når stedet indeholder** – fx `Odense` for kun hjemmekampe. Flere steder adskilles med komma; tom = alle kampe.

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
