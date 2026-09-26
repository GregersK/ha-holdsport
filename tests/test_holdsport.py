from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.holdsport.api import HoldsportAuthError, HoldsportRejected
from custom_components.holdsport.const import CONF_PROFILES, DOMAIN
from custom_components.holdsport.coordinator import (
    match_meeting_start,
    parse_activity,
    parse_places,
)

PARENT, CHILD = 100, 200
PROFILES = [
    {"id": PARENT, "name": "Gregers Kissow"},
    {"id": CHILD, "name": "Emma"},
]


def _iso(delta_days, hour=18):
    d = (dt_util.now() + timedelta(days=delta_days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    return d.isoformat()


def _act(aid, name, days, type_id, type_name, status=0, method="POST", path=None, end=True, hour=18):
    return {
        "id": aid,
        "name": name,
        "starttime": _iso(days, hour),
        "endtime": _iso(days, hour + 1) if end else "",
        "place": "Stadion",
        "comment": "",
        "pickup_place": "",
        "pickup_time": "17:30",
        "status": status,
        "action_method": method,
        "action_path": path or f"/v1/activities/{aid}/activities_users",
        "actions": [],
        "event_type": type_name,
        "event_type_id": type_id,
    }


def _training_with_attendees():
    act = _act(1, "Træning", 1, 2, "Træning")
    act["activities_users"] = [
        {"id": 1, "name": "A", "status": "Tilmeldt", "status_code": 1},
        {"id": 2, "name": "B", "status": "Afmeldt", "status_code": 2},
        {"id": 3, "name": "C", "status": "Udvalgt", "status_code": 4},
    ]
    act["max_attendees"] = 20
    return act


CHILD_ACTS = [
    _training_with_attendees(),
    _act(2, "Kamp mod B93", 3, 1, "Kamp", status=1, method="PUT",
         path="/v1/activities/2/activities_users/999"),
    _act(3, "Stævne", 5, 4, "Stævne", end=False, hour=0),
    _act(4, "Betaling", 6, 9, "Medlemsaktivitet", method="GET",
         path="http://holdsport.dk/sign_in/x"),
    # hjemmekamp uden mødetid fra Holdsport -> mødetid kan beregnes
    {**_act(5, "Kamp i Odense", 8, 1, "Kamp", status=1, method="PUT",
            path="/v1/activities/5/activities_users/998"),
     "place": "Spar Nord Arena, Odense", "pickup_time": ""},
]


class FakeClient:
    def __init__(self, session, username, password):
        self.username = username
        self.password = password
        self.set_status = AsyncMock()
        self.take_task = AsyncMock()

    async def async_get_profiles(self):
        if self.password != "pw":
            raise HoldsportAuthError("bad")
        return PROFILES

    async def async_get_teams(self, login):
        if login == str(CHILD):
            return [{"id": 10, "name": "U10 piger", "role": 1}]
        return [{"id": 20, "name": "Old boys", "role": 1}]

    async def async_get_activities(self, login, team_id, *, from_date=None, page=1, per_page=50):
        if page > 1:
            return []
        return CHILD_ACTS if team_id == 10 else []

    async def async_get_activity(self, login, team_id, activity_id):
        return next(a for a in CHILD_ACTS if a["id"] == activity_id)

    async def async_set_status(self, login, method, path, joined):
        await self.set_status(login, method, path, joined)

    async def async_get_tasks(self, login, activity_id):
        return TASKS if activity_id == 5 else []

    async def async_take_task(self, login, activity_id, task_id):
        await self.take_task(login, activity_id, task_id)


TASKS = [
    {"id": 71, "name": "Kiosk", "max_participants": 2, "enable_attend": True,
     "activity_tasks": [{"id": 1, "user_id": 7, "name": "Anden forælder"}]},
    {"id": 72, "name": "Billetsalg", "max_participants": 1, "enable_attend": True,
     "activity_tasks": [{"id": 2, "user_id": CHILD, "name": "Emma"}]},
    {"id": 73, "name": "Speaker", "max_participants": 1, "enable_attend": True,
     "activity_tasks": [{"id": 3, "user_id": 8, "name": "Træner"}]},
    {"id": 74, "name": "Oprydning", "max_participants": None, "enable_attend": False,
     "activity_tasks": []},
]


CLIENTS = []


def _factory(*a):
    c = FakeClient(*a)
    CLIENTS.append(c)
    return c


@pytest.fixture(autouse=True)
def patch_client():
    CLIENTS.clear()
    with patch("custom_components.holdsport.HoldsportClient", side_effect=_factory), \
         patch("custom_components.holdsport.config_flow.HoldsportClient", side_effect=_factory):
        yield


async def _setup(hass, profiles=(PARENT, CHILD), **extra_options):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=str(PARENT),
        data={CONF_USERNAME: "g@x.dk", CONF_PASSWORD: "pw"},
        options={CONF_PROFILES: list(profiles), **extra_options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_USERNAME: "g@x.dk", CONF_PASSWORD: "nope"})
    assert r["errors"] == {"base": "invalid_auth"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_USERNAME: "g@x.dk", CONF_PASSWORD: "pw"})
    assert r["step_id"] == "profiles"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_PROFILES: [str(CHILD)]})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["options"] == {CONF_PROFILES: [CHILD]}
    assert r["result"].unique_id == str(PARENT)
    await hass.async_block_till_done()


async def test_entities(hass: HomeAssistant):
    await _setup(hass)
    states = {s.entity_id: s for s in hass.states.async_all()}
    print(sorted(states))
    dev_reg = dr.async_get(hass)
    devs = [d.name for d in dev_reg.devices.values() if any(i[0] == DOMAIN for i in d.identifiers)]
    assert sorted(devs) == ["Emma", "Gregers Kissow"]

    cal = hass.states.get("calendar.emma")
    assert cal is not None and cal.attributes["message"] == "Træning"

    training = hass.states.get("sensor.emma_next_training")
    match = hass.states.get("sensor.emma_next_match")
    assert training.attributes["activity_id"] == 1
    assert match.attributes["activity_id"] == 2
    assert match.attributes["status"] == "Tilmeldt"
    assert dt_util.parse_datetime(match.state) is not None
    unanswered = hass.states.get("sensor.emma_awaiting_response")
    # akt. 1 og 3 mangler svar; 4 er betaling (GET) og tælles ikke
    assert unanswered.state == "2"
    assert hass.states.get("sensor.gregers_kissow_next_activity").state == "unknown"


async def test_calendar_events(hass: HomeAssistant):
    await _setup(hass)
    res = await hass.services.async_call(
        "calendar", "get_events",
        {"entity_id": "calendar.emma", "start_date_time": dt_util.now().isoformat(),
         "end_date_time": (dt_util.now() + timedelta(days=30)).isoformat()},
        blocking=True, return_response=True,
    )
    events = res["calendar.emma"]["events"]
    assert [e["summary"] for e in events] == [
        "Træning", "Kamp mod B93", "Stævne", "Betaling", "Kamp i Odense"
    ]
    staevne = events[2]
    assert "T" not in staevne["start"]  # heldag
    assert "Hold: U10 piger" in events[0]["description"]


async def test_services(hass: HomeAssistant):
    await _setup(hass)
    client = CLIENTS[-1]
    dev = next(d for d in dr.async_get(hass).devices.values() if d.name == "Emma")

    await hass.services.async_call(DOMAIN, "attend", {"device_id": dev.id, "activity_id": "1"}, blocking=True)
    client.set_status.assert_awaited_with(str(CHILD), "POST", "/v1/activities/1/activities_users", 1)

    await hass.services.async_call(DOMAIN, "decline", {"device_id": dev.id, "activity_id": 2}, blocking=True)
    client.set_status.assert_awaited_with(str(CHILD), "PUT", "/v1/activities/2/activities_users/999", 2)

    # allerede tilmeldt -> intet kald
    client.set_status.reset_mock()
    await hass.services.async_call(DOMAIN, "attend", {"device_id": dev.id, "activity_id": 2}, blocking=True)
    client.set_status.assert_not_awaited()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "attend", {"device_id": dev.id, "activity_id": 4}, blocking=True)

    client.set_status.side_effect = HoldsportRejected("x")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "attend", {"device_id": dev.id, "activity_id": 1}, blocking=True)


async def test_options_removes_device(hass: HomeAssistant):
    entry = await _setup(hass)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["step_id"] == "init"
    r = await hass.config_entries.options.async_configure(
        r["flow_id"],
        {CONF_PROFILES: [str(CHILD)], "match_meeting_minutes": 90, "match_meeting_places": " Odense "},
    )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_PROFILES: [CHILD], "match_meeting_minutes": 90, "match_meeting_places": "Odense"
    }
    await hass.async_block_till_done()
    names = [d.name for d in dr.async_get(hass).devices.values() if any(i[0] == DOMAIN for i in d.identifiers)]
    assert names == ["Emma"]
    assert hass.states.get("calendar.emma") is not None


async def test_auth_failure_starts_reauth(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=str(PARENT),
                            data={CONF_USERNAME: "g@x.dk", CONF_PASSWORD: "old"},
                            options={CONF_PROFILES: [CHILD]})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == "reauth"
    r = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {CONF_PASSWORD: "pw"})
    assert r["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_PASSWORD] == "pw"
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_match_meeting_time(hass: HomeAssistant):
    await _setup(hass, match_meeting_minutes=90, match_meeting_places="odense, Vojens")
    rt = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    acts = {a.id: a for a in rt.coordinator.data[CHILD].activities}
    home = acts[5]
    assert home.meeting_start == home.start - timedelta(minutes=90)
    assert acts[2].meeting_start is None  # Holdsport har selv en mødetid (17:30)
    assert acts[1].meeting_start is None  # træning

    res = await hass.services.async_call(
        "calendar", "get_events",
        {"entity_id": "calendar.emma", "start_date_time": dt_util.now().isoformat(),
         "end_date_time": (dt_util.now() + timedelta(days=30)).isoformat()},
        blocking=True, return_response=True,
    )
    ev = next(e for e in res["calendar.emma"]["events"] if e["summary"] == "Kamp i Odense")
    local = dt_util.as_local(home.meeting_start)
    assert f"Mødetid: {local:%H.%M}" in ev["description"]


def test_match_meeting_start_rules():
    raw = _act(9, "Kamp", 2, 1, "Kamp", hour=14)
    raw.update(place="Aalborg Skøjtehal", pickup_time="")
    away = parse_activity(raw, 1, "U14")
    assert match_meeting_start(away, 90, ["Odense"]) is None
    assert match_meeting_start(away, 90, []) == away.start - timedelta(minutes=90)
    assert match_meeting_start(away, 0, []) is None
    assert parse_places(" Odense, ,Vojens ") == ["Odense", "Vojens"]


async def test_removed_sensor_is_cleaned_up(hass: HomeAssistant):
    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    old = ent_reg.async_get_or_create("sensor", DOMAIN, f"{PARENT}_{CHILD}_latest_message")
    await _setup(hass)
    assert ent_reg.async_get(old.entity_id) is None


async def test_websocket_subscribe(hass: HomeAssistant, hass_ws_client):
    await _setup(hass)
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "holdsport/subscribe", "entity_id": "calendar.emma"})
    result = await client.receive_json()
    assert result["success"], result
    event = (await client.receive_json())["event"]

    dev = next(d for d in dr.async_get(hass).devices.values() if d.name == "Emma")
    assert event["device_id"] == dev.id
    assert event["name"] == "Emma"
    assert event["available"] is True
    acts = {a["activity_id"]: a for a in event["activities"]}
    assert list(acts) == [1, 2, 3, 4, 5]
    training = acts[1]
    assert training["attending"] == 2  # Tilmeldt + Udvalgt
    assert training["max_attendees"] == 20
    assert "comments" not in training
    assert training["meeting_start"] is None  # slået fra som standard
    assert acts[4]["can_respond"] is False

    # Opdatering af coordinatoren skubbes ud til kortet
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinator
    await coordinator.async_refresh()
    assert (await client.receive_json())["event"]["name"] == "Emma"

    await client.send_json({"id": 2, "type": "holdsport/subscribe", "entity_id": "sensor.findes_ikke"})
    result = await client.receive_json()
    assert not result["success"]
    assert result["error"]["code"] == "not_found"


async def test_tasks(hass: HomeAssistant, hass_ws_client):
    await _setup(hass)
    client = CLIENTS[-1]
    dev = next(d for d in dr.async_get(hass).devices.values() if d.name == "Emma")
    ws = await hass_ws_client(hass)

    await ws.send_json({"id": 1, "type": "holdsport/tasks", "device_id": dev.id, "activity_id": 5})
    res = await ws.receive_json()
    assert res["success"], res
    tasks = {x["id"]: x for x in res["result"]}
    assert tasks[71]["can_take"] and tasks[71]["taken_by"] == ["Anden forælder"]
    assert tasks[72]["mine"] and not tasks[72]["can_take"]
    assert not tasks[73]["can_take"]  # fuld
    assert not tasks[74]["can_take"]  # kan ikke vælges selv
    assert tasks[74]["max_participants"] is None

    await hass.services.async_call(
        DOMAIN, "take_task", {"device_id": dev.id, "activity_id": 5, "task_id": "71"}, blocking=True
    )
    client.take_task.assert_awaited_with(str(CHILD), 5, 71)

    client.take_task.side_effect = HoldsportRejected("fuld")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "take_task", {"device_id": dev.id, "activity_id": 5, "task_id": 73}, blocking=True
        )


async def test_card_registered_as_lovelace_resource(hass: HomeAssistant):
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.setup import async_setup_component

    from custom_components.holdsport import _async_ensure_lovelace_resource

    assert await async_setup_component(hass, "lovelace", {})
    resources = hass.data[LOVELACE_DATA].resources

    await _async_ensure_lovelace_resource(hass, "/holdsport_static/holdsport-card.js?v=1")
    await _async_ensure_lovelace_resource(hass, "/holdsport_static/holdsport-card.js?v=1")
    items = [i for i in resources.async_items() if "holdsport" in i["url"]]
    assert [(i["url"], i["type"]) for i in items] == [
        ("/holdsport_static/holdsport-card.js?v=1", "module")
    ]

    # ny version opdaterer den eksisterende post i stedet for at lave en ny
    await _async_ensure_lovelace_resource(hass, "/holdsport_static/holdsport-card.js?v=2")
    items = [i for i in resources.async_items() if "holdsport" in i["url"]]
    assert [i["url"] for i in items] == ["/holdsport_static/holdsport-card.js?v=2"]
