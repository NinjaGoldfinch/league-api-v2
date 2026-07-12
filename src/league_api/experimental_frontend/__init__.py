from html import escape
from json import dumps
from typing import Any
from urllib.parse import quote, unquote

from fastapi import FastAPI, HTTPException, status
from starlette.responses import HTMLResponse

RESERVED_ROOT_PATHS = frozenset(
    {
        "docs",
        "favicon.ico",
        "jobs",
        "lol",
        "manager",
        "openapi.json",
        "profiles",
        "redoc",
        "riot",
    }
)


def register_experimental_frontend(app: FastAPI) -> None:
    """Register the removable experimental profile frontend."""

    @app.get("/", include_in_schema=False)
    async def experimental_frontend_home() -> HTMLResponse:
        return _render_shell()

    @app.get("/manager", include_in_schema=False)
    async def experimental_frontend_manager() -> HTMLResponse:
        return _render_shell(manager=True)

    @app.get("/{profile_slug:path}", include_in_schema=False)
    async def experimental_frontend_profile(profile_slug: str) -> HTMLResponse:
        profile = parse_profile_slug(profile_slug)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
        return _render_shell(profile=profile)


def build_profile_slug(game_name: str, tag_line: str) -> str:
    """Build a shareable root-level profile slug from Riot ID parts."""
    return f"{quote(game_name.strip(), safe='')}-{quote(tag_line.strip(), safe='')}"


def parse_profile_slug(profile_slug: str) -> dict[str, str] | None:
    """Parse /gameName-tagLine slugs, using the final hyphen as the separator."""
    normalized_slug = profile_slug.strip("/")
    if (
        not normalized_slug
        or "/" in normalized_slug
        or normalized_slug.split("/", maxsplit=1)[0] in RESERVED_ROOT_PATHS
    ):
        return None

    game_name_slug, separator, tag_line_slug = normalized_slug.rpartition("-")
    if not separator or not game_name_slug or not tag_line_slug:
        return None

    game_name = unquote(game_name_slug).strip()
    tag_line = unquote(tag_line_slug).strip()
    if not game_name or not tag_line or "#" in game_name or "#" in tag_line:
        return None

    canonical_slug = build_profile_slug(game_name, tag_line)
    return {
        "gameName": game_name,
        "tagLine": tag_line,
        "riotId": f"{game_name}#{tag_line}",
        "profileSlug": canonical_slug,
    }


def _render_shell(*, profile: dict[str, str] | None = None, manager: bool = False) -> HTMLResponse:
    config: dict[str, Any] = {
        "page": "manager" if manager else "profile" if profile is not None else "home",
        "profile": profile,
        "defaults": {
            "accountRegionalRoute": "asia",
            "platformRoute": "oc1",
            "regionalRoute": "sea",
            "profileMatchLimit": 15,
        },
    }
    title = (
        "Match database manager"
        if manager
        else profile["riotId"]
        if profile is not None
        else "Profile search"
    )
    html = _HTML_TEMPLATE.replace("__PAGE_TITLE__", escape(title)).replace(
        "__APP_CONFIG__", dumps(config, separators=(",", ":"))
    )
    return HTMLResponse(html)


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__PAGE_TITLE__ - League API</title>
  <style>
    :root {
      color-scheme: light;
      --paper: #f6f8fb;
      --panel: #ffffff;
      --ink: #182230;
      --muted: #667085;
      --line: #d7dee8;
      --blue: #2266d2;
      --blue-dark: #194a9a;
      --amber: #ba7b13;
      --green: #157a5b;
      --red: #b42318;
      --shadow: 0 14px 40px rgba(24, 34, 48, 0.09);
      font-family:
        Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    * {
      box-sizing: border-box;
    }

    body {
      min-height: 100vh;
      margin: 0;
      color: var(--ink);
      background:
        linear-gradient(180deg, rgba(34, 102, 210, 0.08), rgba(34, 102, 210, 0) 320px),
        var(--paper);
    }

    button,
    input {
      font: inherit;
    }

    button:focus-visible,
    input:focus-visible,
    a:focus-visible {
      outline: 3px solid rgba(34, 102, 210, 0.34);
      outline-offset: 3px;
    }

    .shell {
      width: min(1080px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 56px;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 48px;
    }

    .brand {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      color: var(--ink);
      font-weight: 760;
      text-decoration: none;
    }

    .brand-mark {
      display: grid;
      width: 30px;
      height: 30px;
      place-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--blue);
      box-shadow: var(--shadow);
      font-weight: 850;
    }

    .nav-note {
      color: var(--muted);
      font-size: 0.9rem;
    }

    .hero {
      display: grid;
      gap: 22px;
      max-width: 760px;
      margin: 0 auto 40px;
      text-align: center;
    }

    h1 {
      max-width: 720px;
      margin: 0 auto;
      font-size: clamp(2.35rem, 7vw, 5.4rem);
      line-height: 0.93;
      letter-spacing: 0;
    }

    .lede {
      max-width: 610px;
      margin: 0 auto;
      color: var(--muted);
      font-size: 1.06rem;
      line-height: 1.55;
    }

    .search {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      width: min(680px, 100%);
      margin: 0 auto;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
    }

    .search input {
      width: 100%;
      min-width: 0;
      border: 0;
      border-radius: 6px;
      padding: 14px 14px;
      color: var(--ink);
      background: #f9fbfd;
    }

    .search button {
      border: 0;
      border-radius: 6px;
      padding: 0 18px;
      color: white;
      background: var(--blue);
      cursor: pointer;
      font-weight: 760;
    }

    .search button:hover {
      background: var(--blue-dark);
    }

    .message {
      min-height: 22px;
      color: var(--red);
      font-size: 0.92rem;
      text-align: center;
    }

    .profile {
      display: none;
      gap: 18px;
    }

    .manager {
      display: none;
      gap: 18px;
    }

    body[data-page="manager"] .manager {
      display: grid;
    }

    body[data-page="manager"] .hero {
      display: none;
    }

    body[data-page="profile"] .profile {
      display: grid;
    }

    body[data-page="profile"] .hero {
      margin-bottom: 28px;
      text-align: left;
    }

    body[data-page="profile"] .hero h1,
    body[data-page="profile"] .hero .lede,
    body[data-page="profile"] .hero .search,
    body[data-page="profile"] .hero .message {
      margin-left: 0;
      margin-right: 0;
    }

    .profile-header,
    .panel,
    .match-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.92);
      box-shadow: var(--shadow);
    }

    .profile-header {
      display: grid;
      grid-template-columns: auto 1fr auto;
      align-items: center;
      gap: 18px;
      padding: 18px;
    }

    .profile-status-actions {
      display: grid;
      justify-items: end;
      gap: 8px;
    }

    .profile-icon {
      width: 76px;
      height: 76px;
      border: 1px solid var(--line);
      border-radius: 8px;
      object-fit: cover;
      background: #e8eef7;
    }

    .identity h2 {
      margin: 0 0 6px;
      font-size: 1.7rem;
      letter-spacing: 0;
    }

    .identity p,
    .stat span,
    .panel p {
      margin: 0;
      color: var(--muted);
    }

    .status-pill {
      display: inline-flex;
      min-height: 36px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 0 12px;
      color: var(--muted);
      background: #f9fbfd;
      font-size: 0.9rem;
      font-weight: 700;
      white-space: nowrap;
    }

    .status-pill[data-tone="ready"] {
      border-color: rgba(21, 122, 91, 0.24);
      color: var(--green);
      background: rgba(21, 122, 91, 0.08);
    }

    .status-pill[data-tone="wait"] {
      border-color: rgba(186, 123, 19, 0.26);
      color: var(--amber);
      background: rgba(186, 123, 19, 0.09);
    }

    .status-pill[data-tone="error"] {
      border-color: rgba(180, 35, 24, 0.22);
      color: var(--red);
      background: rgba(180, 35, 24, 0.08);
    }

    .progress {
      display: grid;
      gap: 8px;
      padding: 16px;
    }

    .progress-row {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      color: var(--muted);
      font-size: 0.92rem;
    }

    .rail {
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: #e8eef7;
    }

    .rail-fill {
      width: 0%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--blue), var(--amber));
      transition: width 180ms ease;
    }

    .stats {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }

    .stat,
    .panel {
      padding: 16px;
    }

    .stat strong {
      display: block;
      margin-top: 5px;
      font-size: 1.45rem;
      letter-spacing: 0;
    }

    .match-list {
      display: grid;
      gap: 12px;
    }

    .load-more {
      justify-self: center;
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 16px;
      color: var(--blue);
      background: var(--panel);
      cursor: pointer;
      font-weight: 760;
      box-shadow: var(--shadow);
    }

    .load-more:hover:not(:disabled) {
      border-color: rgba(34, 102, 210, 0.34);
      background: #f9fbfd;
    }

    .load-more:disabled {
      cursor: progress;
      opacity: 0.7;
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 6px;
    }

    .action-button,
    .manager button,
    .manager-link {
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 6px 10px;
      color: var(--blue);
      background: var(--panel);
      cursor: pointer;
      font-weight: 700;
      text-decoration: none;
    }

    .action-button[data-danger="true"] {
      color: var(--red);
    }

    .manager-toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }

    .manager-toolbar input {
      min-height: 40px;
      flex: 1 1 220px;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 10px;
    }

    .manager-row {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
    }

    .manager-row code {
      overflow-wrap: anywhere;
    }

    .match-card {
      display: grid;
      grid-template-columns: auto 1fr auto;
      align-items: center;
      gap: 14px;
      padding: 14px;
    }

    .champion-icon {
      width: 54px;
      height: 54px;
      border: 1px solid var(--line);
      border-radius: 8px;
      object-fit: cover;
      background: #e8eef7;
    }

    .match-card h3 {
      margin: 0 0 4px;
      font-size: 1rem;
      letter-spacing: 0;
    }

    .match-card p {
      margin: 0;
      color: var(--muted);
      font-size: 0.9rem;
    }

    .raw {
      overflow-x: auto;
      max-height: 240px;
      border-radius: 8px;
      padding: 12px;
      color: #344054;
      background: #f9fbfd;
      font-size: 0.82rem;
      line-height: 1.45;
    }

    @media (max-width: 720px) {
      .shell {
        width: min(100% - 22px, 1080px);
        padding-top: 18px;
      }

      .topbar,
      .profile-header,
      .match-card {
        align-items: start;
        grid-template-columns: 1fr;
      }

      .topbar {
        gap: 8px;
        margin-bottom: 34px;
      }

      .nav-note {
        display: none;
      }

      .search,
      .stats {
        grid-template-columns: 1fr;
      }

      .search button {
        min-height: 46px;
      }

      .profile-icon {
        width: 68px;
        height: 68px;
      }
    }
  </style>
</head>
<body>
  <main class="shell">
    <nav class="topbar" aria-label="Primary">
      <a class="brand" href="/">
        <span class="brand-mark">L</span>
        <span>League API</span>
      </a>
      <div class="actions">
        <a class="manager-link" href="/manager">DB manager</a>
        <span class="nav-note">Experimental profile frontend</span>
      </div>
    </nav>

    <section class="hero" aria-labelledby="page-title">
      <h1 id="page-title">Search a Riot profile</h1>
      <p class="lede" id="page-copy">
        Enter a Riot ID to check cached profile data, then watch the profile update job fill in
        the gaps.
      </p>
      <form class="search" id="search-form">
        <input
          id="riot-id-input"
          name="riot_id"
          autocomplete="off"
          placeholder="NinjaGoldfinch#OCENZ"
          aria-label="Riot ID"
        >
        <button type="submit">Open profile</button>
      </form>
      <div class="message" id="form-message" role="status" aria-live="polite"></div>
    </section>

    <section class="manager" id="manager-view" aria-label="Match database manager">
      <article class="panel">
        <h1>Match database manager</h1>
        <p>Inspect, fetch, unlink, and delete durable Match-V5 data and exact cache entries.</p>
      </article>
      <section class="stats" aria-label="Manager summary">
        <article class="panel stat">
          <span>Durable matches</span><strong id="manager-match-count">-</strong>
        </article>
        <article class="panel stat">
          <span>Player links</span><strong id="manager-link-count">-</strong>
        </article>
        <article class="panel stat">
          <span>Riot cache entries</span><strong id="manager-cache-count">-</strong>
        </article>
      </section>
      <article class="panel manager-toolbar">
        <input id="manager-search" placeholder="Filter match ID" aria-label="Filter match ID">
        <input id="manager-puuid" placeholder="Optional PUUID" aria-label="Optional PUUID">
        <input
          id="manager-fetch-ids"
          placeholder="Match IDs to fetch (comma separated)"
          aria-label="Match IDs to fetch"
        >
        <button id="manager-refresh" type="button">Refresh</button>
        <button id="manager-fetch" type="button">Fetch selected</button>
        <label><input id="manager-force" type="checkbox"> Force upstream</label>
        <button id="manager-prune" type="button">Prune expired cache</button>
      </article>
      <article class="panel"><p id="manager-message">Loading manager data.</p></article>
      <section class="match-list" id="manager-match-list" aria-label="Stored matches"></section>
      <article class="panel">
        <p>Selected match payload</p>
        <pre class="raw" id="manager-raw">{}</pre>
      </article>
    </section>

    <section class="profile" id="profile-view" aria-label="Profile">
      <article class="profile-header">
        <img class="profile-icon" id="profile-icon" alt="" hidden>
        <div class="identity">
          <h2 id="profile-title">Profile</h2>
          <p id="profile-subtitle">Waiting for data.</p>
        </div>
        <div class="profile-status-actions">
          <span class="status-pill" id="profile-status">Not started</span>
          <button id="refresh-profile" type="button">Refresh profile</button>
        </div>
      </article>

      <article class="panel progress" aria-label="Profile fetch progress">
        <div class="progress-row">
          <span id="progress-label">Preparing profile lookup.</span>
          <strong id="progress-percent">0%</strong>
        </div>
        <div class="rail" aria-hidden="true"><div class="rail-fill" id="progress-fill"></div></div>
      </article>

      <section class="stats" aria-label="Profile summary">
        <article class="panel stat">
          <span>Summoner level</span>
          <strong id="summoner-level">-</strong>
        </article>
        <article class="panel stat">
          <span>Recent match IDs</span>
          <strong id="match-id-count">-</strong>
        </article>
        <article class="panel stat">
          <span>Match details</span>
          <strong id="match-detail-count">-</strong>
        </article>
      </section>

      <article class="panel">
        <p id="job-note">No job has been started.</p>
      </article>

      <article class="panel">
        <p>Diagnostics</p>
        <pre class="raw" id="profile-diagnostics">{}</pre>
      </article>

      <section class="match-list" id="match-list" aria-label="Recent matches"></section>
      <button class="load-more" id="load-more-matches" type="button" hidden>
        Load more matches
      </button>

      <article class="panel">
        <p>Latest payload</p>
        <pre class="raw" id="raw-payload">{}</pre>
      </article>
    </section>
  </main>

  <script>
    window.__LEAGUE_PROFILE_CONFIG__ = __APP_CONFIG__;

    const config = window.__LEAGUE_PROFILE_CONFIG__;
    const PROFILE_REFRESH_LOCKOUT_MS = 60_000;
    const state = {
      dataDragonVersion: "15.13.1",
      jobId: null,
      pollTimer: null,
      profilePuuid: null,
      cachedProfile: null,
      jobResult: null,
      matchPagination: null,
      refreshLockTimer: null,
      profileLifecycleState: null
    };

    const els = {
      body: document.body,
      form: document.getElementById("search-form"),
      input: document.getElementById("riot-id-input"),
      message: document.getElementById("form-message"),
      title: document.getElementById("page-title"),
      copy: document.getElementById("page-copy"),
      profileTitle: document.getElementById("profile-title"),
      subtitle: document.getElementById("profile-subtitle"),
      status: document.getElementById("profile-status"),
      refreshProfile: document.getElementById("refresh-profile"),
      icon: document.getElementById("profile-icon"),
      progressLabel: document.getElementById("progress-label"),
      progressPercent: document.getElementById("progress-percent"),
      progressFill: document.getElementById("progress-fill"),
      level: document.getElementById("summoner-level"),
      matchIds: document.getElementById("match-id-count"),
      matchDetails: document.getElementById("match-detail-count"),
      jobNote: document.getElementById("job-note"),
      diagnostics: document.getElementById("profile-diagnostics"),
      matchList: document.getElementById("match-list"),
      loadMore: document.getElementById("load-more-matches"),
      raw: document.getElementById("raw-payload")
    };

    const managerEls = {
      matches: document.getElementById("manager-match-count"),
      links: document.getElementById("manager-link-count"),
      cache: document.getElementById("manager-cache-count"),
      search: document.getElementById("manager-search"),
      puuid: document.getElementById("manager-puuid"),
      fetchIds: document.getElementById("manager-fetch-ids"),
      refresh: document.getElementById("manager-refresh"),
      fetch: document.getElementById("manager-fetch"),
      force: document.getElementById("manager-force"),
      prune: document.getElementById("manager-prune"),
      message: document.getElementById("manager-message"),
      list: document.getElementById("manager-match-list"),
      raw: document.getElementById("manager-raw")
    };

    function parseRiotId(value) {
      const trimmed = value.trim();
      const hashIndex = trimmed.indexOf("#");
      if (hashIndex <= 0 || hashIndex !== trimmed.lastIndexOf("#")) {
        throw new Error("Use gameName#tagLine.");
      }
      const gameName = trimmed.slice(0, hashIndex).trim();
      const tagLine = trimmed.slice(hashIndex + 1).trim();
      if (!gameName || !tagLine) {
        throw new Error("Use gameName#tagLine.");
      }
      return { gameName, tagLine, riotId: `${gameName}#${tagLine}` };
    }

    function buildProfileSlug(gameName, tagLine) {
      return `${encodeURIComponent(gameName)}-${encodeURIComponent(tagLine)}`;
    }

    function profileApiParams(profile) {
      const params = new URLSearchParams({
        riot_id: profile.riotId,
        account_regional_route: config.defaults.accountRegionalRoute,
        platform_route: config.defaults.platformRoute,
        regional_route: config.defaults.regionalRoute
      });
      return params.toString();
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, options);
      const text = await response.text();
      const data = text ? JSON.parse(text) : null;
      if (!response.ok) {
        const error = new Error(
          data && data.detail ? data.detail : `Request failed: ${response.status}`
        );
        error.status = response.status;
        error.data = data;
        throw error;
      }
      return data;
    }

    async function loadDataDragonVersion() {
      try {
        const versions = await requestJson("https://ddragon.leagueoflegends.com/api/versions.json");
        if (Array.isArray(versions) && versions[0]) {
          state.dataDragonVersion = versions[0];
        }
      } catch {
        // Static images are helpful, not required for the experimental shell.
      }
    }

    function profileIconUrl(iconId) {
      return `https://ddragon.leagueoflegends.com/cdn/${state.dataDragonVersion}/img/profileicon/${iconId}.png`;
    }

    function championIconUrl(championName) {
      return `https://ddragon.leagueoflegends.com/cdn/${state.dataDragonVersion}/img/champion/${championName}.png`;
    }

    function setStatus(text, tone = "") {
      els.status.textContent = text;
      if (tone) {
        els.status.dataset.tone = tone;
      } else {
        delete els.status.dataset.tone;
      }
    }

    function setProgress(percent, label) {
      const clamped = Math.max(0, Math.min(100, Number(percent) || 0));
      els.progressFill.style.width = `${clamped}%`;
      els.progressPercent.textContent = `${Math.round(clamped)}%`;
      els.progressLabel.textContent = label;
    }

    function displayCount(value) {
      const numberValue = Number(value);
      return Number.isFinite(numberValue) && numberValue > 0 ? String(numberValue) : "-";
    }

    function renderMatchStats({ matchIds = [], progress = {}, pagination = null } = {}) {
      const discovered = progress.unique_match_ids
        || progress.match_ids_discovered
        || (Array.isArray(matchIds) ? matchIds.length : 0);
      const fetched = progress.matches_fetched
        || (pagination && pagination.total)
        || 0;
      els.matchIds.textContent = displayCount(discovered);
      els.matchDetails.textContent = displayCount(fetched);
    }

    function renderCachedProfile(cached) {
      state.cachedProfile = cached;
      const account = cached.account || {};
      const summoner = cached.summoner || {};
      const matchIds = cached.match_ids || [];
      if (account.puuid) {
        state.profilePuuid = account.puuid;
      }
      const title = account.gameName && account.tagLine
        ? `${account.gameName}#${account.tagLine}`
        : config.profile.riotId;

      els.profileTitle.textContent = title;
      els.subtitle.textContent = account.puuid ? `PUUID ${account.puuid}` : "Account data cached.";
      els.level.textContent = summoner.summonerLevel || "-";
      renderMatchStats({ matchIds });
      els.raw.textContent = JSON.stringify(
        { cachedProfile: cached, jobResult: state.jobResult },
        null,
        2
      );

      if (summoner.profileIconId || summoner.profileIconId === 0) {
        els.icon.src = profileIconUrl(summoner.profileIconId);
        els.icon.hidden = false;
      }
    }

    function latestEvent(events) {
      if (!Array.isArray(events) || !events.length) {
        return null;
      }
      return events[events.length - 1];
    }

    function renderJob(job, options = {}) {
      state.jobResult = job;
      const estimate = job.estimate || {};
      const progress = job.progress || job.summary || {};
      const percent = estimate.percent_complete ?? 0;
      const event = latestEvent(job.events);
      const wait = job.current_wait;
      if (job.account && job.account.puuid) {
        state.profilePuuid = job.account.puuid;
      } else if (job.details && job.details.puuid) {
        state.profilePuuid = job.details.puuid;
      }

      if (job.status === "failed") {
        const message = (job.error && job.error.message) || "Profile job failed.";
        setStatus("Failed", "error");
        setProgress(percent, message);
        els.jobNote.textContent = message;
      } else if (job.status === "succeeded") {
        setStatus("Ready", "ready");
        setProgress(100, "Profile fetch completed.");
        els.jobNote.textContent = "Profile data is ready.";
      } else if (wait) {
        setStatus("Rate limited", "wait");
        setProgress(percent, wait.message || "Waiting for Riot rate limit capacity.");
        els.jobNote.textContent = wait.resume_at
          ? `Resumes at ${new Date(wait.resume_at).toLocaleTimeString()}.`
          : wait.message;
      } else {
        setStatus(job.status || "Running", "");
        setProgress(percent, estimate.description || "Profile job is running.");
        els.jobNote.textContent = event
          ? event.message
          : estimate.description || "Profile job is queued.";
      }

      if (job.account && job.summoner) {
        renderCachedProfile({
          identity_status: "job_result",
          account: job.account,
          summoner: job.summoner,
          match_ids: job.match_ids || []
        });
      }

      const matches = job.matches || (job.result && job.result.matches) || {};
      const matchIds = job.match_ids || (job.result && job.result.match_ids) || [];
      renderMatchStats({ matchIds, progress });
      if (options.renderMatchList !== false) {
        renderMatches(matches, matchIds, progress);
      }
      els.raw.textContent = JSON.stringify({ cachedProfile: state.cachedProfile, job }, null, 2);
    }

    function orderedMatchEntries(matches, matchIds) {
      const matchMap = matches || {};
      const orderedIds = Array.isArray(matchIds) ? matchIds : [];
      const usedIds = new Set();
      const orderedEntries = [];

      for (const matchId of orderedIds) {
        if (matchMap[matchId]) {
          orderedEntries.push([matchId, matchMap[matchId]]);
          usedIds.add(matchId);
        }
      }

      for (const entry of Object.entries(matchMap)) {
        if (!usedIds.has(entry[0])) {
          orderedEntries.push(entry);
        }
      }

      return orderedEntries;
    }

    function actionButton(label, handler, options = {}) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "action-button";
      button.textContent = label;
      if (options.danger) {
        button.dataset.danger = "true";
      }
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await handler();
        } catch (error) {
          els.jobNote.textContent = error.message;
          button.disabled = false;
        }
      });
      return button;
    }

    async function fetchManagedMatches(matchIds, forceUpstream = false, puuid = null) {
      return requestJson("/manager/api/matches/fetch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          match_ids: matchIds,
          regional_route: config.defaults.regionalRoute,
          puuid,
          force_upstream: forceUpstream
        })
      });
    }

    async function refreshCurrentProfile() {
      if (!config.profile) {
        return;
      }
      renderProfileView(await fetchProfileView(config.profile));
    }

    function profileMatchActions(matchId) {
      const actions = document.createElement("div");
      actions.className = "actions";
      actions.append(
        actionButton("Fetch", async () => {
          const result = await fetchManagedMatches([matchId], false, state.profilePuuid);
          els.jobNote.textContent = JSON.stringify(result);
          await refreshCurrentProfile();
        }),
        actionButton("Force", async () => {
          const result = await fetchManagedMatches([matchId], true, state.profilePuuid);
          els.jobNote.textContent = JSON.stringify(result);
          await refreshCurrentProfile();
        }),
        actionButton("Evict cache", async () => {
          if (!window.confirm(`Evict the Match-V5 cache entry for ${matchId}?`)) return;
          const result = await requestJson(
            `/manager/api/cache/matches/${encodeURIComponent(matchId)}?regional_route=${config.defaults.regionalRoute}`,
            { method: "DELETE" }
          );
          els.jobNote.textContent = JSON.stringify(result);
        }),
        actionButton("Unlink player", async () => {
          if (!state.profilePuuid) throw new Error("Profile PUUID is not available.");
          const message = [
            `Remove only this player's link to ${matchId}?`,
            "The durable match stays available."
          ].join(" ");
          if (!window.confirm(message)) return;
          const result = await requestJson(
            `/manager/api/players/${encodeURIComponent(state.profilePuuid)}/matches/${encodeURIComponent(matchId)}`,
            { method: "DELETE" }
          );
          els.jobNote.textContent = JSON.stringify(result);
          await refreshCurrentProfile();
        }, { danger: true }),
        actionButton("Delete DB", async () => {
          const message = [
            `Delete ${matchId} globally from durable storage`,
            "and unlink every player?"
          ].join(" ");
          if (!window.confirm(message)) return;
          const result = await requestJson(
            `/manager/api/matches/${encodeURIComponent(matchId)}`,
            { method: "DELETE" }
          );
          els.jobNote.textContent = JSON.stringify(result);
          await refreshCurrentProfile();
        }, { danger: true }),
        actionButton("Delete both", async () => {
          const message = [
            `Delete ${matchId} globally from cache and durable storage?`,
            "This unlinks every player."
          ].join(" ");
          if (!window.confirm(message)) return;
          const result = await requestJson(
            `/manager/api/matches/${encodeURIComponent(matchId)}?include_cache=true&regional_route=${config.defaults.regionalRoute}`,
            { method: "DELETE" }
          );
          els.jobNote.textContent = JSON.stringify(result);
          await refreshCurrentProfile();
        }, { danger: true })
      );
      return actions;
    }

    function renderMatches(matches, matchIds, progress) {
      const entries = orderedMatchEntries(matches, matchIds);
      renderMatchStats({
        matchIds,
        progress: { ...progress, matches_fetched: progress.matches_fetched || entries.length }
      });
      els.matchList.replaceChildren();

      for (const [matchId, match] of entries) {
        const participant = findCurrentParticipant(match);
        const card = document.createElement("article");
        card.className = "match-card";

        const img = document.createElement("img");
        img.className = "champion-icon";
        img.alt = "";
        if (participant && participant.championName) {
          img.src = championIconUrl(participant.championName);
        } else {
          img.hidden = true;
        }

        const body = document.createElement("div");
        const heading = document.createElement("h3");
        heading.textContent = participant && participant.championName
          ? participant.championName
          : matchId;
        const copy = document.createElement("p");
        copy.textContent = matchSummary(match, participant);
        body.append(heading, copy);

        const result = document.createElement("span");
        result.className = "status-pill";
        if (participant && participant.win === true) {
          result.textContent = "Win";
          result.dataset.tone = "ready";
        } else if (participant && participant.win === false) {
          result.textContent = "Loss";
          result.dataset.tone = "error";
        } else {
          result.textContent = "Match";
        }

        body.append(profileMatchActions(matchId));
        card.append(img, body, result);
        els.matchList.append(card);
      }
    }

    function findCurrentParticipant(match) {
      const puuid = state.profilePuuid;
      const participants = match && match.info && Array.isArray(match.info.participants)
        ? match.info.participants
        : [];
      if (puuid) {
        return participants.find((participant) => participant.puuid === puuid) || null;
      }
      return participants.find((participant) => (
        participant.riotIdGameName === config.profile.gameName
        && participant.riotIdTagline === config.profile.tagLine
      )) || null;
    }

    function matchSummary(match, participant) {
      const info = match.info || {};
      const mode = info.gameMode || "League match";
      const duration = info.gameDuration
        ? `${Math.round(info.gameDuration / 60)} min`
        : "duration unknown";
      if (!participant) {
        return `${mode} - ${duration}`;
      }
      const kills = participant.kills ?? 0;
      const deaths = participant.deaths ?? 0;
      const assists = participant.assists ?? 0;
      return `${mode} - ${duration} - ${kills}/${deaths}/${assists}`;
    }

    function profileViewUrl(profile, options = {}) {
      const path = [
        "/profiles/by-riot-id",
        encodeURIComponent(profile.gameName),
        encodeURIComponent(profile.tagLine)
      ].join("/");
      const params = new URLSearchParams({
        account_regional_route: config.defaults.accountRegionalRoute,
        platform_route: config.defaults.platformRoute,
        regional_route: config.defaults.regionalRoute,
        match_start: String(options.matchStart ?? 0),
        match_limit: String(options.matchLimit ?? config.defaults.profileMatchLimit)
      });
      return `${path}?${params.toString()}`;
    }

    async function fetchProfileView(profile, options = {}) {
      return requestJson(profileViewUrl(profile, options));
    }

    async function startProfileJob(profile) {
      return requestJson(`/profiles/fetch?${profileApiParams(profile)}`, { method: "POST" });
    }

    function refreshLockKey(profile) {
      return `league-profile-refresh:${profile.riotId.toLocaleLowerCase()}`;
    }

    function refreshLockedUntil(profile) {
      try {
        return Number(window.localStorage.getItem(refreshLockKey(profile))) || 0;
      } catch (_) {
        return 0;
      }
    }

    function lockProfileRefresh(profile) {
      const lockedUntil = Date.now() + PROFILE_REFRESH_LOCKOUT_MS;
      try {
        window.localStorage.setItem(refreshLockKey(profile), String(lockedUntil));
      } catch (_) {
        // The in-memory countdown still prevents repeated clicks in this page.
      }
      return lockedUntil;
    }

    function renderRefreshButton(profile) {
      window.clearTimeout(state.refreshLockTimer);
      state.refreshLockTimer = null;
      const jobActive = ["populating", "refreshing"].includes(state.profileLifecycleState);
      const secondsRemaining = Math.max(
        0,
        Math.ceil((refreshLockedUntil(profile) - Date.now()) / 1000)
      );
      els.refreshProfile.disabled = jobActive || secondsRemaining > 0;
      els.refreshProfile.textContent = jobActive
        ? "Refresh in progress"
        : secondsRemaining > 0
          ? `Refresh profile (${secondsRemaining}s)`
          : "Refresh profile";
      if (secondsRemaining > 0) {
        state.refreshLockTimer = window.setTimeout(() => renderRefreshButton(profile), 1000);
      }
    }

    async function queueProfileRefresh(profile) {
      if (refreshLockedUntil(profile) > Date.now()) {
        renderRefreshButton(profile);
        return null;
      }
      lockProfileRefresh(profile);
      renderRefreshButton(profile);
      const job = await startProfileJob(profile);
      if (!job.job_id) {
        throw new Error("Profile job did not return a job id.");
      }
      state.jobId = job.job_id;
      const nextState = state.profileLifecycleState === "missing" ? "populating" : "refreshing";
      state.profileLifecycleState = nextState;
      setStatus(
        job.identity_status === "already_running"
          ? "Refresh in progress"
          : nextState === "populating"
            ? "Populating profile"
            : "Refreshing profile"
      );
      renderRefreshButton(profile);
      return job;
    }

    function renderCompactMatches(matches, options = {}) {
      if (!options.append) {
        els.matchList.replaceChildren();
      }

      for (const match of matches) {
        const card = document.createElement("article");
        card.className = "match-card";

        const img = document.createElement("img");
        img.className = "champion-icon";
        img.alt = "";
        if (match.champion_name) {
          img.src = championIconUrl(match.champion_name);
        } else {
          img.hidden = true;
        }

        const body = document.createElement("div");
        const heading = document.createElement("h3");
        heading.textContent = match.champion_name || match.match_id;
        const copy = document.createElement("p");
        const duration = match.game_duration
          ? `${Math.round(match.game_duration / 60)} min`
          : "duration unknown";
        const score = [
          match.kills ?? 0,
          match.deaths ?? 0,
          match.assists ?? 0
        ].join("/");
        copy.textContent = `${match.game_mode || "League match"} - ${duration} - ${score}`;
        body.append(heading, copy, profileMatchActions(match.match_id));

        const result = document.createElement("span");
        result.className = "status-pill";
        if (match.win === true) {
          result.textContent = "Win";
          result.dataset.tone = "ready";
        } else if (match.win === false) {
          result.textContent = "Loss";
          result.dataset.tone = "error";
        } else {
          result.textContent = "Match";
        }

        card.append(img, body, result);
        els.matchList.append(card);
      }
    }

    function renderMatchPagination(pagination, options = {}) {
      state.matchPagination = pagination || null;
      if (options.updateStats !== false) {
        els.matchDetails.textContent = displayCount(pagination && pagination.total);
      }

      const hasMore = Boolean(
        pagination
        && pagination.has_more
        && pagination.next_start !== null
        && pagination.next_start !== undefined
      );
      els.loadMore.hidden = !hasMore;
      if (hasMore) {
        const loaded = pagination.next_start || 0;
        const remaining = Math.max(0, (pagination.total || 0) - loaded);
        els.loadMore.textContent = remaining > 0
          ? `Load more matches (${remaining} left)`
          : "Load more matches";
        els.loadMore.disabled = false;
      }
    }

    function renderProfileView(view, options = {}) {
      const profile = view.profile || {};
      const lifecycle = view.status || {};
      const summary = view.data_summary || {};
      const progressSnapshot = view.progress;
      const diagnostics = view.diagnostics || {};
      state.profileLifecycleState = lifecycle.state || null;
      if (config.profile) {
        renderRefreshButton(config.profile);
      }
      const account = view.account || {};
      const compactMatches = Array.isArray(view.matches) ? view.matches : [];
      const hasCompactMatches = compactMatches.length > 0;
      const appendMatches = options.appendMatches === true;
      let renderedJobProgress = false;
      if (profile.puuid) {
        state.profilePuuid = profile.puuid;
      }
      if (view.account || view.summoner || view.match_ids) {
        renderCachedProfile({
          identity_status: lifecycle.state,
          account,
          summoner: view.summoner,
          match_ids: view.match_ids
        });
      }
      if (diagnostics.active_job) {
        state.jobId = diagnostics.active_job.job_id;
        renderJob(diagnostics.active_job, {
          renderMatchList: !hasCompactMatches && !appendMatches
        });
        renderedJobProgress = true;
      }

      if (lifecycle.state === "populating") {
        setStatus("Populating profile", "wait");
      } else if (lifecycle.state === "refreshing") {
        setStatus("Refreshing profile", "wait");
      } else if (lifecycle.state === "missing") {
        setStatus("Not cached");
        setProgress(5, "No cached profile data yet.");
      } else if (lifecycle.state === "ready") {
        setStatus("Ready", "ready");
        setProgress(100, "Profile data is ready.");
      } else if (lifecycle.state === "failed") {
        setStatus("Failed", "error");
        setProgress(0, lifecycle.stage_description || lifecycle.message || "Profile fetch failed.");
      }
      if (progressSnapshot) {
        const estimate = progressSnapshot.estimate || {};
        setProgress(
          estimate.percent_complete ?? 0,
          lifecycle.stage_description || estimate.description || lifecycle.message
        );
      }
      const availability = [
        summary.account_available ? "account" : null,
        summary.summoner_available ? "summoner" : null,
        summary.match_ids_available ? "match IDs" : null,
        summary.match_details_available ? "match details" : null
      ].filter(Boolean);
      const statusMessage = lifecycle.message || "Profile status unavailable";
      const availableMessage = availability.join(", ") || "none";
      els.jobNote.textContent = `${statusMessage} Available: ${availableMessage}.`;
      renderMatchStats({
        matchIds: view.match_ids,
        progress: {
          unique_match_ids: summary.unique_match_ids,
          matches_fetched: summary.matches_available
        },
        pagination: view.matches_pagination
      });
      if (Array.isArray(view.matches)) {
        renderCompactMatches(view.matches, { append: appendMatches });
      }
      renderMatchPagination(view.matches_pagination, { updateStats: !renderedJobProgress });
      els.diagnostics.textContent = JSON.stringify(diagnostics, null, 2);
      els.raw.textContent = JSON.stringify(view, null, 2);
    }

    async function pollProfileView(profile) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
      const tick = async () => {
        try {
          const view = await fetchProfileView(profile);
          renderProfileView(view);
          if (["populating", "refreshing"].includes(view.status && view.status.state)) {
            state.pollTimer = window.setTimeout(tick, 1800);
          }
        } catch (error) {
          setStatus("Polling failed", "error");
          els.jobNote.textContent = error.message;
        }
      };
      await tick();
    }

    async function loadProfile(profile) {
      els.body.dataset.page = "profile";
      els.input.value = profile.riotId;
      els.title.textContent = profile.riotId;
      els.copy.textContent = [
        "Checking cached profile data first,",
        "then filling in missing pieces from the background job."
      ].join(" ");
      els.profileTitle.textContent = profile.riotId;
      setStatus("Checking cache");
      setProgress(5, "Looking for cached profile data.");

      try {
        const initialView = await fetchProfileView(profile);
        renderProfileView(initialView);
        const lifecycle = initialView.status || {};
        const dataSummary = initialView.data_summary || {};
        const refreshDue = lifecycle.state === "ready"
          && dataSummary.refresh_after
          && new Date(dataSummary.refresh_after).getTime() <= Date.now();
        const shouldRefresh = !["populating", "refreshing"].includes(lifecycle.state)
          && (lifecycle.state === "missing" || refreshDue);
        let refreshStarted = false;
        if (shouldRefresh) {
          const job = await queueProfileRefresh(profile);
          refreshStarted = job !== null;
          if (job && (job.account || job.summoner || job.match_ids)) {
            renderCachedProfile({
              identity_status: job.identity_status,
              account: job.account || {},
              summoner: job.summoner,
              match_ids: job.match_ids
            });
          }
        }
        if (["populating", "refreshing"].includes(lifecycle.state) || refreshStarted) {
          await pollProfileView(profile);
        }
      } catch (error) {
        setStatus("Job failed", "error");
        els.jobNote.textContent = error.message;
      }
    }

    function selectedManagerMatchIds() {
      return Array.from(managerEls.list.querySelectorAll('input[type="checkbox"]:checked'))
        .map((input) => input.value);
    }

    async function inspectManagerMatch(matchId) {
      const detail = await requestJson(`/manager/api/matches/${encodeURIComponent(matchId)}`);
      managerEls.raw.textContent = JSON.stringify(detail, null, 2);
    }

    function renderManagerMatches(page) {
      managerEls.list.replaceChildren();
      for (const match of page.matches || []) {
        const row = document.createElement("article");
        row.className = "panel manager-row";
        const select = document.createElement("input");
        select.type = "checkbox";
        select.value = match.match_id;
        select.setAttribute("aria-label", `Select ${match.match_id}`);
        const body = document.createElement("div");
        const id = document.createElement("code");
        id.textContent = match.match_id;
        const details = document.createElement("p");
        details.textContent = [
          match.regional_route,
          `${match.linked_puuids.length} player link(s)`,
          `fetched ${new Date(match.fetched_at).toLocaleString()}`
        ].join(" - ");
        body.append(id, details);
        const actions = document.createElement("div");
        actions.className = "actions";
        actions.append(
          actionButton("Inspect", () => inspectManagerMatch(match.match_id)),
          actionButton("Evict cache", async () => {
            if (!window.confirm(`Evict the Match-V5 cache entry for ${match.match_id}?`)) return;
            managerEls.message.textContent = JSON.stringify(await requestJson(
              `/manager/api/cache/matches/${encodeURIComponent(match.match_id)}?regional_route=${match.regional_route}`,
              { method: "DELETE" }
            ));
            await loadManager();
          }),
          actionButton("Delete DB", async () => {
            const message = `Delete ${match.match_id} globally and unlink every player?`;
            if (!window.confirm(message)) return;
            managerEls.message.textContent = JSON.stringify(await requestJson(
              `/manager/api/matches/${encodeURIComponent(match.match_id)}`,
              { method: "DELETE" }
            ));
            await loadManager();
          }, { danger: true }),
          actionButton("Delete both", async () => {
            if (!window.confirm(`Delete ${match.match_id} from cache and durable storage?`)) return;
            managerEls.message.textContent = JSON.stringify(await requestJson(
              `/manager/api/matches/${encodeURIComponent(match.match_id)}?include_cache=true&regional_route=${match.regional_route}`,
              { method: "DELETE" }
            ));
            await loadManager();
          }, { danger: true })
        );
        row.append(select, body, actions);
        managerEls.list.append(row);
      }
      managerEls.message.textContent = [
        `${page.total} stored match(es).`,
        `Showing ${page.matches.length}.`
      ].join(" ");
    }

    async function loadManager() {
      els.body.dataset.page = "manager";
      const params = new URLSearchParams({ offset: "0", limit: "100" });
      if (managerEls.search.value.trim()) params.set("search", managerEls.search.value.trim());
      if (managerEls.puuid.value.trim()) params.set("puuid", managerEls.puuid.value.trim());
      const [summary, page] = await Promise.all([
        requestJson("/manager/api/summary"),
        requestJson(`/manager/api/matches?${params.toString()}`)
      ]);
      managerEls.matches.textContent = summary.durable_matches;
      managerEls.links.textContent = summary.player_match_links;
      managerEls.cache.textContent = summary.cache_available
        ? summary.riot_cache_entries
        : "Disabled";
      renderManagerMatches(page);
    }

    els.form.addEventListener("submit", (event) => {
      event.preventDefault();
      try {
        const profile = parseRiotId(els.input.value);
        const slug = buildProfileSlug(profile.gameName, profile.tagLine);
        window.location.assign(`/${slug}`);
      } catch (error) {
        els.message.textContent = error.message;
      }
    });

    els.refreshProfile.addEventListener("click", async () => {
      if (!config.profile) return;
      try {
        const job = await queueProfileRefresh(config.profile);
        if (job) {
          await pollProfileView(config.profile);
        }
      } catch (error) {
        setStatus("Refresh failed", "error");
        els.jobNote.textContent = error.message;
        renderRefreshButton(config.profile);
      }
    });

    els.loadMore.addEventListener("click", async () => {
      if (!config.profile || !state.matchPagination || state.matchPagination.next_start == null) {
        return;
      }
      els.loadMore.disabled = true;
      try {
        const view = await fetchProfileView(config.profile, {
          matchStart: state.matchPagination.next_start
        });
        renderProfileView(view, { appendMatches: true });
      } catch (error) {
        els.jobNote.textContent = error.message;
        els.loadMore.disabled = false;
      }
    });

    managerEls.refresh.addEventListener("click", () => {
      loadManager().catch((error) => { managerEls.message.textContent = error.message; });
    });

    managerEls.fetch.addEventListener("click", async () => {
      const typedIds = managerEls.fetchIds.value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      const matchIds = Array.from(new Set([...selectedManagerMatchIds(), ...typedIds]));
      if (!matchIds.length) {
        managerEls.message.textContent = "Select a stored match or enter at least one match ID.";
        return;
      }
      try {
        const result = await fetchManagedMatches(
          matchIds,
          managerEls.force.checked,
          managerEls.puuid.value.trim() || null
        );
        managerEls.message.textContent = `${result.succeeded} fetched, ${result.failed} failed.`;
        managerEls.raw.textContent = JSON.stringify(result, null, 2);
        await loadManager();
      } catch (error) {
        managerEls.message.textContent = error.message;
      }
    });

    managerEls.prune.addEventListener("click", async () => {
      try {
        const result = await requestJson("/manager/api/cache/prune-expired", { method: "POST" });
        managerEls.message.textContent = `Pruned ${result.pruned} expired cache entries.`;
        await loadManager();
      } catch (error) {
        managerEls.message.textContent = error.message;
      }
    });

    (async function boot() {
      await loadDataDragonVersion();
      if (config.page === "profile" && config.profile) {
        await loadProfile(config.profile);
      } else if (config.page === "manager") {
        await loadManager();
      } else {
        els.body.dataset.page = "home";
      }
    })();
  </script>
</body>
</html>
"""
