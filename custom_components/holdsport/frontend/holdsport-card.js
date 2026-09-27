// Holdsport-kort til Home Assistant.
// Viser en profils kommende aktiviteter med til-/afmelding, mødetid, deltagere og opgaver.
// Data kommer live fra integrationen via websocket-kommandoen holdsport/subscribe.

const TYPE_COLORS = {
  1: "#2f6fd6", // Kamp
  2: "#3a9f5b", // Træning
  4: "#d0453a", // Stævne
  9: "#c0569b", // Medlemsaktivitet
};
const DEFAULT_COLOR = "#8a8a8a";

const STATUS_NONE = 0;
const STATUS_ATTENDING = 1;
const STATUS_DECLINED = 2;
const STATUS_AVAILABLE = 3;
const STATUS_SELECTED = 4;

const UNLIMITED_ATTENDEES = 999;

const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

class HoldsportCard extends HTMLElement {
  static getConfigForm() {
    const labels = {
      entity: "Person (Holdsport-kalender)",
      title: "Titel (valgfri)",
      days: "Antal dage frem",
    };
    return {
      schema: [
        {
          name: "entity",
          required: true,
          selector: { entity: { filter: { integration: "holdsport", domain: "calendar" } } },
        },
        { name: "title", selector: { text: {} } },
        { name: "days", selector: { number: { min: 1, max: 90, mode: "box" } } },
      ],
      computeLabel: (s) => labels[s.name],
    };
  }

  static getStubConfig(hass) {
    const cal = Object.values(hass.entities || {}).find(
      (e) => e.platform === "holdsport" && e.entity_id.startsWith("calendar."),
    );
    return { entity: cal ? cal.entity_id : "" };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._error = null;
    this._expanded = new Set();
    this._pending = new Map(); // activity_id -> joined_status der er sendt
    this._tasks = new Map(); // activity_id -> {loading, error, list, busy}
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Angiv entity, fx calendar.emma");
    }
    const changed = !this._config || this._config.entity !== config.entity;
    this._config = { days: 14, ...config };
    if (changed) {
      this._unsubscribe();
      this._data = null;
      this._subscribe();
    }
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._subscribe();
  }

  connectedCallback() {
    this._subscribe();
    // Tidspunkter ("om 2 timer") og hvad der er passeret skal følge uret
    this._timer = setInterval(() => this._render(), 60_000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._unsubscribe();
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  async _subscribe() {
    if (!this._hass || !this._config || !this.isConnected || this._unsubPromise) return;
    this._unsubPromise = this._hass.connection
      .subscribeMessage((msg) => this._onData(msg), {
        type: "holdsport/subscribe",
        entity_id: this._config.entity,
      })
      .catch((err) => {
        this._unsubPromise = null;
        this._error = err && err.message ? err.message : String(err);
        this._render();
        return null;
      });
  }

  _unsubscribe() {
    const p = this._unsubPromise;
    this._unsubPromise = null;
    if (p) p.then((unsub) => unsub && unsub()).catch(() => {});
  }

  _onData(msg) {
    this._data = msg;
    this._error = null;
    // Svar der nu er slået igennem hos Holdsport er ikke længere "i gang"
    for (const act of msg.activities) {
      if (this._pending.get(act.activity_id) === act.status_code) {
        this._pending.delete(act.activity_id);
      }
    }
    this._render();
  }

  async _onClick(ev) {
    const el = ev.target.closest("[data-action]");
    if (!el) return;
    const id = Number(el.dataset.id);
    const action = el.dataset.action;

    if (action === "toggle") {
      if (this._expanded.has(id)) {
        this._expanded.delete(id);
      } else {
        this._expanded.add(id);
        this._loadTasks(id);
      }
      this._render();
      return;
    }

    if (action === "task") {
      ev.stopPropagation();
      const taskId = Number(el.dataset.task);
      const entry = this._tasks.get(id);
      if (!entry || entry.busy || !this._data) return;
      entry.busy = taskId;
      this._render();
      try {
        await this._hass.callService("holdsport", "take_task", {
          device_id: this._data.device_id,
          activity_id: id,
          task_id: taskId,
        });
      } catch (err) {
        this._toast(err && err.message ? err.message : "Holdsport afviste opgaven");
      }
      await this._loadTasks(id);
      return;
    }

    if (action === "attend" || action === "decline") {
      ev.stopPropagation();
      if (this._pending.has(id) || !this._data) return;
      this._pending.set(id, action === "attend" ? STATUS_ATTENDING : STATUS_DECLINED);
      this._render();
      try {
        await this._hass.callService("holdsport", action, {
          device_id: this._data.device_id,
          activity_id: id,
        });
      } catch (err) {
        this._pending.delete(id);
        this._render();
        this._toast(err && err.message ? err.message : "Holdsport afviste ændringen");
        return;
      }
      // Hvis svaret ikke ændrede noget (fx allerede tilmeldt), kommer der ingen ny data
      setTimeout(() => {
        if (this._pending.delete(id)) this._render();
      }, 5000);
    }
  }

  // Opgaver hentes først når aktiviteten foldes ud (de er ikke med i aktivitetslisten)
  async _loadTasks(id) {
    if (!this._data) return;
    const prev = this._tasks.get(id);
    this._tasks.set(id, { loading: !prev || !prev.list, list: prev && prev.list });
    this._render();
    try {
      const list = await this._hass.connection.sendMessagePromise({
        type: "holdsport/tasks",
        device_id: this._data.device_id,
        activity_id: id,
      });
      this._tasks.set(id, { list });
    } catch (err) {
      this._tasks.set(id, { error: (err && err.message) || "Kunne ikke hente opgaver" });
    }
    this._render();
  }

  // Hvem kommer: grupperet efter status, fra aktivitetens activities_users + no_rsvp
  _renderParticipants(act) {
    const groups = [
      [[STATUS_ATTENDING], "Tilmeldt", "ok"],
      [[STATUS_SELECTED], "Udvalgt", "sel"],
      [[STATUS_AVAILABLE], "Til rådighed", "avail"],
      [[STATUS_DECLINED], "Afmeldt", "no"],
    ];
    const known = new Set(groups.flatMap((g) => g[0]));
    const people = act.participants || [];
    const rows = groups.map(([codes, label, cls]) => [
      label,
      cls,
      people.filter((p) => codes.includes(p.status_code)).map((p) => p.name),
    ]);
    rows.push(["Andet", "other", people.filter((p) => !known.has(p.status_code) && p.status_code !== STATUS_NONE).map((p) => p.name)]);
    rows.push(["Mangler svar", "none", [...people.filter((p) => p.status_code === STATUS_NONE).map((p) => p.name), ...(act.no_rsvp || [])]]);
    const html = rows
      .filter(([, , names]) => names.length)
      .map(
        ([label, cls, names]) => `
          <div class="pgroup">
            <span class="chip ${cls}">${esc(label)} ${names.length}</span>
            <span class="pnames">${names.map(esc).join(", ")}</span>
          </div>`,
      )
      .join("");
    return html ? `<div class="people"><div class="tasks-head">Deltagere</div>${html}</div>` : "";
  }

  _renderTasks(act) {
    const entry = this._tasks.get(act.activity_id);
    if (!entry) return "";
    if (entry.loading) return `<div class="tasks-note">Henter opgaver…</div>`;
    if (entry.error) return `<div class="tasks-note">${esc(entry.error)}</div>`;
    if (!entry.list || !entry.list.length) return "";
    const rows = entry.list
      .map((task) => {
        const n = task.taken_by.length;
        const count = task.max_participants ? `${n}/${task.max_participants}` : `${n}`;
        const full = task.max_participants && n >= task.max_participants;
        let action;
        if (task.mine) {
          action = `<span class="chip ok">Din opgave</span>`;
        } else if (task.can_take) {
          const busy = entry.busy === task.id ? "disabled" : "";
          action = `<button class="take" data-action="task" data-id="${act.activity_id}" data-task="${task.id}" ${busy}>Tag opgaven</button>`;
        } else {
          action = `<span class="chip other">${full ? "Fuld" : "Vælges af træner"}</span>`;
        }
        const names = task.taken_by.length
          ? `<div class="task-names">${task.taken_by.map(esc).join(", ")}</div>`
          : "";
        return `
          <div class="task">
            <div class="task-main"><b>${esc(task.name)}</b> <span class="task-count">${count}</span>${names}</div>
            <div>${action}</div>
          </div>`;
      })
      .join("");
    return `<div class="tasks"><div class="tasks-head">Opgaver</div>${rows}</div>`;
  }

  _toast(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }),
    );
  }

  _locale() {
    // Kortets tekster er danske, så datoer og tider formateres også på dansk
    // uanset HA-brugerens sprog (ellers fås "04:30 PM" midt i dansk tekst)
    return "da-DK";
  }

  _fmtTime(d) {
    return d.toLocaleTimeString(this._locale(), { hour: "2-digit", minute: "2-digit", hour12: false });
  }

  _dayLabel(d) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const day = new Date(d);
    day.setHours(0, 0, 0, 0);
    const diff = Math.round((day - today) / 86_400_000);
    if (diff === 0) return "I dag";
    if (diff === 1) return "I morgen";
    const s = d.toLocaleDateString(this._locale(), { weekday: "long", day: "numeric", month: "long" });
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  _statusChip(code, canRespond, text) {
    if (!canRespond && code === STATUS_NONE) {
      // Fx betalingsaktiviteter – de kræver Holdsport-appen og tæller ikke som ubesvarede
      return `<span class="chip app" title="Kan kun besvares i Holdsport-appen">Svar i appen</span>`;
    }
    switch (code) {
      case STATUS_ATTENDING:
        return `<span class="chip ok">Tilmeldt</span>`;
      case STATUS_SELECTED:
        return `<span class="chip sel">Udvalgt</span>`;
      case STATUS_AVAILABLE:
        return `<span class="chip avail">Til rådighed</span>`;
      case STATUS_DECLINED:
        return `<span class="chip no">Afmeldt</span>`;
      case STATUS_NONE:
        return `<span class="chip none">Mangler svar</span>`;
      default:
        // Statuskoder vi ikke kender betydningen af endnu – vis Holdsports egen tekst
        return text ? `<span class="chip other">${esc(text)}</span>` : "";
    }
  }

  // Mødetid som klokkeslæt: Holdsports egen, eller den integrationen har beregnet for kampe
  _meetingTime(act) {
    const m = /^(\d{1,2})[:.](\d{2})$/.exec((act.meeting_time || "").trim());
    if (m) return `${m[1].padStart(2, "0")}.${m[2]}`;
    return act.meeting_start ? this._fmtTime(new Date(act.meeting_start)) : "";
  }

  _renderActivity(act, now) {
    const start = new Date(act.start);
    const started = start <= now;
    const pending = this._pending.get(act.activity_id);
    const status = pending ?? act.status_code;
    const attending = status === STATUS_ATTENDING || status === STATUS_SELECTED;
    const color = TYPE_COLORS[act.event_type_id] || DEFAULT_COLOR;
    const expanded = this._expanded.has(act.activity_id);

    const time = act.all_day ? "Heldag" : this._fmtTime(start);
    const meetAt = act.all_day ? "" : this._meetingTime(act);
    const meta = [act.team, act.place].filter(Boolean).map(esc).join(" · ");
    const meetText = meetAt || act.meeting_time;
    const meeting = meetText || act.meeting_place
      ? `<div class="meeting"><ha-icon icon="mdi:clock-outline"></ha-icon>Mødetid ${esc(meetText)}${
          act.meeting_place ? ` · ${esc(act.meeting_place)}` : ""
        }</div>`
      : "";
    // Holdsport bruger 999 som "ingen grænse"
    const count = act.max_attendees && act.max_attendees < UNLIMITED_ATTENDEES
      ? `${act.attending}/${act.max_attendees}`
      : act.attending
        ? `${act.attending}`
        : "";

    let buttons = "";
    if (act.can_respond && !started) {
      const dis = pending !== undefined ? "disabled" : "";
      buttons = `
        <button class="rsvp yes ${attending ? "on" : ""}" data-action="attend" data-id="${act.activity_id}" ${dis} title="Tilmeld">
          <ha-icon icon="mdi:check"></ha-icon></button>
        <button class="rsvp no ${status === STATUS_DECLINED ? "on" : ""}" data-action="decline" data-id="${act.activity_id}" ${dis} title="Afmeld">
          <ha-icon icon="mdi:close"></ha-icon></button>`;
    }

    let details = "";
    if (expanded) {
      const desc = act.comment ? `<div class="desc">${esc(act.comment)}</div>` : "";
      const tasks = this._renderTasks(act);
      const people = this._renderParticipants(act);
      details =
        meeting || desc || tasks || people
          ? `<div class="details">${meeting}${desc}${people}${tasks}</div>`
          : `<div class="details empty-details">Ingen yderligere oplysninger</div>`;
    }

    return `
      <div class="act ${started ? "started" : ""} ${expanded ? "open" : ""}" style="--type-color:${color}">
        <div class="row" data-action="toggle" data-id="${act.activity_id}">
          ${
            meetAt
              ? `<div class="time" title="Mødetid – aktiviteten starter ${esc(time)}">${esc(meetAt)}<div class="start">start ${esc(time)}</div></div>`
              : `<div class="time">${esc(time)}</div>`
          }
          <div class="main">
            <div class="name">${esc(act.name)}</div>
            <div class="meta">${meta}${count ? ` · <ha-icon icon="mdi:account-multiple"></ha-icon>${count}` : ""}</div>
            <div class="tags">${this._statusChip(status, act.can_respond, pending === undefined ? act.status : "")}</div>
          </div>
          <div class="actions">${buttons}</div>
        </div>
        ${details}
      </div>`;
  }

  _render() {
    if (!this._config) return;
    const title = this._config.title || (this._data && this._data.name) || "Holdsport";
    let body;

    if (this._error) {
      body = `<div class="empty">Kunne ikke hente fra Holdsport-integrationen: ${esc(this._error)}</div>`;
    } else if (!this._data) {
      body = `<div class="empty">Henter…</div>`;
    } else {
      const now = new Date();
      const until = new Date(now.getTime() + Number(this._config.days) * 86_400_000);
      const acts = this._data.activities.filter(
        (a) => new Date(a.end) > now && new Date(a.start) < until,
      );
      if (!acts.length) {
        body = `<div class="empty">Ingen aktiviteter de næste ${esc(this._config.days)} dage</div>`;
      } else {
        const groups = [];
        for (const a of acts) {
          const label = this._dayLabel(new Date(a.start));
          if (!groups.length || groups[groups.length - 1].label !== label) {
            groups.push({ label, items: [] });
          }
          groups[groups.length - 1].items.push(a);
        }
        body = groups
          .map(
            (g) =>
              `<div class="day">${esc(g.label)}</div>${g.items
                .map((a) => this._renderActivity(a, now))
                .join("")}`,
          )
          .join("");
      }
      if (!this._data.available) {
        body = `<div class="warn">Holdsport svarer ikke – viser seneste data</div>${body}`;
      }
    }

    const unanswered = this._data
      ? this._data.activities.filter(
          (a) => a.status_code === STATUS_NONE && a.can_respond && new Date(a.start) > new Date(),
        ).length
      : 0;

    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="header">
          <span>${esc(title)}</span>
          ${unanswered ? `<span class="badge">${unanswered} mangler svar</span>` : ""}
        </div>
        <div class="list">${body}</div>
      </ha-card>`;
  }
}

const STYLE = `
  ha-card { overflow: hidden; }
  .header {
    display: flex; align-items: center; justify-content: space-between; gap: 8px;
    padding: 16px 16px 8px; font-size: 1.25em; font-weight: 500;
  }
  .badge {
    font-size: 0.6em; font-weight: 500; padding: 3px 8px; border-radius: 12px;
    background: var(--warning-color, #ff9800); color: #fff; white-space: nowrap;
  }
  .list { padding: 0 8px 12px; }
  .day {
    padding: 12px 8px 4px; font-size: 0.85em; font-weight: 500;
    color: var(--secondary-text-color); text-transform: none;
  }
  .act {
    border-left: 4px solid var(--type-color); border-radius: 6px;
    margin: 4px 0; background: var(--secondary-background-color, rgba(127,127,127,0.06));
  }
  .act.started { opacity: 0.65; }
  .row { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px; cursor: pointer; }
  .time { min-width: 44px; font-weight: 500; font-variant-numeric: tabular-nums; padding-top: 1px; white-space: nowrap; }
  .start { font-size: 0.75em; font-weight: 400; color: var(--secondary-text-color); }
  .people { margin-top: 6px; display: flex; flex-direction: column; gap: 4px; }
  .pgroup { line-height: 1.5; }
  .pgroup .chip { margin-right: 6px; }
  .pnames { overflow-wrap: anywhere; }
  .main { flex: 1; min-width: 0; }
  .name { font-weight: 500; overflow-wrap: anywhere; }
  .meta { font-size: 0.85em; color: var(--secondary-text-color); overflow-wrap: anywhere; }
  .meta ha-icon, .meeting ha-icon { --mdc-icon-size: 14px; vertical-align: -2px; margin-right: 2px; }
  .tags { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
  .chip { font-size: 0.75em; padding: 1px 8px; border-radius: 10px; color: #fff; }
  .chip.ok { background: var(--success-color, #43a047); }
  .chip.sel { background: var(--info-color, #039be5); }
  .chip.avail { background: #00897b; }
  .chip.no { background: var(--error-color, #db4437); }
  .chip.none { background: var(--warning-color, #ff9800); }
  .actions { display: flex; gap: 6px; align-items: center; }
  .rsvp {
    width: 34px; height: 34px; border-radius: 50%; cursor: pointer; padding: 0;
    display: inline-flex; align-items: center; justify-content: center;
    background: transparent; color: var(--secondary-text-color);
    border: 1px solid var(--divider-color, #ccc);
  }
  .rsvp ha-icon { --mdc-icon-size: 20px; }
  .rsvp.yes.on { background: var(--success-color, #43a047); border-color: transparent; color: #fff; }
  .rsvp.no.on { background: var(--error-color, #db4437); border-color: transparent; color: #fff; }
  .rsvp:disabled { opacity: 0.5; cursor: progress; }
  .chip.app, .chip.other { background: var(--disabled-color, #9e9e9e); }
  .details { padding: 0 10px 10px 64px; font-size: 0.9em; }
  .meeting { color: var(--secondary-text-color); margin-bottom: 6px; }
  .desc { white-space: pre-wrap; margin-bottom: 8px; font-style: italic; overflow-wrap: anywhere; }
  .empty { color: var(--secondary-text-color); padding: 12px 8px; }
  .empty-details { color: var(--secondary-text-color); }
  .tasks { margin-top: 6px; display: flex; flex-direction: column; gap: 6px; }
  .tasks-head { font-weight: 500; }
  .tasks-note { color: var(--secondary-text-color); margin-top: 6px; }
  .task {
    display: flex; justify-content: space-between; align-items: center; gap: 8px;
    background: var(--card-background-color, #fff); border-radius: 8px; padding: 6px 10px;
    border: 1px solid var(--divider-color, rgba(0,0,0,0.12));
  }
  .task-count { color: var(--secondary-text-color); font-size: 0.9em; }
  .task-names { color: var(--secondary-text-color); font-size: 0.85em; overflow-wrap: anywhere; }
  .take {
    border: none; border-radius: 16px; padding: 6px 12px; cursor: pointer; white-space: nowrap;
    background: var(--primary-color); color: var(--text-primary-color, #fff); font: inherit; font-size: 0.85em;
  }
  .take:disabled { opacity: 0.5; cursor: progress; }
  .warn { color: var(--warning-color, #ff9800); padding: 8px; font-size: 0.9em; }
  @media (max-width: 450px) { .details { padding-left: 10px; } }
`;

// Sidepanel ("Holdsport" i sidemenuen): ét kort pr. familiemedlem, uden opsætning.
// Profilerne findes ud fra integrationens kalendere i entity-registret.
class HoldsportPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._cards = new Map(); // entity_id -> holdsport-card
    this._key = null;
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    const btn = this.shadowRoot.querySelector("ha-menu-button");
    if (btn) btn.narrow = narrow;
  }

  set panel(_panel) {}

  _calendars() {
    return Object.values(this._hass.entities || {})
      .filter((e) => e.platform === "holdsport" && e.entity_id.startsWith("calendar."))
      .map((e) => e.entity_id)
      .sort((a, b) => this._name(a).localeCompare(this._name(b)));
  }

  _name(entityId) {
    const st = this._hass.states[entityId];
    return (st && st.attributes.friendly_name) || entityId;
  }

  _update() {
    if (!this._hass) return;
    const cals = this._calendars();
    const key = cals.join(",");
    if (key !== this._key) {
      this._key = key;
      this._build(cals);
    }
    const btn = this.shadowRoot.querySelector("ha-menu-button");
    if (btn) btn.hass = this._hass;
    for (const card of this._cards.values()) card.hass = this._hass;
  }

  _build(cals) {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; min-height: 100vh; background: var(--primary-background-color); }
        .toolbar {
          display: flex; align-items: center; height: 56px; padding: 0 12px; gap: 4px;
          background: var(--app-header-background-color, var(--primary-color));
          color: var(--app-header-text-color, #fff); font-size: 20px;
        }
        .grid {
          display: grid; gap: 16px; padding: 16px; box-sizing: border-box;
          grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); align-items: start;
        }
        @media (max-width: 420px) { .grid { grid-template-columns: 1fr; padding: 8px; gap: 8px; } }
        .empty { padding: 24px; color: var(--secondary-text-color); }
      </style>
      <div class="toolbar"><ha-menu-button></ha-menu-button><span>Holdsport</span></div>
      <div class="grid"></div>`;
    const btn = this.shadowRoot.querySelector("ha-menu-button");
    btn.narrow = this._narrow;
    const grid = this.shadowRoot.querySelector(".grid");
    this._cards = new Map();
    if (!cals.length) {
      grid.innerHTML = `<div class="empty">Ingen Holdsport-profiler fundet. Tilføj integrationen under Enheder og tjenester.</div>`;
      return;
    }
    for (const entity of cals) {
      const card = document.createElement("holdsport-card");
      card.setConfig({ entity, days: 30 });
      grid.appendChild(card);
      this._cards.set(entity, card);
    }
  }
}

if (!customElements.get("holdsport-panel")) {
  customElements.define("holdsport-panel", HoldsportPanel);
}

if (!customElements.get("holdsport-card")) {
  customElements.define("holdsport-card", HoldsportCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "holdsport-card",
    name: "Holdsport",
    description: "Kommende aktiviteter med til-/afmelding, mødetid og opgaver",
    preview: false,
    documentationURL: "https://github.com/GregersK/ha-holdsport",
  });
}
