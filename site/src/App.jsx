import React, { useEffect, useMemo, useRef, useState } from 'react';
import { HexColorInput, HexColorPicker } from 'react-colorful';
import Chart from './components/Chart.jsx';
import { getHeroDefaultColor, getHeroOriginalUrl, getHeroSourceUrl } from './lib/heroPfp.js';

const DATA_PATHS = [
  '/data/summary.json',
  '/data/hero-state.json',
  '/data/launch.json',
  '/data/market-history.json',
  '/data/floor-listings.json',
  '/data/treasury.json',
  '/data/market-daily.json',
];

const HERO_ZERO_BODY_PFP = '/assets/heroes/0-body-pfp.png';
const HERO_ZERO_FACE_PFP = '/assets/heroes/0-face-pfp.png';

const HERO_SOURCE_WIDTH = 65;
const HERO_SOURCE_HEIGHT = 70;
const HERO_FACE_CROP = { x: 20, y: 8, width: 26, height: 26 };
const HERO_BODY_OUTPUT = { width: 650, height: 700 };
const HERO_FACE_OUTPUT = { width: 780, height: 780 };
const HERO_IDENTITY_OUTPUT = { width: 52, height: 52 };
const HERO_FAVICON_OUTPUT = { width: 52, height: 52 };
const HERO_FAVICON_RADIUS = 9;
const HERO_IDENTITY_DELAY_MS = 1500;
const HERO_IDENTITY_FADE_MS = 360;
const GITHUB_REPO_URL = 'https://github.com/cjohnsongh/guild-saga-analytics';
const HERO_SOURCE_GITHUB_URL = `${GITHUB_REPO_URL}/tree/main/site/public/assets/heroes/source`;
const HERO_SOURCE_PREVIEW_IDS = Array.from({ length: 21 }, (_, index) => index);
const HERO_SOURCE_PREVIEW_GAP = 3;
const heroSourceImageCache = new Map();
const HERO_PREFERENCE_STORAGE_KEY = 'guild-saga-hero-pfp-preference-v1';
const RETURN_SNAPSHOT_STORAGE_KEY = 'guild-saga-return-snapshot-v1';
const RETURN_RECAP_SESSION_KEY = 'guild-saga-return-recap-v1';
const RETURN_SNAPSHOT_VERSION = 1;
const RETURN_RECAP_VISIBLE_LIMIT = 3;
const DATA_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const PIPELINE_FRESHNESS_URL = 'https://guild-saga-webhook-inbox.cjohnson80.workers.dev/freshness';
const PRODUCTION_HEARTBEAT_MAX_AGE_MS = 6 * 60 * 60 * 1000;
const FLOOR_HEARTBEAT_MAX_AGE_MS = 30 * 60 * 60 * 1000;

function readHeroPreference() {
  const fallback = { heroId: 0, color: getHeroDefaultColor(0) };
  try {
    const stored = window.localStorage.getItem(HERO_PREFERENCE_STORAGE_KEY);
    if (!stored) return fallback;
    const parsed = JSON.parse(stored);
    const heroId = Number(parsed?.heroId);
    if (!Number.isInteger(heroId) || heroId < 0 || heroId > 9999) return fallback;
    const rawColor = String(parsed?.color || '').trim();
    const color = /^#[0-9a-f]{6}$/i.test(rawColor)
      ? rawColor.toUpperCase()
      : getHeroDefaultColor(heroId);
    return { heroId, color };
  } catch {
    return fallback;
  }
}

function saveHeroPreference(heroId, color) {
  try {
    window.localStorage.setItem(HERO_PREFERENCE_STORAGE_KEY, JSON.stringify({ heroId, color }));
  } catch {
    // Storage can be blocked in strict/private browser modes; the site still works for the session.
  }
}

const LABYRINTHS_SLIDES = [
  { id: '00', src: '/assets/labyrinths/00.jpg', alt: 'Guild Saga: Labyrinths key art' },
  { id: '01', src: '/assets/labyrinths/01.png', alt: 'Guild Saga: Labyrinths gameplay screenshot 1' },
  { id: '02', src: '/assets/labyrinths/02.png', alt: 'Guild Saga: Labyrinths gameplay screenshot 2' },
  { id: '03', src: '/assets/labyrinths/03.png', alt: 'Guild Saga: Labyrinths gameplay screenshot 3' },
  { id: '04', src: '/assets/labyrinths/04.png', alt: 'Guild Saga: Labyrinths gameplay screenshot 4' },
  { id: '05', src: '/assets/labyrinths/05.png', alt: 'Guild Saga: Labyrinths gameplay screenshot 5' },
  { id: '06', src: '/assets/labyrinths/06.png', alt: 'Guild Saga: Labyrinths gameplay screenshot 6' },
];

const RANGE_OPTIONS = [
  { id: '1w', label: '1W', days: 7, defaultGranularity: '1d' },
  { id: '1m', label: '1M', months: 1, defaultGranularity: '1d' },
  { id: '1y', label: '1Y', months: 12, defaultGranularity: '1d' },
  { id: '3y', label: '3Y', months: 36, defaultGranularity: '1d' },
  { id: 'all', label: 'All', months: null, defaultGranularity: '1d' },
];

const GRANULARITY_OPTIONS = [
  { id: '1d', label: '1D', name: 'Daily' },
  { id: '1w', label: '1W', name: 'Weekly' },
  { id: '1m', label: '1M', name: 'Monthly' },
];

const NAV_ITEMS = [
  { id: 'overview', label: 'Overview', target: 'overview', icon: 'home' },
  { id: 'ownership', label: 'Ownership', target: 'ownership', category: 'ownership' },
  { id: 'market', label: 'Market', target: 'market', category: 'market' },
  { id: 'collection', label: 'Collection', target: 'collection', category: 'collection' },
  { id: 'economy', label: 'Funding', target: 'economy', category: 'funding' },
  { id: 'team', label: 'Team', target: 'team', category: 'team' },
];

const TEAM_MEMBERS = [
  { name: 'VultM', handle: '@vult_M', roles: 'Co-Founder · CEO · Creative Director' },
  { name: 'SolGutz', handle: '@SolanaGuts', roles: 'Co-Founder · CTO · Lead Programmer' },
  { name: 'Kimo', handle: '@asynkimo', roles: 'Game Developer' },
  { name: 'FL33TFOOTED', handle: '@fl33tfooted', roles: 'Fullstack Developer · Backend / Game Systems' },
  { name: 'Marcos', handle: '@pixelaccountant', roles: 'Lead Artist · Pixel Artist' },
  { name: 'Goulai', handle: '@godlikeng', roles: 'Writer · Narrative' },
  { name: 'Caruso', handle: '@caruso_cv', roles: 'Frontend · UI/UX · Community' },
  { name: "SOL'D", handle: '@sol_huckster', roles: 'Community Team · Playtester' },
];

const TEAM_CREDITS = [
  {
    name: 'Tim de Man',
    handle: '@demantim',
    roles: 'Composer',
    platform: 'YouTube',
    href: 'https://www.youtube.com/@demantim',
    avatarSrc: 'https://f4.bcbits.com/img/0030707558_10.jpg',
  },
  { name: 'Kato Ayaka', handle: '@ayakato22', roles: 'Character Portrait Artist · Illustrator' },
  {
    name: 'Paul Ouzounov',
    handle: '@PaulOuzounov',
    roles: 'Sound Designer',
    platform: 'YouTube',
    href: 'https://www.youtube.com/@PaulOuzounov',
    avatarSrc: '/assets/team/paul-ouzounov.png',
  },
  { name: 'CJohnson', handle: '@ounceofcrypto', roles: 'Discord Mod · Built this website' },
];

const TEAM_OFFICIAL_LINKS = [
  {
    label: 'GuildSaga.com',
    detail: 'Official Guild Saga website',
    href: 'https://guildsaga.com/',
    icon: 'https://guildsaga.com/favicon.ico',
  },
  {
    label: 'Guild Saga: Labyrinths',
    detail: 'Epic Games Store',
    href: 'https://store.epicgames.com/p/guild-saga-labyrinths-ca0f96?lang=en-US',
    icon: 'epic',
  },
  {
    label: 'Guild Saga: Vanished Worlds',
    detail: 'Steam',
    href: 'https://store.steampowered.com/app/2184350/Guild_Saga_Vanished_Worlds/',
    icon: 'https://store.steampowered.com/favicon.ico',
  },
];

const HERO_PLATFORM_GROUPS = [
  {
    id: 'trade',
    label: 'Trade',
    links: [
      { id: 'tensor', label: 'Tensor', href: 'https://www.tensor.trade/trade/guild_saga_heroes', icon: '/assets/platforms/tensor.png' },
      { id: 'magiceden', label: 'Magic Eden', href: 'https://magiceden.io/marketplace/guild_saga_heroes', icon: '/assets/platforms/magiceden.svg' },
    ],
  },
  {
    id: 'community',
    label: 'Community',
    links: [
      { id: 'discord', label: 'Discord', href: 'https://discord.gg/GuildSaga', icon: '/assets/platforms/discord.svg' },
      { id: 'x', label: 'X', href: 'https://x.com/GuildSaga', icon: '/assets/platforms/x.svg' },
    ],
  },
];


function HomeIcon() {
  return (
    <svg
      className="nav-home-icon"
      viewBox="0 0 424 424"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="currentColor"
        d="M224 5c3.296 2.577 6.444 5.282 9.586 8.043 3.145 2.757 6.393 5.305 9.726 7.832 4.932 3.742 9.767 7.586 14.563 11.5a2023 2023 0 0 0 32.137 25.692c4.442 3.484 8.81 7.051 13.168 10.64 3.117 2.535 6.277 5.01 9.445 7.48 5.306 4.14 10.512 8.387 15.688 12.688 6.43 5.338 12.992 10.465 19.656 15.507 5.153 3.911 10.129 7.977 15.07 12.153 3.293 2.741 6.646 5.392 10.024 8.028a743 743 0 0 1 15.027 12.058c3.449 2.82 6.928 5.6 10.41 8.379l3.832 3.07a772 772 0 0 0 6.93 5.485c11.17 8.858 11.17 8.858 12.738 16.445.748 6.52-.13 11.625-4 17-4.254 4.547-8.579 7.737-14.954 8.227-1.063-.01-2.126-.02-3.222-.032l-3.506-.02-3.63-.05-3.69-.027q-4.5-.036-8.998-.098l.008 3.257q.1 39.236.147 78.47c.016 12.65.037 25.299.071 37.948q.046 16.538.056 33.075c.004 5.838.013 11.675.035 17.513q.03 8.244.022 16.488.001 3.023.019 6.047c.015 2.756.01 5.511.003 8.268l.026 2.405c-.05 6.222-1.158 11.853-5.254 16.771l-2.008 1.633-1.992 1.68c-5.303 3.593-9.993 3.742-16.203 3.736l-2.741.02c-2.986.019-5.972.023-8.959.025l-6.243.02q-6.543.017-13.086.015c-5.578 0-11.156.027-16.735.061-4.299.022-8.598.026-12.897.025q-3.084.005-6.168.027c-2.881.02-5.762.014-8.643.002l-2.553.034c-6.652-.07-11.819-1.91-16.928-6.23l-1.352-1.665-1.398-1.648c-3.079-4.961-3.477-9.786-3.482-15.52l-.011-2.215q-.01-2.37-.008-4.74-.002-3.767-.03-7.535-.067-10.708-.077-21.418c-.005-4.377-.03-8.754-.065-13.131q-.015-2.484-.007-4.967c.044-14.361-1.586-27.27-11.547-38.451-9.175-8.868-20.084-12.428-32.68-12.273-11.086.913-20.637 5.394-28.351 13.375-7.26 8.705-10.11 18.209-10.127 29.407l-.015 2.24q-.016 2.398-.026 4.795a1935 1935 0 0 1-.053 7.607c-.062 7.207-.116 14.415-.15 21.622q-.032 6.623-.1 13.245a772 772 0 0 0-.026 5.029 748 748 0 0 1-.055 7.056l.009 2.07c-.101 5.93-1.438 11.44-5.293 16.07l-2.008 1.632-1.992 1.68c-5.303 3.593-9.993 3.742-16.203 3.736l-2.741.02c-2.986.019-5.972.023-8.959.025l-6.243.02q-6.543.017-13.086.015c-5.578 0-11.156.027-16.735.061-4.299.022-8.598.026-12.897.025q-3.084.005-6.168.027c-2.881.02-5.762.014-8.643.002l-2.553.034c-6.652-.07-11.819-1.91-16.928-6.23l-1.352-1.665-1.398-1.648c-3.321-5.352-3.512-10.584-3.468-16.67l-.005-2.615c-.003-2.884.008-5.767.019-8.65v-6.21c-.002-5.615.01-11.23.024-16.845.013-5.87.014-11.74.016-17.61.006-11.113.023-22.226.043-33.34.022-12.652.033-25.305.043-37.958q.032-39.04.101-78.079l-2.26.063c-3.413.084-6.826.136-10.24.187l-3.555.102c-8.11.09-13.65-.646-19.968-6.098C2.977 182.998 1.11 178.08 2 170c2.652-8.235 7.932-13.268 14.625-18.312l3.758-2.895 1.864-1.432c2.457-1.907 4.856-3.88 7.253-5.861a609 609 0 0 1 12.188-9.75A925 925 0 0 0 57 119.5a892 892 0 0 1 15-12c7.161-5.593 14.2-11.331 21.243-17.07a1008 1008 0 0 1 14.913-11.903 1306 1306 0 0 0 20.882-16.69A801 801 0 0 1 142 51.5c7.161-5.593 14.2-11.331 21.243-17.07 7.909-6.435 15.92-12.738 23.955-19.014a776 776 0 0 0 5.376-4.271L196 8.438l3.063-2.442C206.733.784 215.957.272 224 5"
      />
    </svg>
  );
}

function CategoryIcon({ category, className = '' }) {
  return (
    <span
      className={`category-icon ${className}`.trim()}
      data-category={category}
      aria-hidden="true"
    />
  );
}

function SectionHeading({ category, children, id }) {
  return (
    <section className="section-heading">
      <div className="section-heading-title">
        <CategoryIcon category={category} className="section-category-icon" />
        <h2 id={id}>{children}</h2>
      </div>
    </section>
  );
}

const COLORS = {
  text: '#eeeeee',
  muted: '#aaa9b7',
  faint: '#777682',
  grid: '#29292f',
  axis: '#50505a',
  accent: '#668a95',
  accentSoft: '#789aa3',
  accentDark: '#45636b',
  line: '#668a95',
  line2: '#9a865f',
  bar: '#668a95',
  barSoft: '#789aa3',
  live: '#49c879',
};

const CHART_COLORS = {
  market: '#668a95',
  volume: '#3f626b',
  ownershipHolders: '#668a95',
  ownershipSupply: '#45636b',
  staking: '#668a95',
  mint: '#668a95',
  wallet: '#668a95',
  resale: '#668a95',
  resaleDark: '#45636b',
  burn: '#668a95',
  royalties: '#668a95',
  treasury: '#668a95',
  solBars: '#668a95',
  usdcBars: '#668a95',
};

const RARITY_COLORS = {
  Bronze: '#956639',
  Silver: '#757c9b',
  Gold: '#fbd364',
  Elven: '#faebc8',
  Arcane: '#8041a0',
};

const ECONOMY_COLORS = {
  sold: '#ef7b7d',
  bought: '#8ff28b',
};

function fetchJson(path) {
  // Revalidate published JSON rather than trusting a potentially stale browser
  // cache. Unchanged files can still reuse their cached body after validation.
  return fetch(path, { cache: 'no-cache' }).then((response) => {
    if (!response.ok) throw new Error(`${path}: ${response.status}`);
    return response.json();
  });
}

function loadPublishedData() {
  return Promise.all(DATA_PATHS.map(fetchJson))
    .then(([summary, hero, launch, market, floor, treasury, dailyMarket]) => ({
      summary, hero, launch, market, floor, treasury, dailyMarket,
    }));
}

async function loadPipelineFreshness() {
  const response = await fetch(PIPELINE_FRESHNESS_URL, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Pipeline freshness: ${response.status}`);
  const payload = await response.json();
  if (!payload || payload.ok !== true) throw new Error('Pipeline freshness returned an invalid response.');
  return payload;
}

function formatInt(value) {
  return Number(value ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function formatDecimal(value, maximumFractionDigits = 2) {
  return Number(value ?? 0).toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits,
  });
}

function formatSol(value, maximumFractionDigits = 2) {
  return `${formatDecimal(value, maximumFractionDigits)} SOL`;
}

function formatPercent(value, digits = 1) {
  return `${Number(value ?? 0).toLocaleString('en-US', { maximumFractionDigits: digits })}%`;
}

function formatAxisDecimal(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '';
  return n.toFixed(3).replace(/\.?0+$/, '');
}

function formatUpdatedUtc(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value ?? '');
  const datePart = date.toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
  });
  const timePart = date.toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC',
  });
  return `${datePart} · ${timePart} UTC`;
}

function localDateKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function formatDataThrough(value) {
  if (!value) return 'Unavailable';
  const text = String(value);
  let date;
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    const [year, month, day] = text.split('-').map(Number);
    date = new Date(year, month - 1, day);
  } else {
    date = new Date(value);
  }
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatFreshnessDate(value) {
  if (!value) return 'Unavailable';
  const text = String(value);
  let date;
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    const [year, month, day] = text.split('-').map(Number);
    date = new Date(year, month - 1, day);
  } else {
    date = new Date(value);
  }
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function toUtcDateKey(value) {
  if (!value) return null;
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString().slice(0, 10);
}

function utcDateKeyToDay(value) {
  if (!value) return null;
  const [year, month, day] = value.split('-').map(Number);
  if (![year, month, day].every(Number.isFinite)) return null;
  return Math.floor(Date.UTC(year, month - 1, day) / 86400000);
}

function formatLocalDateTime(value) {
  if (!value) return 'Awaiting first verified check';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
    timeZoneName: 'short',
  });
}

function heartbeatAgeMs(now, value) {
  if (!value) return null;
  const then = new Date(value);
  if (Number.isNaN(then.getTime())) return null;
  return Math.max(0, now.getTime() - then.getTime());
}

function floorSnapshotFallbackTimestamp(value) {
  const dateKey = toUtcDateKey(value);
  return dateKey ? `${dateKey}T23:30:00Z` : null;
}

function getFreshnessStatus(data, verifiedAt = new Date(), pipelineFreshness = null) {
  const fallbackCheckedAt = verifiedAt == null
    ? new Date()
    : verifiedAt instanceof Date
      ? verifiedAt
      : new Date(verifiedAt);
  const safeFallbackCheckedAt = Number.isNaN(fallbackCheckedAt.getTime()) ? new Date() : fallbackCheckedAt;
  const serverCheckedAt = pipelineFreshness?.checked_at ? new Date(pipelineFreshness.checked_at) : null;
  const checkedAt = serverCheckedAt && !Number.isNaN(serverCheckedAt.getTime())
    ? serverCheckedAt
    : safeFallbackCheckedAt;

  const todayUtcDay = utcDateKeyToDay(checkedAt.toISOString().slice(0, 10));
  const heroDay = utcDateKeyToDay(toUtcDateKey(data?.hero?.as_of));
  const floorDay = utcDateKeyToDay(toUtcDateKey(data?.floor?.as_of));
  const heroAgeDays = heroDay == null || todayUtcDay == null ? null : todayUtcDay - heroDay;
  const floorAgeDays = floorDay == null || todayUtcDay == null ? null : todayUtcDay - floorDay;
  const bothOverMonthOld = heroAgeDays != null && floorAgeDays != null
    && heroAgeDays > 30 && floorAgeDays > 30;

  const productionHeartbeat = pipelineFreshness?.production_last_success_at || null;
  const floorHeartbeat = pipelineFreshness?.floor_listings_last_success_at || null;
  const productionAge = heartbeatAgeMs(checkedAt, productionHeartbeat);
  const floorHeartbeatAge = heartbeatAgeMs(checkedAt, floorHeartbeat);

  // Missing/unreachable heartbeat information is intentionally not treated as a
  // failure. The UI stays green until a successful public freshness response
  // gives us enough evidence to prove that a pipeline is actually late.
  const productionLate = productionAge != null && productionAge > PRODUCTION_HEARTBEAT_MAX_AGE_MS;
  const floorLate = floorHeartbeatAge != null && floorHeartbeatAge > FLOOR_HEARTBEAT_MAX_AGE_MS;
  const tone = bothOverMonthOld
    ? 'critical'
    : productionLate || floorLate
      ? 'warning'
      : 'healthy';

  const displayDate = productionHeartbeat
    ? localDateKey(productionHeartbeat)
    : data?.hero?.as_of
      ? localDateKey(data.hero.as_of)
      : localDateKey(safeFallbackCheckedAt);

  return {
    tone,
    displayDate,
    productionHeartbeat,
    floorHeartbeat,
    productionLate,
    floorLate,
  };
}

function FreshnessChip({ data, verifiedAt, pipelineFreshness }) {
  const status = getFreshnessStatus(data, verifiedAt, pipelineFreshness);
  const toneLabel = status.tone === 'healthy'
    ? 'Current'
    : status.tone === 'critical'
      ? 'Data is over a month out of date'
      : 'One or more data sources are behind schedule';
  const heartbeatTooltip = [
    `Most stats: ${formatLocalDateTime(status.productionHeartbeat || data?.hero?.as_of)}`,
    `Daily floor/listings: ${formatLocalDateTime(status.floorHeartbeat || floorSnapshotFallbackTimestamp(data?.floor?.as_of))}`,
  ].join('\n');

  return (
    <div className={`freshness-chip is-${status.tone}`} aria-label={`${toneLabel}. Updated ${formatFreshnessDate(status.displayDate)}. ${heartbeatTooltip.replace('\n', '. ')}`}>
      <i aria-hidden="true" title={heartbeatTooltip} />
      <span>Updated {formatFreshnessDate(status.displayDate)}</span>
    </div>
  );
}


function safeStorageRead(storage, key) {
  try {
    const value = storage?.getItem(key);
    return value ? JSON.parse(value) : null;
  } catch {
    return null;
  }
}

function safeStorageWrite(storage, key, value) {
  try {
    storage?.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

function safeStorageRemove(storage, key) {
  try {
    storage?.removeItem(key);
  } catch {
    // Storage can be disabled; the recap simply stays unavailable.
  }
}

function latestTreasuryTotals(treasury) {
  const rows = Array.isArray(treasury?.conversion_history) ? treasury.conversion_history : [];
  const last = rows.length ? rows[rows.length - 1] : null;
  return {
    totalSolSold: Number(last?.total_sol_sold ?? 0),
    totalUsdcPurchased: Number(last?.total_usdc_purchased ?? 0),
  };
}

function buildReturnSnapshot(data, visitedAt = new Date().toISOString()) {
  const hero = data?.hero?.kpis || {};
  const market = data?.market?.kpis || {};
  const floor = data?.floor?.kpis || {};
  const treasury = latestTreasuryTotals(data?.treasury);

  return {
    version: RETURN_SNAPSHOT_VERSION,
    visitedAt,
    dataAsOf: {
      hero: data?.hero?.as_of || null,
      market: data?.market?.as_of || null,
      floor: data?.floor?.as_of || null,
    },
    metrics: {
      activeSupply: Number(hero.active_supply ?? 0),
      holders: Number(hero.beneficial_holders ?? 0),
      staked: Number(hero.staked_heroes ?? 0),
      burned: Number(hero.burned ?? 0),
      secondarySales: Number(market.secondary_sales ?? 0),
      secondaryVolumeSol: Number(market.secondary_volume_sol ?? 0),
      uniqueBuyers: Number(market.unique_buyers ?? 0),
      heroesEverSold: Number(market.heroes_ever_sold ?? 0),
      royaltiesSol: Number(market.guild_saga_royalties_sol ?? 0),
      floorSol: Number(floor.floor_sol ?? 0),
      listedCount: Number(floor.listed_count ?? 0),
      totalSolSold: treasury.totalSolSold,
      totalUsdcPurchased: treasury.totalUsdcPurchased,
    },
  };
}

function returnSnapshotFingerprint(snapshot) {
  return JSON.stringify({
    version: snapshot?.version,
    dataAsOf: snapshot?.dataAsOf,
    metrics: snapshot?.metrics,
  });
}

function signedInt(value) {
  const n = Number(value || 0);
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}${formatInt(Math.abs(n))}`;
}

function signedDecimal(value, digits = 2) {
  const n = Number(value || 0);
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}${formatDecimal(Math.abs(n), digits)}`;
}

function buildReturnChanges(previous, current) {
  if (!previous || previous.version !== RETURN_SNAPSHOT_VERSION || !previous.metrics) return [];
  const before = previous.metrics;
  const after = current.metrics;
  const changes = [];
  const add = (category, section, text) => changes.push({ category, section, text });

  const burnedDelta = after.burned - before.burned;
  const supplyDelta = after.activeSupply - before.activeSupply;
  if (burnedDelta > 0) {
    add('Collection', 'collection', `${formatInt(burnedDelta)} ${burnedDelta === 1 ? 'Hero' : 'Heroes'} burned`);
  }
  if (supplyDelta !== 0 && supplyDelta !== -burnedDelta) {
    add('Collection', 'collection', `Active supply ${signedInt(supplyDelta)}`);
  }

  if (Number.isFinite(before.floorSol) && Number.isFinite(after.floorSol) && before.floorSol !== after.floorSol) {
    const pct = before.floorSol > 0 ? ((after.floorSol - before.floorSol) / before.floorSol) * 100 : null;
    const pctText = Number.isFinite(pct) ? ` (${signedDecimal(pct, 1)}%)` : '';
    add('Market', 'market', `Floor ${formatDecimal(before.floorSol, 3)} → ${formatDecimal(after.floorSol, 3)} SOL${pctText}`);
  }

  const salesDelta = after.secondarySales - before.secondarySales;
  const volumeDelta = after.secondaryVolumeSol - before.secondaryVolumeSol;
  if (salesDelta > 0 || volumeDelta > 0.0005) {
    const parts = [];
    if (salesDelta > 0) parts.push(`${formatInt(salesDelta)} ${salesDelta === 1 ? 'sale' : 'sales'}`);
    if (volumeDelta > 0.0005) parts.push(`${formatDecimal(volumeDelta, 2)} SOL volume`);
    add('Market', 'market', parts.join(' · '));
  }

  const listingsDelta = after.listedCount - before.listedCount;
  if (listingsDelta !== 0) {
    add('Market', 'market', `Listings ${formatInt(before.listedCount)} → ${formatInt(after.listedCount)} (${signedInt(listingsDelta)})`);
  }

  const stakedDelta = after.staked - before.staked;
  if (stakedDelta !== 0) {
    add('Ownership', 'ownership', `${formatInt(Math.abs(stakedDelta))} ${stakedDelta > 0 ? 'more' : 'fewer'} ${Math.abs(stakedDelta) === 1 ? 'Hero' : 'Heroes'} staked`);
  }

  const holdersDelta = after.holders - before.holders;
  if (holdersDelta !== 0) add('Ownership', 'ownership', `Holders ${signedInt(holdersDelta)}`);

  const firstSaleDelta = after.heroesEverSold - before.heroesEverSold;
  if (firstSaleDelta > 0) {
    add('Market', 'market', `${formatInt(firstSaleDelta)} ${firstSaleDelta === 1 ? 'Hero sold' : 'Heroes sold'} for the first time`);
  }

  const buyerDelta = after.uniqueBuyers - before.uniqueBuyers;
  if (buyerDelta > 0) add('Market', 'market', `Unique buyers +${formatInt(buyerDelta)}`);

  const royaltiesDelta = after.royaltiesSol - before.royaltiesSol;
  if (royaltiesDelta > 0.0005) add('Funding', 'economy', `+${formatDecimal(royaltiesDelta, 2)} SOL royalties`);

  const solSoldDelta = after.totalSolSold - before.totalSolSold;
  const usdcDelta = after.totalUsdcPurchased - before.totalUsdcPurchased;
  if (solSoldDelta > 0.0005 || usdcDelta > 0.005) {
    const parts = [];
    if (solSoldDelta > 0.0005) parts.push(`+${formatDecimal(solSoldDelta, 3)} SOL sold`);
    if (usdcDelta > 0.005) parts.push(`+${formatDecimal(usdcDelta, 2)} USDC purchased`);
    add('Funding', 'economy', parts.join(' · '));
  }

  return changes;
}

function formatRecapSinceShort(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const now = new Date();
  const sameDay = date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();
  if (sameDay) {
    return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  }
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatRecapVisit(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

function rangeStartDate(rangeId, endValue, earliestValue) {
  if (rangeId === 'all') return earliestValue;
  const range = RANGE_OPTIONS.find((item) => item.id === rangeId);
  if (!range) return earliestValue;
  const end = new Date(`${String(endValue).slice(0, 10)}T00:00:00Z`);
  const start = new Date(end);
  if (range.days) start.setUTCDate(start.getUTCDate() - range.days);
  else if (range.months) start.setUTCMonth(start.getUTCMonth() - range.months);
  else return earliestValue;
  const earliest = new Date(`${String(earliestValue).slice(0, 10)}T00:00:00Z`);
  return (start < earliest ? earliest : start).toISOString().slice(0, 10);
}

function mondayOf(dateValue) {
  const date = new Date(`${String(dateValue).slice(0, 10)}T00:00:00Z`);
  const day = date.getUTCDay();
  const delta = day === 0 ? -6 : 1 - day;
  date.setUTCDate(date.getUTCDate() + delta);
  return date.toISOString().slice(0, 10);
}

function monthOf(dateValue) {
  return `${String(dateValue).slice(0, 7)}-01`;
}

function aggregateFloorRows(rows, granularity) {
  if (granularity === '1d') {
    return rows.map((row) => ({
      date: row.snapshot_date,
      floor_sol: Number(row.floor_sol),
      listed_count: Number(row.listed_count),
    }));
  }

  const groups = new Map();
  rows.forEach((row) => {
    const key = granularity === '1m' ? monthOf(row.snapshot_date) : mondayOf(row.snapshot_date);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });

  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, values]) => {
      const ordered = [...values].sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date));
      const last = ordered.at(-1);
      return {
        date,
        floor_sol: Number(last?.floor_sol ?? 0),
        listed_count: Number(last?.listed_count ?? 0),
      };
    });
}

function aggregateMarketRows(rows, granularity) {
  const groups = new Map();
  rows.forEach((row) => {
    const key = granularity === '1d'
      ? row.date
      : granularity === '1m'
        ? monthOf(row.date)
        : mondayOf(row.date);
    const current = groups.get(key) || { date: key, sales: 0, volume_sol: 0 };
    current.sales += Number(row.sales || 0);
    current.volume_sol += Number(row.volume_sol || 0);
    groups.set(key, current);
  });
  return [...groups.values()].sort((a, b) => a.date.localeCompare(b.date));
}

function niceStep(raw) {
  if (!Number.isFinite(raw) || raw <= 0) return 1;
  const exponent = Math.floor(Math.log10(raw));
  const magnitude = 10 ** exponent;
  const fraction = raw / magnitude;
  const niceFraction = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
  return niceFraction * magnitude;
}

function axisScale(values, { zeroBase = false, integer = false, tickCount = 5 } = {}) {
  const finite = values.map(Number).filter(Number.isFinite);
  if (!finite.length) return { min: 0, max: 1, interval: 0.2 };
  const dataMin = Math.min(...finite);
  const dataMax = Math.max(...finite);
  const span = Math.max(dataMax - dataMin, Math.abs(dataMax) * 0.08, integer ? 5 : 0.01);
  let interval = niceStep(span / tickCount);
  if (integer) interval = Math.max(1, Math.round(interval));
  let min = zeroBase ? 0 : Math.floor((dataMin - span * 0.08) / interval) * interval;
  if (!zeroBase && dataMin >= 0) min = Math.max(0, min);
  let max = dataMax > 0 ? dataMax * 1.008 : dataMax + interval * 0.05;
  if (max <= min) max = min + interval;
  return { min, max, interval };
}

function commonTimeAxis(gridIndex, startDate, endDate, showLabels = true) {
  return {
    type: 'time',
    gridIndex,
    min: startDate,
    max: endDate,
    boundaryGap: false,
    axisLine: { show: showLabels, lineStyle: { color: COLORS.axis } },
    axisTick: { show: false },
    splitLine: { show: true, lineStyle: { color: '#202020' } },
    axisLabel: {
      show: showLabels,
      color: COLORS.muted,
      fontSize: 12,
      hideOverlap: true,
      formatter: (value) => new Date(value).toLocaleDateString('en-US', {
        month: 'short', year: 'numeric', timeZone: 'UTC',
      }),
    },
    axisPointer: {
      show: true,
      snap: false,
      lineStyle: { color: 'transparent', width: 0, opacity: 0 },
      label: { show: false },
    },
  };
}

function valueAxis(gridIndex, scale, formatter, { position = 'right', split = true, show = true } = {}) {
  return {
    type: 'value',
    gridIndex,
    position,
    show,
    min: scale.min,
    max: scale.max,
    interval: scale.interval,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: {
      color: COLORS.muted,
      fontSize: 12,
      formatter,
      showMaxLabel: false,
    },
    splitLine: { show: split, showMaxLine: false, lineStyle: { color: COLORS.grid } },
  };
}

function makeMiniFloorOption(rows) {
  const values = rows.map((row) => Number(row.floor_sol)).filter(Number.isFinite);
  const scale = axisScale(values, { zeroBase: false, tickCount: 4 });
  return {
    animation: false,
    grid: { left: 6, right: 52, top: 8, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'line', lineStyle: { color: '#777', type: 'dashed', width: 1 } },
      backgroundColor: '#171717',
      borderColor: '#444',
      textStyle: { color: '#efefef', fontSize: 13 },
      formatter: (params) => {
        const item = Array.isArray(params) ? params[0] : params;
        const date = new Date(item.axisValue).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
        const value = Array.isArray(item.value) ? item.value[1] : item.value;
        return `<strong>${date}</strong><br/>Floor ${formatSol(value, 3)}`;
      },
    },
    xAxis: {
      type: 'time',
      boundaryGap: false,
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: {
        color: COLORS.muted,
        fontSize: 12,
        hideOverlap: true,
        formatter: (value) => new Date(value).toLocaleDateString('en-US', { month: 'short', timeZone: 'UTC' }),
      },
    },
    yAxis: {
      type: 'value',
      position: 'right',
      min: scale.min,
      max: scale.max,
      interval: scale.interval,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: COLORS.muted, fontSize: 12, formatter: formatAxisDecimal, showMaxLabel: false },
      splitLine: { show: true, showMaxLine: false, lineStyle: { color: '#252525' } },
    },
    series: [{
      name: 'Floor',
      type: 'line',
      symbol: 'none',
      showSymbol: false,
      sampling: 'none',
      progressive: 0,
      connectNulls: true,
      data: rows.map((row) => [row.snapshot_date, Number(row.floor_sol)]),
      lineStyle: { width: 1.05, color: COLORS.line },
      itemStyle: { color: COLORS.line },
    }],
  };
}

function makeOwnershipOption(tiers, roundSupplyLabels = false) {
  const totalHolders = tiers.reduce((sum, row) => sum + Number(row.holder_count || 0), 0);
  const labels = tiers.map((row) => row.tier);
  const holderShare = tiers.map((row) => ({
    value: totalHolders ? (Number(row.holder_count) / totalHolders) * 100 : 0,
    count: Number(row.holder_count),
  }));
  const supplyShare = tiers.map((row) => Number(row.supply_pct || 0));
  return {
    animation: false,
    grid: { left: 52, right: 14, top: 44, bottom: 44 },
    legend: {
      top: 3,
      right: 0,
      data: ['Holders', 'Supply'],
      textStyle: { color: COLORS.muted, fontSize: 13 },
      itemWidth: 11,
      itemHeight: 9,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#171717',
      borderColor: '#444',
      textStyle: { color: '#efefef', fontSize: 13 },
      formatter: (params) => {
        const idx = params?.[0]?.dataIndex ?? 0;
        const row = tiers[idx];
        const holdersPct = totalHolders ? (Number(row.holder_count) / totalHolders) * 100 : 0;
        return `<strong>${row.tier} Heroes</strong><br/>${formatInt(row.holder_count)} holders (${formatPercent(holdersPct)})<br/>${formatPercent(row.supply_pct)} of active supply`;
      },
    },
    xAxis: {
      type: 'category',
      data: labels,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisLabel: { color: COLORS.muted, fontSize: 13 },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      interval: 20,
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { color: COLORS.muted, fontSize: 12, formatter: '{value}%' },
      splitLine: { lineStyle: { color: COLORS.grid } },
    },
    series: [
      {
        name: 'Holders',
        type: 'bar',
        data: holderShare,
        barGap: '3%',
        barCategoryGap: '5%',
        itemStyle: { color: CHART_COLORS.ownershipHolders },
        label: {
          show: true,
          position: 'top',
          color: '#d8d8d8',
          fontSize: 13,
          formatter: (p) => formatInt(p.data.count),
        },
      },
      {
        name: 'Supply',
        type: 'bar',
        data: supplyShare,
        barGap: '3%',
        barCategoryGap: '5%',
        itemStyle: { color: CHART_COLORS.ownershipSupply },
        label: {
          show: true,
          position: 'top',
          color: '#c6c5d0',
          fontSize: 13,
          formatter: (p) => formatPercent(p.value, roundSupplyLabels ? 0 : 1),
        },
      },
    ],
  };
}

function makeStakingOption(rows) {
  const labels = rows.map((row) => row.bucket);
  const values = rows.map((row) => Number(row.heroes || 0));
  const scale = axisScale(values, { zeroBase: true, integer: true, tickCount: 4 });
  return {
    animation: false,
    grid: { left: 54, right: 12, top: 30, bottom: 48 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#171717',
      borderColor: '#444',
      textStyle: { color: '#efefef', fontSize: 13 },
      formatter: (params) => {
        const idx = params?.[0]?.dataIndex ?? 0;
        const row = rows[idx];
        return `<strong>${row.bucket}</strong><br/>${formatInt(row.heroes)} Heroes`;
      },
    },
    xAxis: {
      type: 'category',
      data: labels,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisLabel: {
        color: COLORS.muted,
        fontSize: 12,
        interval: 'auto',
        hideOverlap: true,
      },
    },
    yAxis: valueAxis(0, scale, (value) => formatInt(value), { position: 'left' }),
    series: [{
      type: 'bar',
      data: values,
      barCategoryGap: '9%',
      itemStyle: { color: CHART_COLORS.staking },
      label: {
        show: true,
        position: 'top',
        color: '#d2d2d2',
        fontSize: 13,
        formatter: (p) => formatInt(p.value),
      },
    }],
  };
}

function makeTradingMarketOption(displayRows, marketRows, startDate, endDate, rangeId, granularity, currentFloor, currentListings) {
  const floorValues = displayRows.map((row) => Number(row.floor_sol));
  const listingValues = displayRows.map((row) => Number(row.listed_count));
  const floorScale = axisScale(floorValues, { zeroBase: rangeId === 'all', tickCount: 5 });
  const listingScale = axisScale(listingValues, { zeroBase: rangeId === 'all', integer: true, tickCount: 5 });
  const volumeMax = Math.max(1, ...marketRows.map((row) => Number(row.volume_sol || 0)));
  const volumeScale = { min: 0, max: Math.max(1, volumeMax * 0.34), interval: Math.max(1, volumeMax * 0.34) };
  const intervalLabel = GRANULARITY_OPTIONS.find((item) => item.id === granularity)?.name || 'Weekly';
  const barMaxWidth = granularity === '1d' ? 14 : granularity === '1m' ? 24 : 10;

  return {
    animation: false,
    backgroundColor: 'transparent',
    grid: [
      { left: 18, right: 68, top: 30, height: '42%' },
      { left: 18, right: 68, top: '55%', bottom: 44 },
    ],
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'line', snap: false, lineStyle: { color: 'transparent', width: 0, opacity: 0 } },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
      formatter: (params) => {
        const items = Array.isArray(params) ? params : [params];
        const axisValue = items.find((item) => item.axisValue)?.axisValue;
        const date = axisValue
          ? new Date(axisValue).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
          : '';
        const floor = items.find((item) => item.seriesName === 'Floor Price');
        const listings = items.find((item) => item.seriesName === 'Listings');
        const volume = items.find((item) => item.seriesName === 'Volume SOL');
        const parts = [`<strong>${date}</strong> <span style="color:#8d8c99">${intervalLabel}</span>`];
        if (floor) parts.push(`Floor ${formatSol(Array.isArray(floor.value) ? floor.value[1] : floor.value, 2)}`);
        if (volume) parts.push(`Volume ${formatSol(Array.isArray(volume.value) ? volume.value[1] : volume.value, 1)}`);
        if (listings) parts.push(`Listings ${formatInt(Array.isArray(listings.value) ? listings.value[1] : listings.value)}`);
        return parts.join('<br/>');
      },
    },
    axisPointer: { link: [{ xAxisIndex: [0, 1] }] },
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], filterMode: 'filter', zoomOnMouseWheel: false, moveOnMouseMove: true, moveOnMouseWheel: false },
    ],
    xAxis: [
      commonTimeAxis(0, startDate, endDate, false),
      commonTimeAxis(1, startDate, endDate, true),
    ],
    yAxis: [
      {
        ...valueAxis(0, floorScale, formatAxisDecimal, { position: 'right' }),
      },
      {
        ...valueAxis(0, volumeScale, () => '', { position: 'left', split: false, show: false }),
      },
      {
        ...valueAxis(1, listingScale, (value) => formatInt(value), { position: 'right' }),
      },
    ],
    series: [
      {
        name: 'Floor Price',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: 'none',
        showSymbol: false,
        sampling: 'none',
        progressive: 0,
        connectNulls: true,
        data: displayRows.map((row) => [row.date, row.floor_sol]),
        lineStyle: { width: 1.15, color: COLORS.line },
        itemStyle: { color: COLORS.line },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: COLORS.live, width: 1, type: 'dashed', opacity: 0.72 },
          label: {
            show: true,
            position: 'end',
            color: '#07150d',
            backgroundColor: COLORS.live,
            borderRadius: 2,
            padding: [3, 5],
            fontSize: 12,
            formatter: formatDecimal(currentFloor, 2),
          },
          data: [{ yAxis: Number(currentFloor) }],
        },
        z: 3,
      },
      {
        name: 'Volume SOL',
        type: 'bar',
        xAxisIndex: 0,
        yAxisIndex: 1,
        data: marketRows.map((row) => [row.date, row.volume_sol]),
        barMaxWidth,
        itemStyle: { color: CHART_COLORS.volume, opacity: 0.78 },
        z: 1,
      },
      {
        name: 'Listings',
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 2,
        symbol: 'none',
        showSymbol: false,
        sampling: 'none',
        progressive: 0,
        connectNulls: true,
        data: displayRows.map((row) => [row.date, row.listed_count]),
        lineStyle: { width: 1.25, color: COLORS.line2 },
        itemStyle: { color: COLORS.line2 },
        endLabel: {
          show: true,
          color: '#17130b',
          backgroundColor: COLORS.line2,
          borderRadius: 2,
          padding: [3, 5],
          fontSize: 12,
          formatter: () => formatInt(currentListings),
        },
        z: 3,
      },
    ],
  };
}


function pointerXValue(chart, spec, pointX, rect, dataLength) {
  // ECharts x-axis conversion expects a scalar pixel when the finder targets a
  // single x axis. Passing [x, y] can yield NaN for an axis-only conversion,
  // which previously collapsed category charts to data index 0.
  let converted = chart.convertFromPixel({ xAxisIndex: spec.xAxisIndex }, pointX);

  if (Array.isArray(converted)) converted = converted[0];
  if (typeof converted === 'string' && converted) return converted;
  if (Number.isFinite(Number(converted))) return Number(converted);

  // Defensive category-axis fallback. This also keeps the interaction stable
  // if a browser/ECharts build cannot convert the scalar pixel for some reason.
  const count = Math.max(1, Number(dataLength || 0));
  const ratio = Math.max(0, Math.min(0.999999, (pointX - rect.x) / Math.max(1, rect.width)));
  return Math.floor(ratio * count);
}

function nearestSeriesValueAtX(chart, spec, xValue) {
  const option = chart.getOption();
  const series = option.series?.[spec.seriesIndex];
  const data = Array.isArray(series?.data) ? series.data : [];
  if (!data.length) return null;

  const first = data[0];
  if (Array.isArray(first) || Array.isArray(first?.value)) {
    const numericTarget = Number(xValue);
    const dateTarget = typeof xValue === 'string' ? new Date(xValue).getTime() : NaN;
    const target = Number.isFinite(numericTarget) ? numericTarget : dateTarget;
    if (!Number.isFinite(target)) return null;

    let bestIndex = 0;
    let bestDistance = Infinity;
    data.forEach((entry, index) => {
      const raw = Array.isArray(entry) ? entry : entry?.value;
      const rawX = raw?.[0];
      const numericX = typeof rawX === 'number' ? rawX : new Date(rawX).getTime();
      if (!Number.isFinite(numericX)) return;
      const distance = Math.abs(numericX - target);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = index;
      }
    });
    const raw = Array.isArray(data[bestIndex]) ? data[bestIndex] : data[bestIndex]?.value;
    const value = Number(raw?.[1]);
    return Number.isFinite(value) ? { dataIndex: bestIndex, value } : null;
  }

  const xAxis = option.xAxis?.[spec.xAxisIndex] || {};
  const categories = Array.isArray(xAxis.data) ? xAxis.data : [];
  let index = -1;
  if (typeof xValue === 'string') index = categories.indexOf(xValue);
  if (index < 0 && Number.isFinite(Number(xValue))) index = Math.round(Number(xValue));
  index = Math.max(0, Math.min(data.length - 1, index));
  const raw = data[index]?.value ?? data[index];
  const value = Number(Array.isArray(raw) ? raw[1] : raw);
  return Number.isFinite(value) ? { dataIndex: index, value } : null;
}

/*
 * Line-chart crosshair:
 * - the vertical guide follows the mouse continuously;
 * - the horizontal guide snaps to the selected line's value at the nearest X;
 * - ECharts still owns the tooltip, but its native vertical line is hidden on
 *   these charts so category axes cannot visually snap the crosshair sideways.
 */
function installSnappedLineCrosshair(chart, specs) {
  const zr = chart.getZr();
  const horizontalLineId = 'snapped-line-horizontal';
  const horizontalLabelId = 'snapped-line-label';
  const verticalIds = [...new Set(specs.map((spec) => spec.gridIndex))]
    .map((gridIndex) => ({ gridIndex, id: `snapped-line-vertical-${gridIndex}` }));

  const pointOf = (event) => ({
    x: Number(event.offsetX ?? event.event?.offsetX ?? 0),
    y: Number(event.offsetY ?? event.event?.offsetY ?? 0),
  });

  const gridRect = (gridIndex) => chart.getModel().getComponent('grid', gridIndex)?.coordinateSystem?.getRect?.() || null;

  const hide = () => {
    chart.setOption({
      graphic: [
        { id: horizontalLineId, type: 'line', invisible: true },
        { id: horizontalLabelId, type: 'text', invisible: true },
        ...verticalIds.map(({ id }) => ({ id, type: 'line', invisible: true })),
      ],
    }, { lazyUpdate: true, silent: true });
  };

  const onMouseMove = (event) => {
    const point = pointOf(event);
    const spec = specs.find((candidate) => {
      const rect = gridRect(candidate.gridIndex);
      return rect
        && point.x >= rect.x && point.x <= rect.x + rect.width
        && point.y >= rect.y && point.y <= rect.y + rect.height;
    });

    if (!spec) {
      hide();
      return;
    }

    const rect = gridRect(spec.gridIndex);
    const option = chart.getOption();
    const seriesData = option.series?.[spec.seriesIndex]?.data || [];
    const xValue = pointerXValue(chart, spec, point.x, rect, seriesData.length);
    const nearest = nearestSeriesValueAtX(chart, spec, xValue);
    if (!nearest) {
      hide();
      return;
    }

    const yPixel = Number(chart.convertToPixel({ yAxisIndex: spec.yAxisIndex }, nearest.value));
    if (!Number.isFinite(yPixel)) {
      hide();
      return;
    }

    const axisPosition = option.yAxis?.[spec.yAxisIndex]?.position || 'left';
    const rightSide = axisPosition === 'right';
    const labelText = spec.formatValue ? spec.formatValue(nearest.value) : formatDecimal(nearest.value, 2);

    const verticalGraphics = verticalIds.flatMap(({ gridIndex, id }) => {
      const verticalRect = gridRect(gridIndex);
      if (!verticalRect) return [];
      return [{
        id,
        type: 'line',
        silent: true,
        invisible: false,
        z: 100,
        shape: {
          x1: point.x,
          y1: verticalRect.y,
          x2: point.x,
          y2: verticalRect.y + verticalRect.height,
        },
        style: { stroke: '#747480', lineWidth: 1, lineDash: [4, 4] },
      }];
    });

    chart.setOption({
      graphic: [
        ...verticalGraphics,
        {
          id: horizontalLineId,
          type: 'line',
          silent: true,
          invisible: false,
          z: 100,
          shape: { x1: rect.x, y1: yPixel, x2: rect.x + rect.width, y2: yPixel },
          style: { stroke: '#747480', lineWidth: 1, lineDash: [4, 4] },
        },
        {
          id: horizontalLabelId,
          type: 'text',
          silent: true,
          invisible: false,
          z: 101,
          x: rightSide ? rect.x + rect.width + 5 : rect.x - 5,
          y: yPixel,
          style: {
            text: labelText,
            fill: '#f0f0f3',
            font: '12px Inter, ui-sans-serif, system-ui, sans-serif',
            align: rightSide ? 'left' : 'right',
            verticalAlign: 'middle',
            backgroundColor: '#66666e',
            padding: [3, 5],
            borderRadius: 3,
          },
        },
      ],
    }, { lazyUpdate: true, silent: true });
  };

  zr.on('mousemove', onMouseMove);
  zr.on('globalout', hide);
  return () => {
    zr.off('mousemove', onMouseMove);
    zr.off('globalout', hide);
  };
}

function installMarketChartInteractions(chart) {
  const cleanupDrag = installMarketYAxisDrag(chart);
  const cleanupCrosshair = installSnappedLineCrosshair(chart, [
    { gridIndex: 0, xAxisIndex: 0, yAxisIndex: 0, seriesIndex: 0, formatValue: (value) => formatDecimal(value, 2) },
    { gridIndex: 1, xAxisIndex: 1, yAxisIndex: 2, seriesIndex: 2, formatValue: (value) => formatInt(value) },
  ]);
  return () => {
    cleanupCrosshair?.();
    cleanupDrag?.();
  };
}

function installBurnLineCrosshair(chart) {
  return installSnappedLineCrosshair(chart, [
    { gridIndex: 0, xAxisIndex: 0, yAxisIndex: 0, seriesIndex: 1, formatValue: (value) => formatInt(value) },
  ]);
}

function installRoyaltiesLineCrosshair(chart) {
  return installSnappedLineCrosshair(chart, [
    { gridIndex: 0, xAxisIndex: 0, yAxisIndex: 0, seriesIndex: 0, formatValue: (value) => formatDecimal(value, 2) },
  ]);
}

function installConversionLineCrosshair(chart) {
  return installSnappedLineCrosshair(chart, [
    { gridIndex: 0, xAxisIndex: 0, yAxisIndex: 0, seriesIndex: 0, formatValue: (value) => formatDecimal(value, 2) },
  ]);
}


function installMarketYAxisDrag(chart) {
  const zr = chart.getZr();
  const chartDom = chart.getDom();

  // ZRender owns the actual canvas cursor and may override a cursor assigned
  // only to the ECharts wrapper. Set both layers so the resize cursor remains
  // visible while hovering and dragging either axis.
  const applyCursor = (cursor = 'default') => {
    zr.setCursorStyle?.(cursor);
    chartDom.style.cursor = cursor === 'default' ? '' : cursor;
  };

  const initial = chart.getOption().yAxis.map((axis) => ({
    min: Number(axis.min),
    max: Number(axis.max),
    interval: Number(axis.interval),
  }));
  let drag = null;

  const pointOf = (event) => ({
    x: Number(event.offsetX ?? event.event?.offsetX ?? 0),
    y: Number(event.offsetY ?? event.event?.offsetY ?? 0),
  });

  const gridRect = (gridIndex) => chart.getModel().getComponent('grid', gridIndex)?.coordinateSystem?.getRect?.() || null;

  const hitRightAxis = (event) => {
    const { x, y } = pointOf(event);
    const width = chart.getWidth();
    for (const gridIndex of [0, 1]) {
      const rect = gridRect(gridIndex);
      if (!rect) continue;
      const inY = y >= rect.y && y <= rect.y + rect.height;
      const inAxisGutter = x >= rect.x + rect.width - 5 && x <= width;
      if (inY && inAxisGutter) return gridIndex === 0 ? 0 : 2;
    }
    return null;
  };

  // Tensor/TradingView-style time-scale manipulation lives on the visible
  // bottom x-axis. The native ew-resize cursor is the affordance, rather than
  // drawing a separate floating arrow over the chart.
  const hitBottomTimeAxis = (event) => {
    const { x, y } = pointOf(event);
    const rect = gridRect(1);
    if (!rect) return false;
    const axisTop = rect.y + rect.height - 3;
    return x >= rect.x && x <= rect.x + rect.width && y >= axisTop && y <= chart.getHeight();
  };

  const hitPlotGrid = (event) => {
    const { x, y } = pointOf(event);
    for (const gridIndex of [0, 1]) {
      const rect = gridRect(gridIndex);
      if (!rect) continue;
      if (x >= rect.x && x <= rect.x + rect.width && y >= rect.y && y <= rect.y + rect.height) {
        return gridIndex;
      }
    }
    return null;
  };

  const setCursor = (event) => {
    const axisIndex = hitRightAxis(event);
    if (axisIndex !== null) {
      applyCursor('ns-resize');
      return;
    }
    if (hitBottomTimeAxis(event)) {
      applyCursor('ew-resize');
      return;
    }
    applyCursor(hitPlotGrid(event) !== null ? 'grab' : 'default');
  };

  const patchAxis = (axisIndex, min, max) => {
    const span = Math.max(1e-9, max - min);
    const integer = axisIndex === 2;
    const interval = integer
      ? Math.max(1, Math.round(niceStep(span / 5)))
      : niceStep(span / 5);
    const patch = { min, max, interval };
    const yAxis = [{}, {}, {}];
    yAxis[axisIndex] = patch;
    chart.setOption({ yAxis }, { lazyUpdate: true });
  };

  const currentAxisRange = (axisIndex) => {
    const option = chart.getOption().yAxis?.[axisIndex] || {};
    const min = Number(option.min);
    const max = Number(option.max);
    if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return null;
    return { min, max, span: max - min };
  };

  const currentZoomRange = () => {
    const zoom = chart.getOption().dataZoom?.[0] || {};
    const start = Number.isFinite(Number(zoom.start)) ? Number(zoom.start) : 0;
    const end = Number.isFinite(Number(zoom.end)) ? Number(zoom.end) : 100;
    return { start, end, span: Math.max(0.5, end - start), center: (start + end) / 2 };
  };

  const patchZoom = (start, end) => {
    let nextStart = Math.max(0, Math.min(100, start));
    let nextEnd = Math.max(0, Math.min(100, end));
    if (nextEnd - nextStart < 0.5) {
      const center = (nextStart + nextEnd) / 2;
      nextStart = Math.max(0, center - 0.25);
      nextEnd = Math.min(100, center + 0.25);
    }
    chart.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0, start: nextStart, end: nextEnd });
  };

  const onMouseDown = (event) => {
    const raw = event.event;
    if (raw?.button !== undefined && raw.button !== 0) return;
    const axisIndex = hitRightAxis(event);

    if (axisIndex !== null) {
      const range = currentAxisRange(axisIndex);
      if (!range) return;
      const { y } = pointOf(event);
      drag = {
        mode: 'scale-y',
        axisIndex,
        startY: y,
        startMin: range.min,
        startMax: range.max,
        startSpan: range.span,
        center: (range.min + range.max) / 2,
      };
      applyCursor('ns-resize');
      raw?.preventDefault?.();
      return;
    }

    if (hitBottomTimeAxis(event)) {
      const zoom = currentZoomRange();
      const { x } = pointOf(event);
      drag = {
        mode: 'scale-x',
        startX: x,
        startSpan: zoom.span,
        center: zoom.center,
      };
      applyCursor('ew-resize');
      raw?.preventDefault?.();
      return;
    }

    const gridIndex = hitPlotGrid(event);
    if (gridIndex === null) return;
    const plotAxisIndex = gridIndex === 0 ? 0 : 2;
    const range = currentAxisRange(plotAxisIndex);
    const rect = gridRect(gridIndex);
    if (!range || !rect) return;
    const { y } = pointOf(event);
    drag = {
      mode: 'pan-y',
      axisIndex: plotAxisIndex,
      startY: y,
      startMin: range.min,
      startMax: range.max,
      startSpan: range.span,
      gridHeight: rect.height,
    };
    applyCursor('grabbing');
  };

  const onMouseMove = (event) => {
    if (!drag) {
      setCursor(event);
      return;
    }

    const { x, y } = pointOf(event);

    if (drag.mode === 'scale-y') {
      const deltaY = y - drag.startY;
      const factor = Math.exp(deltaY * 0.009);
      const span = Math.min(drag.startSpan * 25, Math.max(drag.startSpan * 0.04, drag.startSpan * factor));
      let min = drag.center - span / 2;
      let max = drag.center + span / 2;

      if (min < 0) {
        max -= min;
        min = 0;
      }
      if (drag.axisIndex === 2) {
        min = Math.max(0, Math.floor(min));
        max = Math.max(min + 1, Math.ceil(max));
      }

      patchAxis(drag.axisIndex, min, max);
      applyCursor('ns-resize');
      event.event?.preventDefault?.();
      return;
    }

    if (drag.mode === 'scale-x') {
      const deltaX = x - drag.startX;
      const factor = Math.exp(deltaX * 0.009);
      const span = Math.max(2, Math.min(100, drag.startSpan * factor));
      let start = drag.center - span / 2;
      let end = drag.center + span / 2;
      if (start < 0) {
        end -= start;
        start = 0;
      }
      if (end > 100) {
        start -= end - 100;
        end = 100;
      }
      patchZoom(start, end);
      applyCursor('ew-resize');
      event.event?.preventDefault?.();
      return;
    }

    const deltaY = y - drag.startY;
    const shift = drag.gridHeight > 0 ? (deltaY / drag.gridHeight) * drag.startSpan : 0;
    let min = drag.startMin + shift;
    let max = drag.startMax + shift;

    if (min < 0) {
      max -= min;
      min = 0;
    }
    if (drag.axisIndex === 2) {
      min = Math.max(0, Math.floor(min));
      max = Math.max(min + 1, Math.ceil(max));
    }

    patchAxis(drag.axisIndex, min, max);
    applyCursor('grabbing');
  };

  const stopDrag = (event) => {
    drag = null;
    if (event) setCursor(event);
    else applyCursor('default');
  };

  const onDoubleClick = (event) => {
    const axisIndex = hitRightAxis(event);
    if (axisIndex !== null) {
      const axis = initial[axisIndex];
      if (!axis || !Number.isFinite(axis.min) || !Number.isFinite(axis.max)) return;
      const yAxis = [{}, {}, {}];
      yAxis[axisIndex] = axis;
      chart.setOption({ yAxis }, { lazyUpdate: true });
      return;
    }
    if (hitBottomTimeAxis(event)) patchZoom(0, 100);
  };

  zr.on('mousedown', onMouseDown);
  zr.on('mousemove', onMouseMove);
  zr.on('mouseup', stopDrag);
  zr.on('globalout', stopDrag);
  zr.on('dblclick', onDoubleClick);

  return () => {
    zr.off('mousedown', onMouseDown);
    zr.off('mousemove', onMouseMove);
    zr.off('mouseup', stopDrag);
    zr.off('globalout', stopDrag);
    zr.off('dblclick', onDoubleClick);
    applyCursor('default');
  };
}

function makeMintPhaseOption(rows) {
  const values = rows.map((row) => Number(row.heroes_minted || 0));
  const scale = axisScale(values, { zeroBase: true, integer: true, tickCount: 4 });
  return {
    animation: false,
    grid: { left: 58, right: 16, top: 32, bottom: 48 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
      formatter: (params) => {
        const row = rows[params?.[0]?.dataIndex ?? 0];
        return `<strong>${row.phase}</strong><br/>${formatInt(row.heroes_minted)} Heroes<br/>${formatInt(row.unique_minters)} unique minters`;
      },
    },
    xAxis: {
      type: 'category',
      data: rows.map((row) => row.phase),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisLabel: { color: COLORS.muted, fontSize: 13 },
    },
    yAxis: valueAxis(0, scale, (value) => formatInt(value), { position: 'left' }),
    series: [{
      type: 'bar',
      data: values,
      barCategoryGap: '8%',
      itemStyle: { color: CHART_COLORS.mint },
      label: { show: true, position: 'top', color: '#d8d8e2', fontSize: 13, formatter: (p) => formatInt(p.value) },
    }],
  };
}

function makeMintWalletOption(rows) {
  const values = rows.map((row) => Number(row.minters || 0));
  const scale = axisScale(values, { zeroBase: true, integer: true, tickCount: 4 });
  return {
    animation: false,
    grid: { left: 58, right: 16, top: 32, bottom: 48 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
      formatter: (params) => {
        const row = rows[params?.[0]?.dataIndex ?? 0];
        return `<strong>${row.heroes_minted} Hero${row.heroes_minted === 1 ? '' : 'es'} minted</strong><br/>${formatInt(row.minters)} wallets<br/>${formatInt(row.total_heroes)} Heroes`;
      },
    },
    xAxis: {
      type: 'category',
      data: rows.map((row) => `${row.heroes_minted}`),
      name: 'Heroes minted per wallet',
      nameLocation: 'middle',
      nameGap: 30,
      nameTextStyle: { color: COLORS.muted, fontSize: 12 },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisLabel: { color: COLORS.muted, fontSize: 13 },
    },
    yAxis: valueAxis(0, scale, (value) => formatInt(value), { position: 'left' }),
    series: [{
      type: 'bar',
      data: values,
      barCategoryGap: '8%',
      itemStyle: { color: CHART_COLORS.wallet },
      label: { show: true, position: 'top', color: '#d8d8e2', fontSize: 13, formatter: (p) => formatInt(p.value) },
    }],
  };
}

function makeFirstResaleOption(rows) {
  const values = rows.map((row) => Number(row.heroes || 0));
  const scale = axisScale(values, { zeroBase: true, integer: true, tickCount: 4 });
  return {
    animation: false,
    grid: { left: 58, right: 16, top: 32, bottom: 52 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
      formatter: (params) => {
        const row = rows[params?.[0]?.dataIndex ?? 0];
        return `<strong>${row.bucket}</strong><br/>${formatInt(row.heroes)} Heroes`;
      },
    },
    xAxis: {
      type: 'category',
      data: rows.map((row) => row.bucket),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisLabel: { color: COLORS.muted, fontSize: 12, interval: 'auto', hideOverlap: true },
    },
    yAxis: valueAxis(0, scale, (value) => formatInt(value), { position: 'left' }),
    series: [{
      type: 'bar',
      data: values,
      barCategoryGap: '10%',
      itemStyle: {
        color: (params) => params.dataIndex === rows.length - 1 ? CHART_COLORS.resaleDark : CHART_COLORS.resale,
      },
      label: { show: true, position: 'top', color: '#d8d8e2', fontSize: 13, formatter: (p) => formatInt(p.value) },
    }],
  };
}

function makeBurnHistoryOption(rows) {
  const monthlyValues = rows.map((row) => Number(row.monthly_burns || 0));
  const totalValues = rows.map((row) => Number(row.total_burns || 0));
  const scale = axisScale([...monthlyValues, ...totalValues], { zeroBase: true, integer: true, tickCount: 4 });

  return {
    animation: false,
    grid: { left: 58, right: 18, top: 42, bottom: 48 },
    legend: {
      top: 2,
      right: 0,
      data: ['Monthly burns', 'Total burns'],
      textStyle: { color: COLORS.muted, fontSize: 13 },
      itemWidth: 11,
      itemHeight: 9,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', snap: false, lineStyle: { color: 'transparent', width: 0, opacity: 0 } },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
    },
    xAxis: {
      type: 'category',
      data: rows.map((row) => row.month),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisLabel: {
        color: COLORS.muted,
        fontSize: 12,
        hideOverlap: true,
        formatter: (value) => new Date(`${value}T00:00:00Z`).toLocaleDateString('en-US', {
          month: 'short',
          year: '2-digit',
          timeZone: 'UTC',
        }),
      },
    },
    yAxis: {
      ...valueAxis(0, scale, (value) => formatInt(value), { position: 'left' }),
    },
    series: [
      {
        name: 'Monthly burns',
        type: 'bar',
        data: monthlyValues,
        barCategoryGap: '10%',
        barMaxWidth: 24,
        itemStyle: { color: CHART_COLORS.burn },
      },
      {
        name: 'Total burns',
        type: 'line',
        data: totalValues,
        symbol: 'none',
        lineStyle: { width: 1.7, color: '#b5657a' },
        itemStyle: { color: '#b5657a' },
        z: 3,
      },
    ],
  };
}

function makeBurnRarityOption(rows) {
  const order = ['Bronze', 'Silver', 'Gold', 'Elven', 'Arcane'];
  const total = rows.reduce((sum, row) => sum + Number(row.burned || 0), 0);

  return {
    animation: false,
    tooltip: {
      trigger: 'item',
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
      formatter: (p) => `${p.name}<br/><strong>${formatInt(p.value)}</strong> burned (${formatPercent(total ? Number(p.value) / total * 100 : 0)})`,
    },
    legend: {
      orient: 'horizontal',
      left: 'center',
      bottom: 24,
      data: order,
      textStyle: { color: COLORS.muted, fontSize: 12 },
      itemWidth: 9,
      itemHeight: 9,
      itemGap: 10,
    },
    series: [{
      type: 'pie',
      radius: ['38%', '82%'],
      center: ['50%', '43%'],
      label: { show: false },
      emphasis: { scaleSize: 4 },
      data: order.map((rarity) => {
        const row = rows.find((item) => item.rarity === rarity);
        return {
          name: rarity,
          value: Number(row?.burned || 0),
          itemStyle: {
            color: RARITY_COLORS[rarity],
            borderColor: '#101012',
            borderWidth: 0.65,
          },
        };
      }),
    }],
  };
}

function monthAxis(rows) {
  return {
    type: 'category',
    data: rows.map((row) => row.month),
    boundaryGap: true,
    axisTick: { show: false },
    axisLine: { lineStyle: { color: COLORS.axis } },
    axisLabel: {
      color: COLORS.muted,
      fontSize: 12,
      hideOverlap: true,
      formatter: (value) => new Date(`${value}T00:00:00Z`).toLocaleDateString('en-US', {
        month: 'short',
        year: 'numeric',
        timeZone: 'UTC',
      }),
    },
  };
}

function makeSolConversionOption(rows) {
  const monthly = rows.map((row) => Number(row.sol_sold || 0));
  const cumulative = rows.map((row) => Number(row.total_sol_sold || 0));
  const scale = axisScale([...monthly, ...cumulative], { zeroBase: true, tickCount: 4 });
  return {
    animation: false,
    grid: { left: 58, right: 18, top: 46, bottom: 48 },
    legend: {
      top: 2,
      right: 0,
      data: ['Total SOL Sold', 'SOL Sold'],
      textStyle: { color: COLORS.muted, fontSize: 13 },
      itemWidth: 10,
      itemHeight: 10,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', snap: false, lineStyle: { color: 'transparent', width: 0, opacity: 0 } },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
    },
    xAxis: monthAxis(rows),
    yAxis: {
      ...valueAxis(0, scale, (value) => formatInt(value), { position: 'left' }),
    },
    series: [
      {
        name: 'Total SOL Sold',
        type: 'line',
        data: cumulative,
        symbol: 'none',
        lineStyle: { width: 1.7, color: ECONOMY_COLORS.sold },
        itemStyle: { color: ECONOMY_COLORS.sold },
        z: 3,
      },
      {
        name: 'SOL Sold',
        type: 'bar',
        data: monthly,
        barCategoryGap: '10%',
        barMaxWidth: 24,
        itemStyle: { color: CHART_COLORS.solBars },
      },
    ],
  };
}

function makeUsdcConversionOption(rows) {
  const monthly = rows.map((row) => Number(row.usdc_purchased || 0));
  const cumulative = rows.map((row) => Number(row.total_usdc_purchased || 0));
  const scale = axisScale([...monthly, ...cumulative], { zeroBase: true, tickCount: 4 });
  return {
    animation: false,
    grid: { left: 70, right: 18, top: 46, bottom: 48 },
    legend: {
      top: 2,
      right: 0,
      data: ['Total USDC Purchased', 'USDC Purchased'],
      textStyle: { color: COLORS.muted, fontSize: 13 },
      itemWidth: 10,
      itemHeight: 10,
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', snap: false, lineStyle: { color: 'transparent', width: 0, opacity: 0 } },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
    },
    xAxis: monthAxis(rows),
    yAxis: {
      ...valueAxis(0, scale, (value) => `$${formatInt(value)}`, { position: 'left' }),
    },
    series: [
      {
        name: 'Total USDC Purchased',
        type: 'line',
        data: cumulative,
        symbol: 'none',
        lineStyle: { width: 1.7, color: ECONOMY_COLORS.bought },
        itemStyle: { color: ECONOMY_COLORS.bought },
        z: 3,
      },
      {
        name: 'USDC Purchased',
        type: 'bar',
        data: monthly,
        barCategoryGap: '10%',
        barMaxWidth: 24,
        itemStyle: { color: CHART_COLORS.usdcBars },
      },
    ],
  };
}

function makeRoyaltiesOption(rows) {
  const values = rows.map((row) => Number(row.royalties_sol || 0));
  const scale = axisScale(values, { zeroBase: true, tickCount: 4 });
  return {
    animation: false,
    grid: { left: 58, right: 18, top: 30, bottom: 46 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', snap: false, lineStyle: { color: 'transparent', width: 0, opacity: 0 } },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
      formatter: (params) => {
        const item = params?.[0];
        return item ? `<strong>${item.axisValue}</strong><br/>${formatSol(item.value, 3)}` : '';
      },
    },
    xAxis: monthAxis(rows),
    yAxis: {
      ...valueAxis(0, scale, (value) => formatAxisDecimal(value), { position: 'left' }),
    },
    series: [{
      name: 'Guild Saga royalties',
      type: 'line',
      data: values,
      symbol: 'none',
      lineStyle: { width: 1.5, color: CHART_COLORS.royalties },
      itemStyle: { color: CHART_COLORS.royalties },
    }],
  };
}

function makeTreasurySplitOption(rows) {
  return {
    animation: false,
    grid: { left: 48, right: 16, top: 22, bottom: 56 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
      formatter: (params) => {
        const row = rows[params?.[0]?.dataIndex ?? 0];
        return `<strong>${row.destination}</strong><br/>${formatSol(row.sol, 3)}<br/>${formatPercent(row.pct, 0)} of mint proceeds`;
      },
    },
    xAxis: {
      type: 'category',
      data: rows.map((row) => row.destination.replace(' branch', '')),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisLabel: { color: COLORS.muted, fontSize: 12 },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      interval: 25,
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { color: COLORS.muted, fontSize: 12, formatter: '{value}%' },
      splitLine: { lineStyle: { color: COLORS.grid } },
    },
    series: [{
      type: 'bar',
      data: rows.map((row) => ({
        value: Number(row.pct || 0),
        sol: Number(row.sol || 0),
      })),
      barCategoryGap: '16%',
      itemStyle: { color: CHART_COLORS.treasury },
      label: {
        show: true,
        position: 'top',
        color: '#dedde7',
        fontSize: 13,
        formatter: (p) => `${formatPercent(p.value, 0)}\n${formatSol(p.data.sol, 0)}`,
      },
    }],
  };
}

function SnapshotStat({ label, value, sub }) {
  return (
    <div className="snapshot-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      {sub && <small>{sub}</small>}
    </div>
  );
}

function ShareMeter({ label, value, pct, tone = 'accent' }) {
  const clamped = Math.max(0, Math.min(100, Number(pct || 0)));
  return (
    <div className="share-meter">
      <div className="share-meter-labels">
        <strong>{label}</strong>
        <span>{value} · {formatPercent(clamped)} of active supply</span>
      </div>
      <div className="share-meter-track" aria-label={`${label}: ${formatPercent(clamped)} of active supply`}>
        <span className={`share-meter-fill share-meter-${tone}`} style={{ width: `${clamped}%` }} />
      </div>
    </div>
  );
}

function LightboxIcon({ type }) {
  if (type === 'close') {
    return (
      <svg className="image-lightbox-control-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M6 6 18 18M18 6 6 18" />
      </svg>
    );
  }

  const points = type === 'prev' ? '15 5 8 12 15 19' : '9 5 16 12 9 19';
  return (
    <svg className="image-lightbox-control-icon image-lightbox-arrow-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <polyline points={points} />
    </svg>
  );
}

function ImageLightbox({ items, index, onClose, onChange, label }) {
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'ArrowLeft') onChange((index - 1 + items.length) % items.length);
      if (event.key === 'ArrowRight') onChange((index + 1) % items.length);
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [index, items.length, onChange, onClose]);

  const item = items[index];

  return (
    <div
      className="image-lightbox"
      role="dialog"
      aria-modal="true"
      aria-label={label}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <button type="button" className="image-lightbox-close" aria-label="Close image viewer" onClick={onClose}><LightboxIcon type="close" /></button>
      {items.length > 1 && (
        <button
          type="button"
          className="image-lightbox-arrow image-lightbox-prev"
          aria-label="Previous image"
          onClick={() => onChange((index - 1 + items.length) % items.length)}
        ><LightboxIcon type="prev" /></button>
      )}
      <div className="image-lightbox-image-wrap">
        <img src={item.src} alt={item.alt} />
      </div>
      {items.length > 1 && (
        <button
          type="button"
          className="image-lightbox-arrow image-lightbox-next"
          aria-label="Next image"
          onClick={() => onChange((index + 1) % items.length)}
        ><LightboxIcon type="next" /></button>
      )}
    </div>
  );
}

function loadHeroSourceImage(heroId) {
  if (heroSourceImageCache.has(heroId)) return heroSourceImageCache.get(heroId);

  const request = new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Could not load Hero #${heroId} PFP source.`));
    image.src = getHeroSourceUrl(heroId);
  });

  heroSourceImageCache.set(heroId, request);
  request.catch(() => heroSourceImageCache.delete(heroId));
  return request;
}

function preloadImageUrl(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => {
      if (typeof image.decode !== 'function') {
        resolve();
        return;
      }
      image.decode().then(resolve, resolve);
    };
    image.onerror = () => reject(new Error(`Could not preload image: ${src}`));
    image.src = src;
  });
}

function canvasToPngBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error('Browser could not encode the PFP PNG.'));
    }, 'image/png');
  });
}

async function makeHeroPfpBlob(sourceImage, backgroundColor, variant, outputOverride = null) {
  const isFace = variant === 'face';
  const sourceWidth = isFace ? HERO_FACE_CROP.width : HERO_SOURCE_WIDTH;
  const sourceHeight = isFace ? HERO_FACE_CROP.height : HERO_SOURCE_HEIGHT;
  const output = outputOverride || (isFace ? HERO_FACE_OUTPUT : HERO_BODY_OUTPUT);

  // Composite at the original tiny pixel resolution first. The second canvas
  // then performs only an exact integer nearest-neighbor enlargement.
  const compositeCanvas = document.createElement('canvas');
  compositeCanvas.width = sourceWidth;
  compositeCanvas.height = sourceHeight;
  const compositeContext = compositeCanvas.getContext('2d', { alpha: false });
  if (!compositeContext) throw new Error('Browser could not create the PFP canvas.');

  compositeContext.imageSmoothingEnabled = false;
  compositeContext.fillStyle = backgroundColor;
  compositeContext.fillRect(0, 0, sourceWidth, sourceHeight);

  if (isFace) {
    compositeContext.drawImage(
      sourceImage,
      HERO_FACE_CROP.x,
      HERO_FACE_CROP.y,
      HERO_FACE_CROP.width,
      HERO_FACE_CROP.height,
      0,
      0,
      sourceWidth,
      sourceHeight,
    );
  } else {
    compositeContext.drawImage(sourceImage, 0, 0);
  }

  const outputCanvas = document.createElement('canvas');
  outputCanvas.width = output.width;
  outputCanvas.height = output.height;
  const outputContext = outputCanvas.getContext('2d', { alpha: false });
  if (!outputContext) throw new Error('Browser could not create the PFP output canvas.');

  outputContext.imageSmoothingEnabled = false;
  outputContext.drawImage(compositeCanvas, 0, 0, output.width, output.height);
  return canvasToPngBlob(outputCanvas);
}


async function makeRoundedHeroFaviconBlob(sourceImage, backgroundColor) {
  const compositeCanvas = document.createElement('canvas');
  compositeCanvas.width = HERO_FACE_CROP.width;
  compositeCanvas.height = HERO_FACE_CROP.height;
  const compositeContext = compositeCanvas.getContext('2d', { alpha: false });
  if (!compositeContext) throw new Error('Browser could not create the favicon canvas.');

  compositeContext.imageSmoothingEnabled = false;
  compositeContext.fillStyle = backgroundColor;
  compositeContext.fillRect(0, 0, HERO_FACE_CROP.width, HERO_FACE_CROP.height);
  compositeContext.drawImage(
    sourceImage,
    HERO_FACE_CROP.x,
    HERO_FACE_CROP.y,
    HERO_FACE_CROP.width,
    HERO_FACE_CROP.height,
    0,
    0,
    HERO_FACE_CROP.width,
    HERO_FACE_CROP.height,
  );

  const outputCanvas = document.createElement('canvas');
  outputCanvas.width = HERO_FAVICON_OUTPUT.width;
  outputCanvas.height = HERO_FAVICON_OUTPUT.height;
  const outputContext = outputCanvas.getContext('2d');
  if (!outputContext) throw new Error('Browser could not create the favicon output canvas.');

  outputContext.imageSmoothingEnabled = false;
  outputContext.beginPath();
  outputContext.roundRect(
    0,
    0,
    HERO_FAVICON_OUTPUT.width,
    HERO_FAVICON_OUTPUT.height,
    HERO_FAVICON_RADIUS,
  );
  outputContext.clip();
  outputContext.drawImage(
    compositeCanvas,
    0,
    0,
    HERO_FAVICON_OUTPUT.width,
    HERO_FAVICON_OUTPUT.height,
  );

  return canvasToPngBlob(outputCanvas);
}

function setHeroFavicon(href) {
  let link = document.querySelector('link[rel="icon"]');
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    link.type = 'image/png';
    document.head.appendChild(link);
  }
  link.href = href;
}

function BrandHeroMark({ candidate }) {
  const initialUsesStaticHeroZero = candidate.heroId === 0 && candidate.color === getHeroDefaultColor(0);
  const [currentUrl, setCurrentUrl] = useState(initialUsesStaticHeroZero ? HERO_ZERO_FACE_PFP : null);
  const [nextUrl, setNextUrl] = useState(null);
  const [nextVisible, setNextVisible] = useState(false);
  const candidateVersionRef = useRef(0);
  const currentBlobUrlRef = useRef(null);
  const nextBlobUrlRef = useRef(null);
  const currentFaviconBlobUrlRef = useRef(null);
  const fadeTimerRef = useRef(null);
  const firstCandidateRef = useRef(true);

  useEffect(() => {
    const version = ++candidateVersionRef.current;
    const isInitialCandidate = firstCandidateRef.current;
    const delay = isInitialCandidate ? 0 : HERO_IDENTITY_DELAY_MS;
    firstCandidateRef.current = false;
    const timer = window.setTimeout(async () => {
      try {
        const sourceImage = await loadHeroSourceImage(candidate.heroId);
        const [headerBlob, faviconBlob] = await Promise.all([
          makeHeroPfpBlob(sourceImage, candidate.color, 'face', HERO_IDENTITY_OUTPUT),
          makeRoundedHeroFaviconBlob(sourceImage, candidate.color),
        ]);

        if (version !== candidateVersionRef.current) return;

        const url = URL.createObjectURL(headerBlob);
        const faviconUrl = URL.createObjectURL(faviconBlob);

        const previousFaviconBlobUrl = currentFaviconBlobUrlRef.current;
        currentFaviconBlobUrlRef.current = faviconUrl;
        setHeroFavicon(faviconUrl);
        if (previousFaviconBlobUrl) URL.revokeObjectURL(previousFaviconBlobUrl);

        // A persisted preference is restored immediately on first load. Later
        // changes keep the intentional 1.5 s settle delay + crossfade.
        if (isInitialCandidate) {
          const previousBlobUrl = currentBlobUrlRef.current;
          currentBlobUrlRef.current = url;
          setCurrentUrl(url);
          setNextUrl(null);
          setNextVisible(false);
          if (previousBlobUrl) URL.revokeObjectURL(previousBlobUrl);
          return;
        }

        nextBlobUrlRef.current = url;
        setNextVisible(false);
        setNextUrl(url);

        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => setNextVisible(true));
        });

        if (fadeTimerRef.current) window.clearTimeout(fadeTimerRef.current);
        fadeTimerRef.current = window.setTimeout(() => {
          const previousBlobUrl = currentBlobUrlRef.current;
          currentBlobUrlRef.current = url;
          nextBlobUrlRef.current = null;
          setCurrentUrl(url);
          setNextUrl(null);
          setNextVisible(false);
          if (previousBlobUrl) URL.revokeObjectURL(previousBlobUrl);
        }, HERO_IDENTITY_FADE_MS);
      } catch (error) {
        if (version === candidateVersionRef.current) console.error(error);
      }
    }, delay);

    return () => window.clearTimeout(timer);
  }, [candidate.heroId, candidate.color]);

  useEffect(() => () => {
    if (fadeTimerRef.current) window.clearTimeout(fadeTimerRef.current);
    if (currentBlobUrlRef.current) URL.revokeObjectURL(currentBlobUrlRef.current);
    if (nextBlobUrlRef.current) URL.revokeObjectURL(nextBlobUrlRef.current);
    if (currentFaviconBlobUrlRef.current) URL.revokeObjectURL(currentFaviconBlobUrlRef.current);
  }, []);

  return (
    <span className="brand-mark" aria-hidden="true">
      {currentUrl && <img className="brand-mark-image" src={currentUrl} alt="" />}
      {nextUrl && (
        <img
          className={`brand-mark-image brand-mark-image-next ${nextVisible ? 'is-visible' : ''}`}
          src={nextUrl}
          alt=""
        />
      )}
    </span>
  );
}

function HeroImageCard({ item, mobileActive, onOpen }) {
  return (
    <button
      type="button"
      className={`hero-art hero-art-${item.id} ${mobileActive ? 'is-mobile-active' : ''}`}
      onClick={onOpen}
      aria-label={`Open ${item.alt}`}
    >
      <img src={item.src} alt={item.alt} />
    </button>
  );
}

function PfpColorPopover({ color, onChange, defaultColor }) {
  const [open, setOpen] = useState(false);
  const popoverRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;

    const handlePointerDown = (event) => {
      if (!popoverRef.current?.contains(event.target)) setOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  return (
    <div className="pfp-color-popover" ref={popoverRef}>
      <button
        type="button"
        className="pfp-color-trigger"
        aria-label="Choose PFP background color"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="pfp-color-trigger-swatch" style={{ background: color }} aria-hidden="true" />
        <span className="pfp-color-trigger-chevron" aria-hidden="true">⌄</span>
      </button>

      {open && (
        <div className="pfp-color-panel" role="dialog" aria-label="PFP background color picker">
          <div className="pfp-color-panel-title">PFP Background</div>
          <HexColorPicker color={color} onChange={onChange} />
          <div className="pfp-color-panel-row">
            <span>Hex</span>
            <HexColorInput
              className="pfp-color-hex"
              color={color}
              onChange={onChange}
              prefixed
              spellCheck="false"
              aria-label="PFP background hex color"
            />
          </div>
          <button
            type="button"
            className="pfp-color-default"
            onClick={() => onChange(defaultColor)}
          >
            Default
          </button>
        </div>
      )}
    </div>
  );
}

function HeroShowcase({ onIdentityCandidate }) {
  const [initialPreference] = useState(readHeroPreference);
  const [mobileView, setMobileView] = useState('original');
  const [heroLightboxIndex, setHeroLightboxIndex] = useState(null);
  const [heroInput, setHeroInput] = useState(() => String(initialPreference.heroId));
  const [heroId, setHeroId] = useState(initialPreference.heroId);
  const [pfpColor, setPfpColor] = useState(initialPreference.color);
  const [visibleHeroSet, setVisibleHeroSet] = useState(() => ({
    heroId: initialPreference.heroId,
    original: getHeroOriginalUrl(initialPreference.heroId),
    body: HERO_ZERO_BODY_PFP,
    face: HERO_ZERO_FACE_PFP,
  }));
  const generationIdRef = useRef(0);
  const activeBlobUrlsRef = useRef([]);

  useEffect(() => {
    saveHeroPreference(heroId, pfpColor);
    onIdentityCandidate?.({ heroId, color: pfpColor });
  }, [heroId, pfpColor, onIdentityCandidate]);

  useEffect(() => {
    let cancelled = false;
    const generationId = ++generationIdRef.current;

    const generate = async () => {
      let nextUrls = [];
      try {
        const sourceImage = await loadHeroSourceImage(heroId);
        const [bodyBlob, faceBlob] = await Promise.all([
          makeHeroPfpBlob(sourceImage, pfpColor, 'body'),
          makeHeroPfpBlob(sourceImage, pfpColor, 'face'),
        ]);

        nextUrls = [URL.createObjectURL(bodyBlob), URL.createObjectURL(faceBlob)];
        const nextHeroSet = {
          heroId,
          original: getHeroOriginalUrl(heroId),
          body: nextUrls[0],
          face: nextUrls[1],
        };

        // Keep the current trio visible until every replacement is loaded and
        // decoded. One state update then reveals all three in the same render.
        await Promise.all([
          preloadImageUrl(nextHeroSet.original),
          preloadImageUrl(nextHeroSet.body),
          preloadImageUrl(nextHeroSet.face),
        ]);

        if (cancelled || generationId !== generationIdRef.current) {
          nextUrls.forEach((url) => URL.revokeObjectURL(url));
          return;
        }

        const previousUrls = activeBlobUrlsRef.current;
        activeBlobUrlsRef.current = nextUrls;
        setVisibleHeroSet(nextHeroSet);
        nextUrls = [];
        window.requestAnimationFrame(() => {
          previousUrls.forEach((url) => URL.revokeObjectURL(url));
        });
      } catch (error) {
        nextUrls.forEach((url) => URL.revokeObjectURL(url));
        if (!cancelled && generationId === generationIdRef.current) {
          console.error(error);
        }
      }
    };

    generate();
    return () => {
      cancelled = true;
    };
  }, [heroId, pfpColor]);

  useEffect(() => () => {
    activeBlobUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
  }, []);

  const heroShowcaseItems = useMemo(() => [
    {
      id: 'original',
      label: 'Original',
      src: visibleHeroSet.original,
      alt: `Guild Saga Hero #${visibleHeroSet.heroId} original NFT image`,
    },
    {
      id: 'body',
      label: 'Body PFP',
      src: visibleHeroSet.body,
      alt: `Guild Saga Hero #${visibleHeroSet.heroId} body profile picture`,
    },
    {
      id: 'face',
      label: 'Face PFP',
      src: visibleHeroSet.face,
      alt: `Guild Saga Hero #${visibleHeroSet.heroId} face profile picture`,
    },
  ], [visibleHeroSet]);

  const handleHeroInput = (event) => {
    const raw = String(event.target.value || '');
    const digits = raw.replace(/^\s*#/, '').replace(/\D/g, '');

    if (digits === '') {
      setHeroInput('');
      return;
    }

    const nextHeroId = Number(digits);
    if (!Number.isInteger(nextHeroId) || nextHeroId < 0 || nextHeroId > 9999) return;

    setHeroInput(digits);
    if (nextHeroId !== heroId) {
      setHeroId(nextHeroId);
      setPfpColor(getHeroDefaultColor(nextHeroId));
    }
  };

  return (
    <section className="hero-showcase" aria-labelledby="hero-showcase-title">
      <div className="hero-showcase-copy">
        <h1 id="hero-showcase-title">Guild Saga Heroes</h1>
        <p className="intro-lead">
          A 10,000-piece Solana NFT collection that can be used in <strong>Guild Saga: Labyrinths</strong>, a tactical RPG from Ocelot Technologies.
        </p>
        <p className="intro-body">
          This community-made site tracks the collection's supply, holders, staking activity, burns and secondary-market history using on-chain data.
        </p>
        <div className="hero-link-groups" aria-label="Marketplace and community links">
          {HERO_PLATFORM_GROUPS.map((group) => (
            <div key={group.id} className="hero-link-group">
              <span className="hero-link-group-label">{group.label}</span>
              <div className="hero-link-grid">
                {group.links.map((item) => (
                  <a
                    key={item.id}
                    className="hero-platform-link"
                    data-platform={item.id}
                    href={item.href}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <span className="hero-platform-link-icon" aria-hidden="true">
                      <img src={item.icon} alt="" />
                    </span>
                    <span className="hero-platform-link-title">{item.label}</span>
                  </a>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="hero-browser">
        <div className="hero-art-grid">
          {heroShowcaseItems.map((item, index) => (
            <HeroImageCard
              key={item.id}
              item={item}
              mobileActive={mobileView === item.id}
              onOpen={() => setHeroLightboxIndex(index)}
            />
          ))}
        </div>

        <div className="hero-mobile-tabs" role="tablist" aria-label="Hero image type">
          {heroShowcaseItems.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={mobileView === item.id}
              className={mobileView === item.id ? 'is-active' : ''}
              onClick={() => setMobileView(item.id)}
            >
              {item.id === 'original' ? 'NFT' : item.id === 'body' ? 'Body' : 'Face'}
            </button>
          ))}
        </div>

        <div className="hero-browser-controls" aria-label="Hero and PFP controls">
          <label className="hero-control-group hero-selector" htmlFor="hero-number-preview">
            <span className="hero-control-label">Select Hero</span>
            <span className="hero-number-input-wrap">
              <span aria-hidden="true">#</span>
              <input
                id="hero-number-preview"
                type="text"
                inputMode="numeric"
                autoComplete="off"
                value={heroInput}
                onChange={handleHeroInput}
                aria-label="Hero number from 0 to 9999"
              />
            </span>
          </label>

          <div className="hero-control-group pfp-background-control">
            <span className="hero-control-label">PFP Background</span>
            <PfpColorPopover
              color={pfpColor}
              onChange={(value) => setPfpColor(String(value).toUpperCase())}
              defaultColor={getHeroDefaultColor(heroId)}
            />
          </div>
        </div>
      </div>

      {heroLightboxIndex !== null && (
        <ImageLightbox
          items={heroShowcaseItems}
          index={heroLightboxIndex}
          onClose={() => setHeroLightboxIndex(null)}
          onChange={setHeroLightboxIndex}
          label={`Guild Saga Hero #${visibleHeroSet.heroId} image viewer`}
        />
      )}
    </section>
  );
}

function EpicGamesIcon() {
  return (
    <svg className="epic-games-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path fill="currentColor" d="M3.537 0C2.165 0 1.66.506 1.66 1.879V18.44c0 .145.007.29.02.433.031.3.037.59.316.92.027.033.311.245.311.245.153.075.258.13.43.2l8.335 3.491c.433.199.614.276.928.27.314.006.495-.071.928-.27l8.335-3.492c.172-.07.277-.124.43-.2 0 0 .284-.211.311-.243.28-.33.285-.621.316-.92.014-.144.02-.288.02-.434V1.879C22.34.506 21.834 0 20.462 0H3.537Zm1.18 3.19h3.114v1.274H6.117v2.603h1.648v1.275H6.117v2.774h1.74v1.275h-3.14V3.19Zm3.816 0h2.198c1.138 0 1.7.564 1.7 1.708v2.445c0 1.144-.562 1.71-1.7 1.71h-.799v3.338h-1.4V3.19Zm4.53 0h1.4v9.201h-1.4V3.19Zm3.84-.08h.68c1.138 0 1.688.553 1.688 1.696v1.88h-1.374v-1.8c0-.369-.17-.54-.523-.54h-.235c-.367 0-.537.17-.537.539v5.81c0 .369.17.54.537.54h.262c.353 0 .523-.171.523-.54V8.619h1.373v2.143c0 1.144-.562 1.71-1.7 1.71h-.694c-1.138 0-1.7-.566-1.7-1.71V4.82c0-1.144.562-1.709 1.7-1.709ZM9.933 4.425v3.392h.575c.354 0 .523-.171.523-.54V4.965c0-.368-.17-.54-.523-.54h-.575Zm-3.74 10.147c.215 0 .412.036.591.108.18.073.343.172.49.299l-.452.546a1.247 1.247 0 0 0-.308-.195.91.91 0 0 0-.363-.068.658.658 0 0 0-.28.06.703.703 0 0 0-.224.163.783.783 0 0 0-.151.243.799.799 0 0 0-.056.299v.008c0 .111.019.214.056.31a.7.7 0 0 0 .157.245.736.736 0 0 0 .238.16.774.774 0 0 0 .303.058.79.79 0 0 0 .445-.116v-.339h-.548v-.565H7.37v1.255a2.019 2.019 0 0 1-.524.307 1.789 1.789 0 0 1-.683.123 1.642 1.642 0 0 1-.602-.107 1.46 1.46 0 0 1-.478-.3 1.371 1.371 0 0 1-.318-.455 1.438 1.438 0 0 1-.115-.58v-.008c0-.203.038-.393.113-.57.075-.178.179-.331.312-.46.133-.13.291-.233.474-.309.183-.074.382-.111.598-.111h.046Zm11.963.008c.22 0 .424.031.612.094.188.062.357.155.507.277l-.386.546a1.562 1.562 0 0 0-.39-.205 1.178 1.178 0 0 0-.388-.07.347.347 0 0 0-.208.052.154.154 0 0 0-.07.127v.008c0 .032.007.06.022.084a.198.198 0 0 0 .076.066.831.831 0 0 0 .147.06c.062.02.14.04.236.061.16.037.303.078.43.122.127.045.236.101.328.17a.678.678 0 0 1 .207.24.739.739 0 0 1 .071.337v.008a.865.865 0 0 1-.081.382.82.82 0 0 1-.229.285 1.032 1.032 0 0 1-.353.18 1.606 1.606 0 0 1-.46.061 2.16 2.16 0 0 1-.71-.116 1.718 1.718 0 0 1-.593-.346l.43-.514c.277.223.578.335.9.335a.457.457 0 0 0 .236-.05.157.157 0 0 0 .082-.142v-.008a.15.15 0 0 0-.02-.077.204.204 0 0 0-.073-.066.753.753 0 0 0-.143-.062 2.45 2.45 0 0 0-.233-.062 5.036 5.036 0 0 1-.413-.113 1.26 1.26 0 0 1-.331-.16.72.72 0 0 1-.222-.243.73.73 0 0 1-.082-.36v-.008c0-.128.025-.248.074-.359a.794.794 0 0 1 .214-.283 1.007 1.007 0 0 1 .34-.185c.133-.044.282-.066.448-.066h.025Zm-9.358.025h.742l1.183 2.81h-.825l-.203-.499H8.623l-.198.498h-.81l1.183-2.81Zm2.197.02h.814l.663 1.08.663-1.08h.814v2.79h-.766v-1.602l-.711 1.091h-.016l-.707-1.083v1.593h-.754v-2.79Zm3.469 0h2.235v.658h-1.473v.422h1.334v.61h-1.334v.442h1.493v.658h-2.255v-2.79Zm-5.3.897-.315.793h.624l-.31-.793Zm-1.145 5.19h8.014l-4.09 1.348-3.924-1.348Z" />
    </svg>
  );
}

function getLabyrinthsCarouselLayout(viewportWidth = 0, copyHeight = 0) {
  const width = window.innerWidth;

  if (width <= 520) {
    const step = 84;
    return { step, offset: (100 - step) / 2, height: null };
  }

  if (width <= 780) {
    const step = 50;
    return { step, offset: (100 - step) / 2, height: null };
  }

  if (width <= 1120) {
    const step = 100 / 3;
    return { step, offset: (100 - step) / 2, height: null };
  }

  // Desktop derives the slide width from the finished left-column height.
  // Each screenshot remains 16:9; the 10px term accounts for the item's
  // 5px horizontal padding on each side. This makes the carousel bottom
  // land exactly on the Epic button bottom instead of relying on a guessed
  // fixed percentage.
  if (viewportWidth > 0 && copyHeight > 0) {
    const itemOuterWidth = (copyHeight * 16 / 9) + 10;
    const step = (itemOuterWidth / viewportWidth) * 100;
    return { step, offset: (100 - step) / 2, height: copyHeight };
  }

  const step = 25;
  return { step, offset: (100 - step) / 2, height: null };
}

function LabyrinthsShowcase() {
  const REAL_COUNT = LABYRINTHS_SLIDES.length;
  const LOOP_PAD = Math.min(4, REAL_COUNT);
  const START_INDEX = LOOP_PAD;
  const loopSlides = useMemo(() => {
    if (!REAL_COUNT) return [];
    return [
      ...LABYRINTHS_SLIDES.slice(-LOOP_PAD).map((slide, index) => ({ ...slide, loopKey: `pre-${index}-${slide.id}` })),
      ...LABYRINTHS_SLIDES.map((slide, index) => ({ ...slide, loopKey: `main-${index}-${slide.id}` })),
      ...LABYRINTHS_SLIDES.slice(0, LOOP_PAD).map((slide, index) => ({ ...slide, loopKey: `post-${index}-${slide.id}` })),
    ];
  }, [LOOP_PAD, REAL_COUNT]);

  const [carouselIndex, setCarouselIndex] = useState(() => START_INDEX + (window.innerWidth > 1120 ? 1 : 0));
  const [carouselPaused, setCarouselPaused] = useState(false);
  const [carouselTransition, setCarouselTransition] = useState(true);
  const [lightboxIndex, setLightboxIndex] = useState(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [carouselLayout, setCarouselLayout] = useState(() => getLabyrinthsCarouselLayout());
  const carouselLocked = useRef(false);
  const labyrinthsCopyRef = useRef(null);
  const labyrinthsViewportRef = useRef(null);

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(media.matches);
    update();
    media.addEventListener?.('change', update);
    return () => media.removeEventListener?.('change', update);
  }, []);

  useEffect(() => {
    const update = () => {
      const viewportWidth = labyrinthsViewportRef.current?.clientWidth || 0;
      const copyHeight = labyrinthsCopyRef.current?.getBoundingClientRect().height || 0;
      const next = getLabyrinthsCarouselLayout(viewportWidth, copyHeight);
      setCarouselLayout((current) => (
        Math.abs(current.step - next.step) < 0.001
        && Math.abs((current.height || 0) - (next.height || 0)) < 0.5
          ? current
          : next
      ));
    };

    update();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    if (labyrinthsCopyRef.current) observer?.observe(labyrinthsCopyRef.current);
    if (labyrinthsViewportRef.current) observer?.observe(labyrinthsViewportRef.current);
    window.addEventListener('resize', update);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', update);
    };
  }, []);

  useEffect(() => {
    if (reducedMotion || carouselPaused || lightboxIndex !== null || REAL_COUNT <= 1) return undefined;
    const timer = window.setInterval(() => {
      if (carouselLocked.current) return;
      carouselLocked.current = true;
      setCarouselIndex((current) => current + 1);
    }, 9000);
    return () => window.clearInterval(timer);
  }, [carouselPaused, lightboxIndex, reducedMotion, REAL_COUNT]);

  const normalizeIndex = (index) => {
    if (index >= START_INDEX + REAL_COUNT) return index - REAL_COUNT;
    if (index < START_INDEX) return index + REAL_COUNT;
    return index;
  };

  const moveCarousel = (direction) => {
    if (REAL_COUNT <= 1) return;

    if (reducedMotion) {
      setCarouselIndex((current) => normalizeIndex(current + direction));
      return;
    }

    if (carouselLocked.current) return;
    carouselLocked.current = true;
    setCarouselIndex((current) => current + direction);
  };

  const finishCarouselMove = () => {
    const normalized = normalizeIndex(carouselIndex);
    if (normalized !== carouselIndex) {
      setCarouselTransition(false);
      setCarouselIndex(normalized);
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          setCarouselTransition(true);
          carouselLocked.current = false;
        });
      });
      return;
    }
    carouselLocked.current = false;
  };

  return (
    <section className="labyrinths-showcase" aria-labelledby="labyrinths-showcase-title">
      <div className="labyrinths-copy" ref={labyrinthsCopyRef}>
        <h2 id="labyrinths-showcase-title">Guild Saga: Labyrinths</h2>
        <p>A free-to-play tactical RPG of ever-changing dungeon archives, strategic battles and risky choices.</p>
        <a className="epic-wishlist-link" href="https://store.epicgames.com/p/guild-saga-labyrinths-ca0f96?lang=en-US" target="_blank" rel="noreferrer">
          <span className="epic-wishlist-icon" aria-hidden="true"><EpicGamesIcon /></span>
          <span>Wishlist on Epic Games Store</span>
        </a>
      </div>

      <div
        className="labyrinths-carousel"
        onMouseEnter={() => setCarouselPaused(true)}
        onMouseLeave={() => setCarouselPaused(false)}
      >
        <div
          className="labyrinths-carousel-viewport"
          ref={labyrinthsViewportRef}
          style={carouselLayout.height ? { height: `${carouselLayout.height}px` } : undefined}
        >
          <div
            className={`labyrinths-carousel-track ${carouselTransition && !reducedMotion ? 'is-animated' : ''}`}
            style={{ transform: `translateX(${carouselLayout.offset - carouselIndex * carouselLayout.step}%)` }}
            onTransitionEnd={finishCarouselMove}
          >
            {loopSlides.map((slide) => {
              const actualIndex = LABYRINTHS_SLIDES.findIndex((item) => item.id === slide.id);
              return (
                <button
                  type="button"
                  className="labyrinths-carousel-item"
                  style={{ flex: `0 0 ${carouselLayout.step}%` }}
                  key={slide.loopKey}
                  onClick={() => setLightboxIndex(actualIndex)}
                  aria-label={`Open Guild Saga: Labyrinths screenshot ${actualIndex + 1}`}
                >
                  <img src={slide.src} alt={slide.alt} />
                </button>
              );
            })}
          </div>
        </div>

        {REAL_COUNT > 1 && (
          <>
            <button
              type="button"
              className="labyrinths-arrow labyrinths-arrow-prev"
              aria-label="Previous screenshots"
              onClick={() => moveCarousel(-1)}
            ><LightboxIcon type="prev" /></button>
            <button
              type="button"
              className="labyrinths-arrow labyrinths-arrow-next"
              aria-label="Next screenshots"
              onClick={() => moveCarousel(1)}
            ><LightboxIcon type="next" /></button>
          </>
        )}
      </div>

      {lightboxIndex !== null && (
        <ImageLightbox
          items={LABYRINTHS_SLIDES}
          index={lightboxIndex}
          onClose={() => setLightboxIndex(null)}
          onChange={setLightboxIndex}
          label="Guild Saga: Labyrinths screenshot viewer"
        />
      )}
    </section>
  );
}

function Overview({ data, onHeroIdentityCandidate }) {
  return (
    <div className="page-stack overview-page">
      <HeroShowcase onIdentityCandidate={onHeroIdentityCandidate} />
      <LabyrinthsShowcase />
    </div>
  );
}

function Market({ data }) {
  const [range, setRange] = useState('all');
  const [granularity, setGranularity] = useState('1d');
  const tradingRef = useRef(null);
  const allFloorRows = data.floor.history;
  const allDailyMarketRows = data.dailyMarket.rows || [];
  const endDate = allFloorRows.at(-1)?.snapshot_date || data.floor.as_of;
  const earliestDate = allFloorRows[0]?.snapshot_date || endDate;
  const startDate = rangeStartDate(range, endDate, earliestDate);

  const floorRows = useMemo(
    () => allFloorRows.filter((row) => row.snapshot_date >= startDate && row.snapshot_date <= endDate),
    [allFloorRows, startDate, endDate],
  );
  const dailyMarketRows = useMemo(
    () => allDailyMarketRows.filter((row) => row.date >= startDate && row.date <= endDate),
    [allDailyMarketRows, startDate, endDate],
  );
  const displayRows = useMemo(() => aggregateFloorRows(floorRows, granularity), [floorRows, granularity]);
  const marketRows = useMemo(() => aggregateMarketRows(dailyMarketRows, granularity), [dailyMarketRows, granularity]);

  const period = useMemo(() => ({
    sales: dailyMarketRows.reduce((total, row) => total + Number(row.sales || 0), 0),
    volume: dailyMarketRows.reduce((total, row) => total + Number(row.volume_sol || 0), 0),
  }), [dailyMarketRows]);

  const chartOption = useMemo(
    () => makeTradingMarketOption(
      displayRows,
      marketRows,
      startDate,
      endDate,
      range,
      granularity,
      data.summary.floor.floor_sol,
      data.summary.floor.listed_count,
    ),
    [displayRows, marketRows, startDate, endDate, range, granularity, data.summary.floor.floor_sol, data.summary.floor.listed_count],
  );

  const rangeLabel = RANGE_OPTIONS.find((item) => item.id === range)?.label || 'All';
  const periodLabel = range === 'all' ? 'All-time' : rangeLabel;
  const listedPct = (data.summary.floor.listed_count / data.summary.hero.active_supply) * 100;

  const selectRange = (id) => {
    setRange(id);
    const option = RANGE_OPTIONS.find((item) => item.id === id);
    if (option?.defaultGranularity) setGranularity(option.defaultGranularity);
  };

  const openFullscreen = () => {
    const element = tradingRef.current;
    if (element?.requestFullscreen) element.requestFullscreen().catch(() => {});
  };

  return (
    <div className="page-stack market-page">
      <SectionHeading category="market">Market</SectionHeading>

      <section className="snapshot-strip market-snapshot" aria-label="Market statistics">
        <SnapshotStat label="Floor" value={formatSol(data.summary.floor.floor_sol, 3)} />
        <SnapshotStat label="Listed" value={formatInt(data.summary.floor.listed_count)} sub={`${formatPercent(listedPct)} of active supply`} />
        <SnapshotStat label={`${periodLabel} Sales`} value={formatInt(period.sales)} />
        <SnapshotStat label={`${periodLabel} Volume`} value={formatSol(period.volume, 0)} />
      </section>

      <section className="trading-panel" ref={tradingRef}>
        <div className="trading-toolbar">
          <div className="trading-title-group">
            <strong>Floor & Listings</strong>
            <span className="interval-label">Interval</span>
            <div className="interval-control" aria-label="Chart interval">
              {GRANULARITY_OPTIONS.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={granularity === item.id ? 'is-active' : ''}
                  aria-pressed={granularity === item.id}
                  onClick={() => setGranularity(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
          <div className="trading-actions">
            <span className="marketplaces-label">Marketplaces:</span>
            <a href="https://www.tensor.trade/trade/guild_saga_heroes" target="_blank" rel="noreferrer">Tensor</a>
            <a href="https://magiceden.io/marketplace/guild_saga_heroes" target="_blank" rel="noreferrer">Magic Eden</a>
            <button type="button" className="secondary-button" onClick={openFullscreen}>Full screen</button>
          </div>
        </div>

        <div className="trading-chart" aria-label="Guild Saga Heroes floor price, volume and listings history">
          <Chart option={chartOption} onInit={installMarketChartInteractions} />
        </div>

        <div className="trading-rangebar" aria-label="Chart time range">
          <div className="range-control trading-range-control">
            {RANGE_OPTIONS.map((item) => (
              <button
                type="button"
                key={item.id}
                className={range === item.id ? 'is-active' : ''}
                aria-pressed={range === item.id}
                onClick={() => selectRange(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <span className="range-context">{GRANULARITY_OPTIONS.find((item) => item.id === granularity)?.name} data</span>
        </div>
      </section>
    </div>
  );
}

function Ownership({ data }) {
  const tiers = data.hero.holder_distribution;
  const totalHolders = tiers.reduce((sum, row) => sum + Number(row.holder_count || 0), 0);
  const small = tiers.find((row) => row.tier === '1-4');
  const large = tiers.filter((row) => ['50-99', '100+'].includes(row.tier));
  const largeHolders = large.reduce((sum, row) => sum + Number(row.holder_count || 0), 0);
  const largeSupply = large.reduce((sum, row) => sum + Number(row.supply_pct || 0), 0);
  const [ownershipWidgetsStacked, setOwnershipWidgetsStacked] = useState(() => window.matchMedia('(max-width: 780px)').matches);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 780px)');
    const syncLayout = () => setOwnershipWidgetsStacked(media.matches);
    syncLayout();
    media.addEventListener('change', syncLayout);
    return () => media.removeEventListener('change', syncLayout);
  }, []);

  const ownershipOption = useMemo(
    () => makeOwnershipOption(tiers, ownershipWidgetsStacked),
    [tiers, ownershipWidgetsStacked],
  );

  const rows = data.hero.quest_activity;
  const active7 = Number(rows.find((row) => row.bucket === 'Active 0–7d')?.heroes || 0);
  const active30 = rows.slice(0, 2).reduce((sum, row) => sum + Number(row.heroes || 0), 0);
  const idleYear = Number(rows.find((row) => row.bucket === 'Idle 1+ year')?.heroes || 0);
  const neverQuested = Number(rows.find((row) => row.bucket === 'Never quested')?.heroes || 0);
  const idleYearCombined = idleYear + neverQuested;
  const staked = Number(data.summary.hero.staked_heroes || 0);
  const stakingOption = useMemo(() => makeStakingOption(rows), [rows]);

  return (
    <div className="page-stack ownership-page">
      <SectionHeading category="ownership" id="ownership-title">Ownership</SectionHeading>

      <div className="ownership-layout">
        <section className="snapshot-strip joint-snapshot ownership-snapshot ownership-stats" aria-label="Ownership statistics">
          <SnapshotStat label="Active Supply" value={formatInt(data.summary.hero.active_supply)} />
          <SnapshotStat label="Holders" value={formatInt(totalHolders)} />
          <SnapshotStat label="1–4 Heroes" value={formatInt(small?.holder_count)} sub={`${formatPercent(totalHolders ? Number(small?.holder_count || 0) / totalHolders * 100 : 0)} of holders`} />
          <SnapshotStat label="50+ Heroes" value={formatInt(largeHolders)} sub={`${formatPercent(largeSupply)} of active supply`} />
        </section>

        <section className="snapshot-strip joint-snapshot staking-snapshot staking-stats" aria-label="Staking and quest statistics">
          <SnapshotStat label="Staked" value={formatInt(staked)} sub={`${formatPercent(data.summary.hero.staked_supply_pct)} of active supply`} />
          <SnapshotStat label="Active ≤7d" value={formatInt(active7)} sub={`${formatPercent(staked ? active7 / staked * 100 : 0)} of staked`} />
          <SnapshotStat label="Active ≤30d" value={formatInt(active30)} sub={`${formatPercent(staked ? active30 / staked * 100 : 0)} of staked`} />
          <SnapshotStat label="Idle 1+ Year" value={formatInt(idleYearCombined)} sub={`${formatPercent(staked ? idleYearCombined / staked * 100 : 0)} of staked`} />
        </section>

        <section className="ownership-staked-meter" aria-label={`Staked: ${formatPercent(data.summary.hero.staked_supply_pct)} of active supply`}>
          <div className="ownership-staked-track">
            <div className="ownership-staked-fill" style={{ width: `${Math.max(0, Math.min(100, Number(data.summary.hero.staked_supply_pct || 0)))}%` }}>
              <strong>Staked</strong>
              <span>{formatPercent(data.summary.hero.staked_supply_pct)}</span>
            </div>
          </div>
        </section>

        <section className="joint-domain-panel ownership-chart-panel" aria-labelledby="holder-distribution-title">
          <div className="joint-chart-head">
            <strong id="holder-distribution-title">Holder Distribution</strong>
            <span>Share of holders vs share of active supply</span>
          </div>
          <div className="domain-chart joint-domain-chart"><Chart option={ownershipOption} /></div>
        </section>

        <section className="joint-domain-panel staking-chart-panel" aria-labelledby="quest-activity-title">
          <div className="joint-chart-head">
            <strong id="quest-activity-title">Quest Activity</strong>
            <span>Time since the last qualifying quest restart</span>
          </div>
          <div className="domain-chart joint-domain-chart"><Chart option={stakingOption} /></div>
        </section>
      </div>
    </div>
  );
}

function Collection({ data }) {
  const launch = data.launch;
  const hero = data.hero;
  const market = data.market;
  const mintPhaseOption = useMemo(() => makeMintPhaseOption(launch.mint_phases), [launch.mint_phases]);
  const mintWalletOption = useMemo(() => makeMintWalletOption(launch.public_mint_distribution), [launch.public_mint_distribution]);
  const resaleOption = useMemo(() => makeFirstResaleOption(market.first_resale_timing), [market.first_resale_timing]);
  const burnOption = useMemo(() => makeBurnHistoryOption(hero.burn_history), [hero.burn_history]);
  const rarityOption = useMemo(() => makeBurnRarityOption(hero.burned_by_rarity), [hero.burned_by_rarity]);

  return (
    <div className="page-stack domain-page collection-page">
      <SectionHeading category="collection">Collection</SectionHeading>

      <section className="snapshot-strip snapshot-strip-four">
        <SnapshotStat label="Original Supply" value="10,000" />
        <SnapshotStat label="Public Mint" value={formatInt(launch.kpis.public_mint_supply)} />
        <SnapshotStat label="Burned" value={formatInt(hero.kpis.burned)} />
        <SnapshotStat label="Active Supply" value={formatInt(hero.kpis.active_supply)} />
      </section>

      <section className="collection-chapter collection-origin-chapter">
        <div className="chapter-heading">
          <div>
            <h3>Mint</h3>
            <p>Feb 25, 2022 · {formatSol(launch.kpis.mint_price_sol, 1)} · {formatInt(launch.kpis.unique_public_minters)} public minters</p>
          </div>
          <div className="chapter-facts">
            <span><strong>{formatInt(launch.kpis.public_mint_supply)}</strong> public Heroes</span>
            <span><strong>100</strong> separately allocated</span>
          </div>
        </div>
        <div className="collection-three-col">
          <div className="domain-chart-block compact-chart-block origin-chart-block">
            <div className="section-bar"><span className="section-title">Mint phases</span></div>
            <div className="domain-chart"><Chart option={mintPhaseOption} /></div>
          </div>
          <div className="domain-chart-block compact-chart-block origin-chart-block">
            <div className="section-bar"><span className="section-title">Wallet distribution</span></div>
            <div className="domain-chart"><Chart option={mintWalletOption} /></div>
          </div>
          <div className="domain-chart-block compact-chart-block origin-chart-block">
            <div className="section-bar"><span className="section-title">Time to first resale</span></div>
            <div className="section-note">{formatInt(data.summary.market.heroes_ever_sold)} Heroes ever sold</div>
            <div className="domain-chart"><Chart option={resaleOption} /></div>
          </div>
        </div>
      </section>

      <section className="collection-chapter burns-chapter">
        <div className="chapter-heading">
          <div><h3>Burns</h3><p>{formatInt(hero.kpis.burned)} Heroes permanently removed from supply</p></div>
        </div>
        <div className="collection-burn-grid">
          <div className="domain-chart-block compact-chart-block burn-chart-block burn-history-block">
            <div className="section-bar"><span className="section-title">Burn history</span></div>
            <div className="domain-chart"><Chart option={burnOption} onInit={installBurnLineCrosshair} /></div>
          </div>
          <div className="domain-chart-block compact-chart-block burn-chart-block burn-rarity-block">
            <div className="section-bar"><span className="section-title">Burned by rarity</span></div>
            <div className="domain-chart"><Chart option={rarityOption} /></div>
          </div>
          <div className="burn-fire-panel">
            <div className="burn-fire-frame">
              <img
                src="https://media1.tenor.com/m/J_tVwZX-xpoAAAAd/guild-saga-fire.gif"
                alt="Guild Saga tavern burning"
                loading="lazy"
              />
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function Funding({ data }) {
  const treasury = data.treasury;
  const solOption = useMemo(() => makeSolConversionOption(treasury.conversion_history), [treasury.conversion_history]);
  const usdcOption = useMemo(() => makeUsdcConversionOption(treasury.conversion_history), [treasury.conversion_history]);
  const splitOption = useMemo(() => makeTreasurySplitOption(treasury.initial_mint_treasury_split), [treasury.initial_mint_treasury_split]);
  const royaltiesOption = useMemo(() => makeRoyaltiesOption(data.market.monthly_activity), [data.market.monthly_activity]);

  return (
    <div className="page-stack domain-page economy-page">
      <SectionHeading category="funding">Funding</SectionHeading>

      <section className="snapshot-strip snapshot-strip-three">
        <SnapshotStat label="Public Mint Proceeds" value={formatSol(data.summary.launch.public_mint_proceeds_sol, 0)} />
        <SnapshotStat label="Guild Saga Royalties" value={formatSol(data.summary.market.guild_saga_royalties_sol, 2)} />
        <SnapshotStat label="Mint + Royalties" value={formatSol(data.summary.market.mint_plus_royalties_sol, 2)} />
      </section>

      <section className="domain-chart-block">
        <div className="section-bar">
          <span className="section-title">Royalties over time</span>
        </div>
        <div className="section-note">Monthly royalties received from secondary-market sales</div>
        <div className="domain-chart economy-royalties-chart"><Chart option={royaltiesOption} onInit={installRoyaltiesLineCrosshair} /></div>
      </section>

      <section className="economy-section">
        <div className="section-bar"><span className="section-title">Mint Proceeds</span><strong>Feb 26, 2022</strong></div>
        <div className="mint-split-layout">
          <div className="treasury-split-chart"><Chart option={splitOption} /></div>
          <div className="economy-copy">
            <p>
              One day after the mint, the mint-payee wallet distributed <strong>14,850 SOL</strong>:
              <strong> 14,107.720 SOL (95%)</strong> through one intermediate address and
              <strong> 742.512 SOL (5%)</strong> through another.
            </p>
            <p>
              Both intermediates behaved like pass-through exchange/deposit infrastructure, forwarding essentially
              their full balances within minutes rather than acting as long-term treasury wallets.
            </p>
            <p>
              Shortly after the 95% branch entered exchange-side infrastructure, a Coinbase-labeled address sent
              <strong> 14,109.220490 SOL</strong> to <code>2u3ao...</code>, a wallet later tied to Guild Saga financial
              operations. The timing and amount are strongly correlated, but the exchange hop prevents proof that it
              was literally the same SOL.
            </p>
            <p>About 90 minutes later, <code>2u3ao...</code> began making large SOL → USDC conversions.</p>
          </div>
        </div>
      </section>

      <section className="economy-conversion-section economy-conversion-clean">
        <div className="economy-two-charts">
          <div className="economy-chart-card">
            <div className="economy-chart-head">
              <strong>SOL Converted to USDC</strong>
              <span>Monthly SOL sold and cumulative SOL sold</span>
            </div>
            <div className="domain-chart economy-conversion-chart"><Chart option={solOption} onInit={installConversionLineCrosshair} /></div>
          </div>
          <div className="economy-chart-card">
            <div className="economy-chart-head">
              <strong>USDC Purchased</strong>
              <span>Monthly USDC received and cumulative USDC purchased</span>
            </div>
            <div className="domain-chart economy-conversion-chart"><Chart option={usdcOption} onInit={installConversionLineCrosshair} /></div>
          </div>
        </div>
      </section>

      <section className="economy-context">
        <div className="section-bar"><span className="section-title">What the chain can and cannot tell us</span></div>
        <div className="economy-copy economy-copy-wide">
          <p>
            Some wallet activity passes through exchanges or addresses that cannot be tied to a specific person, so the
            amounts sold do not show the full story.
          </p>
          <p>
            A March 2022 Guild Saga AMA stated that NFT mint proceeds were being used to expand the team, which was
            described as growing from roughly <strong>9 to 16 people</strong> shortly after the mint. On-chain evidence can
            establish movement of funds, but not the company's complete off-chain expenditure or payroll.
          </p>
        </div>
      </section>

      <section className="economy-context">
        <div className="section-bar"><span className="section-title">Project-connected wallet activity</span></div>
        <div className="economy-copy economy-copy-wide">
          <p>
            The later chain history identifies three especially important project-connected wallets:
            <code> 2u3ao...</code>, <code>3xQ...</code>, and <code>Fgk8...</code>.
          </p>
          <p>
            <code>3xQ...</code> is independently connected to Guild Saga through custody of <strong>24 custom genesis Heroes</strong>.
            In May 2022, <code>2u3ao...</code> sent substantial USDC to project-connected wallets, including
            <strong> 120,361 USDC to 3xQ...</strong>.
          </p>
          <p>
            In August 2022, <code>3xQ...</code> transferred <strong>57,000 USDC, 150 SOL, seven Guild Saga Heroes,
            and four additional NFT-like assets</strong> to <code>Fgk8...</code>.
          </p>
          <p>
            Full-history traces show repeated two-way SOL, USDC and JitoSOL transfers among project-connected wallets
            through 2026. Between <code>3xQ...</code> and <code>Fgk8...</code> alone, the traced history contains approximately
            <strong> 1.40 million USDC</strong> in direct transfers across both directions, alongside thousands of SOL/JitoSOL
            in additional movements.
          </p>
          <p>
            These flows establish an ongoing project-connected finance/custody network. They do <strong>not</strong> establish
            that all wallets were controlled by the same individual, or that every asset and transaction in those wallets
            belonged to Guild Saga.
          </p>
        </div>
      </section>
    </div>
  );
}

function TeamProfileCard({ member }) {
  const screenName = member.handle.replace(/^@/, '');
  const platform = member.platform || 'X';
  const profileUrl = member.href || `https://x.com/${screenName}`;
  const avatarSrc = member.avatarSrc || `https://unavatar.io/x/${screenName}`;
  const initials = member.name
    .split(/\s+/)
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();

  return (
    <article className="team-card">
      <a
        className="team-avatar-link"
        href={profileUrl}
        target="_blank"
        rel="noreferrer"
        aria-label={`${member.name} on ${platform}`}
      >
        <span className="team-avatar-fallback" aria-hidden="true">{initials}</span>
        <img
          className="team-avatar"
          src={avatarSrc}
          alt={`${member.name} profile picture`}
          loading="lazy"
          decoding="async"
        />
      </a>
      <div className="team-card-copy">
        <strong className="team-member-name">{member.name}</strong>
        <a
          className="team-handle"
          href={profileUrl}
          target="_blank"
          rel="noreferrer"
          aria-label={`${member.handle} on ${platform}`}
        >
          {member.handle}
        </a>
        <p className="team-role">{member.roles}</p>
      </div>
    </article>
  );
}

function Team() {
  return (
    <div className="page-stack team-page">
      <SectionHeading category="team">Team</SectionHeading>
      <div className="team-grid" aria-label="Guild Saga team">
        {TEAM_MEMBERS.map((member) => <TeamProfileCard key={member.handle} member={member} />)}
      </div>

      <section className="team-subsection">
        <div className="section-bar"><span className="section-title">Credits</span></div>
        <div className="team-grid team-contributor-grid" aria-label="Guild Saga credits">
          {TEAM_CREDITS.map((member) => <TeamProfileCard key={member.handle} member={member} />)}
        </div>
      </section>

      <section className="team-subsection team-official-section">
        <div className="section-bar"><span className="section-title">Official links</span></div>
        <div className="team-official-links">
          {TEAM_OFFICIAL_LINKS.map((link) => (
            <a key={link.href} className="team-official-link" href={link.href} target="_blank" rel="noreferrer">
              <span className="team-official-link-icon" aria-hidden="true">
                {link.icon === 'epic' ? <EpicGamesIcon /> : <img src={link.icon} alt="" />}
              </span>
              <span className="team-official-link-copy">
                <strong>{link.label}</strong>
                <span>{link.detail}</span>
              </span>
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}

function SiteFooter() {
  return (
    <footer className="site-footer">
      <a className="data-page-link" href="#data">Data & Methodology</a>
    </footer>
  );
}

function DataPage({ onBack }) {
  const heroSourceStripRef = useRef(null);
  const [heroSourcePreviewCount, setHeroSourcePreviewCount] = useState(HERO_SOURCE_PREVIEW_IDS.length);

  useEffect(() => {
    const strip = heroSourceStripRef.current;
    if (!strip) return undefined;

    const updatePreviewCount = () => {
      const styles = window.getComputedStyle(strip);
      const paddingLeft = Number.parseFloat(styles.paddingLeft) || 0;
      const paddingRight = Number.parseFloat(styles.paddingRight) || 0;
      const availableWidth = Math.max(0, strip.clientWidth - paddingLeft - paddingRight);
      const fit = Math.max(1, Math.floor((availableWidth + HERO_SOURCE_PREVIEW_GAP) / (HERO_SOURCE_WIDTH + HERO_SOURCE_PREVIEW_GAP)));
      setHeroSourcePreviewCount(Math.min(HERO_SOURCE_PREVIEW_IDS.length, fit));
    };

    updatePreviewCount();

    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(updatePreviewCount);
      observer.observe(strip);
      return () => observer.disconnect();
    }

    window.addEventListener('resize', updatePreviewCount);
    return () => window.removeEventListener('resize', updatePreviewCount);
  }, []);

  const files = [
    ['summary.json', 'Current headline collection, market, launch and freshness metrics.'],
    ['hero-state.json', 'Holder tiers, staking and quest state, burn history, and burned rarity.'],
    ['floor-listings.json', 'Historical floor price and listing-count observations.'],
    ['market-history.json', 'Secondary-market activity, first-resale timing, and royalty history.'],
    ['market-daily.json', 'Daily secondary sales and SOL volume used by the market explorer.'],
    ['launch.json', 'Original mint history, mint phases, and public mint distribution.'],
    ['treasury.json', 'Mint-proceeds split and project-connected economic aggregates.'],
  ];

  const metricDefinitions = [
    ['Active supply', 'Heroes in canonical state that have not been explicitly burned.'],
    ['Holders', 'Unique current beneficial owners after known staking or custody state is resolved, rather than a raw count of token accounts.'],
    ['Staked', 'Heroes currently in the validated World Mode staking state.'],
    ['Quest activity', 'Time since the last qualifying quest restart for a currently staked Hero. Time buckets can age forward without a new transaction.'],
    ['Sales and volume', 'Validated secondary sales from supported marketplace program paths, deduplicated by transaction signature and Hero mint.'],
    ['Floor and listings', 'One marketplace snapshot per UTC date. Missing dates are left missing rather than filled with invented values.'],
    ['Royalties', 'Creator royalty value associated with validated secondary sales.'],
    ['First resale', 'The first validated secondary sale after a Hero entered circulation.'],
    ['Funding activity', 'Observed on-chain transfers and conversions involving project-connected wallets. A transfer relationship does not by itself prove common ownership or off-chain spending.'],
  ];

  const implementationPaths = [
    ['collector/', 'Solana normalization, World Mode state rules, ownership logic, burn detection, and marketplace parsers.'],
    ['cloudflare/webhook-inbox/', 'Worker, D1 schema, raw webhook inbox, authentication, and Cron Trigger dispatch.'],
    ['.github/workflows/', 'Guarded dry-run and production entry points with shared release concurrency.'],
    ['scripts/production_pipeline.py', 'Live Hero and market orchestration, validation, commit, deployment proof, and exact D1 acknowledgement.'],
    ['scripts/floor_listings_pipeline.py', 'Independent daily marketplace snapshot, validation, release, and deployment proof.'],
    ['scripts/build_dashboard_data.py', 'Deterministic builder for the JSON consumed by the website.'],
    ['data/baseline/ + data/state/ + data/history/', 'Immutable baseline data, compact live state/checkpoints, and append-only or daily history.'],
    ['site/', 'React frontend, public JSON, Hero art, and presentation logic.'],
    ['tests/', 'Parser, state-machine, pipeline, baseline-integrity, and release-safety regression tests.'],
  ];

  return (
    <div className="data-page page-stack">
      <section className="data-page-heading">
        <div>
          <span className="eyebrow">Documentation</span>
          <h1>Data & Methodology</h1>
          <p>
            How the site derives, validates, publishes, and presents Guild Saga Heroes data. This page is intended
            to provide enough implementation context to understand the system or adapt the same architecture to
            another Solana collection without reverse-engineering the entire repository.
          </p>
        </div>
        <div className="data-header-actions">
          <a className="data-github-link" href={GITHUB_REPO_URL} target="_blank" rel="noreferrer">
            <span className="data-github-icon" aria-hidden="true">
              <img src="/assets/icons/brand-github.svg" alt="" />
            </span>
            <span>View source on GitHub</span>
          </a>
          <button className="secondary-button" type="button" onClick={onBack}>Back to analytics</button>
        </div>
      </section>

      <section className="data-summary-grid" aria-label="Data system at a glance">
        <article className="data-summary-card">
          <span className="data-card-kicker">Browser delivery</span>
          <strong>Static JSON</strong>
          <p>The public browser reads versioned JSON and image assets. It does not query Solana or marketplace APIs directly.</p>
        </article>
        <article className="data-summary-card">
          <span className="data-card-kicker">Hero + market</span>
          <strong>Twice hourly</strong>
          <p>Raw Solana deliveries accumulate continuously and Cloudflare starts the guarded production reducer at :00 and :30 UTC.</p>
        </article>
        <article className="data-summary-card">
          <span className="data-card-kicker">Floor + listings</span>
          <strong>Daily close snapshot</strong>
          <p>Collection stats are sampled at 23:30 UTC, with a 23:50 recovery firing. At most one row is written for each UTC date.</p>
        </article>
      </section>

      <section className="data-method-section">
        <div className="data-method-heading">
          <span className="eyebrow">Published interface</span>
          <h2>Public data files</h2>
          <p>
            The frontend is intentionally separated from collection infrastructure. These are the public files the site consumes,
            so another client can inspect the same published state without reproducing the collector.
          </p>
        </div>
        <div className="data-file-list">
          {files.map(([name, description]) => (
            <a key={name} href={`/data/${name}`} target="_blank" rel="noreferrer" className="data-file-row">
              <code>{name}</code>
              <span>{description}</span>
              <b aria-hidden="true">↗</b>
            </a>
          ))}
        </div>
      </section>

      <section className="data-method-section">
        <div className="data-method-heading">
          <span className="eyebrow">Architecture</span>
          <h2>How data reaches the website</h2>
          <p>
            There are three deliberately separate data paths. Live state is event-driven, floor/listings is a daily marketplace
            snapshot, and historical facts are rebuilt from immutable local baselines. Keeping these domains separate prevents
            one unavailable provider from corrupting unrelated data.
          </p>
        </div>

        <div className="data-flow-grid">
          <article className="data-flow-card">
            <span className="data-flow-number">01</span>
            <h3>Hero + market state</h3>
            <div className="data-flow-steps" aria-label="Live Hero and market pipeline">
              <span>Helius raw webhook</span><i>→</i><span>Cloudflare Worker + D1</span><i>→</i><span>30-minute Cron Trigger</span><i>→</i><span>GitHub Actions</span><i>→</i><span>Python reducers + validators</span><i>→</i><span>Commit + Pages proof</span><i>→</i><span>D1 ACK</span>
            </div>
            <p>Raw deliveries are stored first. Guild Saga state is interpreted later by tested deterministic Python code.</p>
          </article>

          <article className="data-flow-card">
            <span className="data-flow-number">02</span>
            <h3>Floor + listings</h3>
            <div className="data-flow-steps" aria-label="Floor and listings pipeline">
              <span>Cloudflare daily cron</span><i>→</i><span>GitHub Actions</span><i>→</i><span>Magic Eden collection stats</span><i>→</i><span>One UTC row</span><i>→</i><span>Validation</span><i>→</i><span>Commit + Pages proof</span>
            </div>
            <p>The 23:50 firing is recovery only. If 23:30 succeeded, the second run proves the current row and becomes a no-op.</p>
          </article>

          <article className="data-flow-card">
            <span className="data-flow-number">03</span>
            <h3>Historical + static</h3>
            <div className="data-flow-steps" aria-label="Historical data pipeline">
              <span>Immutable baseline files</span><i>→</i><span>Deterministic builder</span><i>→</i><span>Public JSON</span>
            </div>
            <p>Mint history, rarity, historical sales, and known funding history do not require a periodic full-chain rescan during normal production.</p>
          </article>
        </div>
      </section>

      <section className="data-method-section">
        <div className="data-method-heading">
          <span className="eyebrow">Infrastructure</span>
          <h2>What Cloudflare does</h2>
          <p>
            Cloudflare is more than the web host. It is also the durable handoff and production clock between live Solana delivery
            and the repository's deterministic data pipeline.
          </p>
        </div>
        <div className="data-info-grid">
          <article className="data-info-card"><h3>Worker</h3><p>Authenticates raw Helius webhook requests, deduplicates them by transaction signature, writes the original payload to D1, and returns quickly. The Worker deliberately does not calculate Guild Saga metrics.</p></article>
          <article className="data-info-card"><h3>D1 inbox</h3><p>Acts as the durable pending-event queue. A stable receive-time watermark freezes each production batch, so events arriving during a run are left for the next run instead of leaking into the selected snapshot.</p></article>
          <article className="data-info-card"><h3>Cron Triggers</h3><p>Cloudflare is the production scheduler. Only allow-listed cron strings can emit the two repository-dispatch events used by the live pipeline and the daily floor/listings pipeline.</p></article>
          <article className="data-info-card"><h3>Pages</h3><p>Serves the React application and static JSON. Production does not acknowledge a live D1 batch until the pipeline verifies that the exact committed JSON is actually available from the corresponding Pages deployment.</p></article>
        </div>
      </section>

      <section className="data-method-section">
        <div className="data-method-heading">
          <span className="eyebrow">Correctness</span>
          <h2>Release safety and failure behavior</h2>
          <p>The pipeline is intentionally conservative. A stale site is preferred to a fabricated or partially published state.</p>
        </div>
        <div className="data-safety-grid">
          <div><strong>Stable batches</strong><span>Each live run processes a frozen D1 snapshot. Later arrivals remain pending.</span></div>
          <div><strong>Idempotent input</strong><span>Duplicate webhook delivery is harmless because signatures are unique in the inbox and reducers keep deterministic cursors.</span></div>
          <div><strong>Fail closed</strong><span>Stale state cannot overwrite newer state, ambiguous same-slot ownership conflicts are rejected, and missing marketplace fields do not create guessed values.</span></div>
          <div><strong>Checkpoint discipline</strong><span>A checkpoint advances only after collection, reduction, JSON generation, and validation all succeed.</span></div>
          <div><strong>Exact release inventory</strong><span>Production stages only explicit allow-listed files and refuses a moved main branch instead of force-pushing over it.</span></div>
          <div><strong>Deployment proof</strong><span>The exact committed public JSON must be observed on Cloudflare Pages before a live webhook batch is acknowledged.</span></div>
          <div><strong>Crash recovery</strong><span>A committed batch manifest lets a later runner prove the already-published release and safely finish the ACK without reducing a different batch.</span></div>
          <div><strong>No production race</strong><span>Live updates and daily floor/listings use the same GitHub Actions concurrency group, so both cannot write main at once.</span></div>
        </div>
      </section>

      <section className="data-method-section">
        <div className="data-method-heading">
          <span className="eyebrow">Definitions</span>
          <h2>How the main metrics are interpreted</h2>
          <p>These definitions matter because blockchain accounts, custody addresses, and rolling time windows are not always equivalent to the plain-language metric shown to a reader.</p>
        </div>
        <div className="data-definition-grid">
          {metricDefinitions.map(([name, definition]) => (
            <article key={name} className="data-definition-card"><h3>{name}</h3><p>{definition}</p></article>
          ))}
        </div>
      </section>

      <section className="data-method-section">
        <div className="data-method-heading">
          <span className="eyebrow">Freshness + browser state</span>
          <h2>What is live, local, and remembered</h2>
        </div>
        <div className="data-info-grid data-info-grid-three">
          <article className="data-info-card"><h3>Updated indicator</h3><p>The site checks both the published JSON and a small public Cloudflare freshness endpoint every five minutes while the tab is active. Successful production jobs write heartbeat timestamps even when there is no new Guild Saga activity, so a quiet collection is not mistaken for a broken pipeline. The live pipeline gets a six-hour tolerance; the once-daily floor/listings pipeline gets its normal daily interval plus roughly six hours. Failed browser requests do not downgrade a previously verified state. Hover the colored status dot to see the last successful live and floor/listings checks in your local time. Confirmed lag turns the indicator yellow, and both published data domains more than 30 days stale turns it red.</p></article>
          <article className="data-info-card"><h3>Selected Hero</h3><p>The selected Hero and PFP background preference are stored in normal first-party browser local storage. No account, wallet connection, download permission, or browser notification permission is required.</p></article>
          <article className="data-info-card"><h3>Since your last visit</h3><p>A compact KPI snapshot is stored locally in the browser. On a later visit, meaningful differences are calculated against the newly published data. Session storage keeps that recap stable across refreshes during the same tab session.</p></article>
        </div>
      </section>

      <section className="data-method-section">
        <div className="data-method-heading">
          <span className="eyebrow">Reimplementation guide</span>
          <h2>Adapting the architecture to another Solana collection</h2>
          <p>
            Most of the release machinery can stay unchanged. The collection-specific contract is concentrated in a smaller set of inputs and parsers that an implementation must replace deliberately rather than infer from the UI.
          </p>
        </div>
        <div className="data-adapt-grid">
          <article><strong>Replace collection identity</strong><p>Mint inventory, metadata and trait schema, public collection symbol, image assets, project links, and display copy.</p></article>
          <article><strong>Replace on-chain semantics</strong><p>Program IDs, instruction discriminators, account positions, staking/custody rules, burn rules, and any collection-specific beneficial-owner resolution.</p></article>
          <article><strong>Replace market contracts</strong><p>Supported marketplace program layouts, collection filters, sale-price parsing, royalty interpretation, and floor/listing source semantics.</p></article>
          <article><strong>Establish a baseline</strong><p>Canonical Hero state, historical sales, rarity, mint history, checkpoints, and deterministic fixtures for the date where live incremental processing begins.</p></article>
          <article><strong>Keep the safety boundary</strong><p>Raw durable inbox, stable batch watermark, deterministic reducers, fail-closed validation, exact-file releases, deployment proof, and acknowledgement only after proof.</p></article>
          <article><strong>Write collection fixtures</strong><p>Use known real transactions to lock parser behavior and state transitions before production automation is enabled.</p></article>
        </div>

        <div className="data-implementation-map">
          <h3>Repository map</h3>
          {implementationPaths.map(([path, purpose]) => (
            <div key={path} className="data-implementation-row"><code>{path}</code><span>{purpose}</span></div>
          ))}
        </div>
      </section>

      <section className="data-method-section pfp-method-section">
        <div className="data-method-heading">
          <span className="eyebrow">Site tool</span>
          <h2>Hero PFP Creator</h2>
          <p>
            For this site, each of the 10,000 Heroes has a transparent 65 × 70 pixel source image. These files
            preserve the Hero art at a 1:1 pixel scale, and the compositing and enlargement happen
            locally in your browser. In the NFT artwork, each art pixel is displayed as a 5 × 5 block, so the
            65 × 70 files are the underlying pixel-art representation rather than the NFT&apos;s display resolution.
          </p>
        </div>

        <div className="pfp-method-copy">
          <p>
            For a body PFP, the full 65 × 70 transparent image is drawn over the selected background color at
            1:1 pixel scale, then enlarged 10× to 650 × 700. For a face PFP, the creator takes a 26 × 26 crop
            starting at x20 / y8 and enlarges it 30× to 780 × 780.
          </p>
          <p>
            Both paths disable image smoothing and use nearest-neighbor scaling, preserving the hard pixel edges
            instead of blurring them. Each Hero starts with a mapped default background color based on that NFT&apos;s
            Background trait, and the color picker can override it before download.
          </p>
        </div>

        <div className="hero-source-access">
          <div>
            <h3>10,000 transparent source PNGs</h3>
            <p>
              Every Hero from #0 through #9999 is published in the repository as a transparent 65 × 70 PNG
              with the art preserved at a 1:1 pixel scale. The files are grouped into folders of 1,000 so the
              full set remains straightforward to browse directly from GitHub.
            </p>
          </div>
          <a
            className="data-source-link"
            href={HERO_SOURCE_GITHUB_URL}
            target="_blank"
            rel="noreferrer"
          >
            Browse all 10,000 PNGs on GitHub <span aria-hidden="true">↗</span>
          </a>
        </div>

        <div className="hero-source-preview">
          <div
            ref={heroSourceStripRef}
            className="hero-source-strip"
            aria-label={`Heroes 0 through ${Math.max(0, heroSourcePreviewCount - 1)} at 1:1 pixel scale`}
          >
            {HERO_SOURCE_PREVIEW_IDS.slice(0, heroSourcePreviewCount).map((heroId) => (
              <div key={heroId} className="hero-source-sprite">
                <img
                  src={getHeroSourceUrl(heroId)}
                  alt={`Guild Hero #${heroId} transparent source preview`}
                  width="65"
                  height="70"
                  decoding="async"
                />
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const [data, setData] = useState(null);
  const [dataVerifiedAt, setDataVerifiedAt] = useState(null);
  const [pipelineFreshness, setPipelineFreshness] = useState(null);
  const [error, setError] = useState(null);
  const [activeSection, setActiveSection] = useState('overview');
  const [showDataPage, setShowDataPage] = useState(() => window.location.hash === '#data');
  const [heroIdentityCandidate, setHeroIdentityCandidate] = useState(readHeroPreference);
  const [returnRecap, setReturnRecap] = useState(null);
  const [returnRecapExpanded, setReturnRecapExpanded] = useState(false);
  const navScrollLockRef = useRef(null);
  const dataRefreshInFlightRef = useRef(false);
  const lastDataRefreshAtRef = useRef(0);
  const hasLoadedDataRef = useRef(false);
  const returnRecapInitializedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    const refreshPublishedData = async () => {
      if (dataRefreshInFlightRef.current) return;
      dataRefreshInFlightRef.current = true;

      try {
        const [dataResult, freshnessResult] = await Promise.allSettled([
          loadPublishedData(),
          loadPipelineFreshness(),
        ]);
        if (cancelled) return;

        const verifiedAt = Date.now();
        lastDataRefreshAtRef.current = verifiedAt;

        if (dataResult.status === 'fulfilled') {
          setData(dataResult.value);
          setDataVerifiedAt(verifiedAt);
          hasLoadedDataRef.current = true;
          setError(null);
        } else if (!hasLoadedDataRef.current) {
          setError(dataResult.reason);
        } else {
          // Keep the last fully verified snapshot visible. A transient refresh
          // failure is not evidence that the published data itself is stale.
          console.warn('Background data refresh failed; keeping last verified snapshot.', dataResult.reason);
        }

        if (freshnessResult.status === 'fulfilled') {
          setPipelineFreshness(freshnessResult.value);
        } else {
          // Operational freshness is deliberately fail-soft in the browser. If
          // Cloudflare cannot be reached, retain the last verified heartbeat
          // state rather than presenting an outage that has not been proven.
          console.warn('Pipeline freshness check failed; keeping last verified status.', freshnessResult.reason);
        }
      } finally {
        dataRefreshInFlightRef.current = false;
      }
    };

    refreshPublishedData();

    const intervalId = window.setInterval(() => {
      if (document.visibilityState === 'visible') refreshPublishedData();
    }, DATA_REFRESH_INTERVAL_MS);

    const onVisibilityChange = () => {
      if (
        document.visibilityState === 'visible'
        && Date.now() - lastDataRefreshAtRef.current >= DATA_REFRESH_INTERVAL_MS
      ) {
        refreshPublishedData();
      }
    };

    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, []);


  useEffect(() => {
    if (!data) return;
    const current = buildReturnSnapshot(data);

    // Auto-refreshing the site's public JSON must not turn same-session changes
    // into a new "since your last visit" recap. Establish that recap once per
    // page session, keep it stable, but continually advance the local baseline
    // so the next genuinely new visit starts from the latest data the user saw.
    if (returnRecapInitializedRef.current) {
      safeStorageWrite(window.localStorage, RETURN_SNAPSHOT_STORAGE_KEY, current);
      return;
    }
    returnRecapInitializedRef.current = true;

    const currentFingerprint = returnSnapshotFingerprint(current);
    const sessionRecap = safeStorageRead(window.sessionStorage, RETURN_RECAP_SESSION_KEY);

    if (
      sessionRecap?.version === RETURN_SNAPSHOT_VERSION
      && Array.isArray(sessionRecap.changes)
      && sessionRecap.changes.length
    ) {
      setReturnRecap(sessionRecap);
      safeStorageWrite(window.localStorage, RETURN_SNAPSHOT_STORAGE_KEY, current);
      return;
    }

    const previous = safeStorageRead(window.localStorage, RETURN_SNAPSHOT_STORAGE_KEY);
    const previousFingerprint = previous ? returnSnapshotFingerprint(previous) : null;
    const changes = previous && previousFingerprint !== currentFingerprint
      ? buildReturnChanges(previous, current)
      : [];

    const nextRecap = changes.length
      ? {
          version: RETURN_SNAPSHOT_VERSION,
          previousVisitedAt: previous.visitedAt,
          currentFingerprint,
          changes,
        }
      : null;

    if (nextRecap) {
      safeStorageWrite(window.sessionStorage, RETURN_RECAP_SESSION_KEY, nextRecap);
    } else {
      safeStorageRemove(window.sessionStorage, RETURN_RECAP_SESSION_KEY);
    }

    safeStorageWrite(window.localStorage, RETURN_SNAPSHOT_STORAGE_KEY, current);
    setReturnRecap(nextRecap);
    setReturnRecapExpanded(false);
  }, [data]);


  useEffect(() => {
    const onHashChange = () => {
      const isData = window.location.hash === '#data';
      setShowDataPage(isData);
      if (isData) window.scrollTo({ top: 0, behavior: 'auto' });
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  useEffect(() => {
    if (!data || showDataPage) return undefined;
    const updateActive = () => {
      // During a nav-initiated smooth scroll, keep the clicked destination
      // visually selected. Without this lock the scroll spy briefly activates
      // every intermediate section as it passes beneath the header.
      if (navScrollLockRef.current?.id) {
        setActiveSection(navScrollLockRef.current.id);
        return;
      }

      const marker = window.scrollY + 150;
      let current = 'overview';
      const targets = [...new Set(NAV_ITEMS.map((item) => item.target || item.id))];

      for (const target of targets) {
        const element = document.getElementById(target);
        if (!element) continue;
        const documentTop = element.getBoundingClientRect().top + window.scrollY;
        if (documentTop <= marker) current = target;
      }

      // The final section can be too close to the bottom of the document to
      // ever cross the normal marker. Treat reaching the page bottom as the
      // final nav section so Team cannot remain visually stuck on Funding.
      const atDocumentBottom = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 4;
      if (atDocumentBottom && targets.length) current = targets[targets.length - 1];

      setActiveSection(current);
    };
    updateActive();
    window.addEventListener('scroll', updateActive, { passive: true });
    window.addEventListener('resize', updateActive);
    return () => {
      window.removeEventListener('scroll', updateActive);
      window.removeEventListener('resize', updateActive);
    };
  }, [data, showDataPage]);

  const scrollToSection = (id, options = {}) => {
    const move = () => {
      const element = document.getElementById(id);
      if (!element) return;
      const isNavTarget = NAV_ITEMS.some((item) => (item.target || item.id) === id);
      const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

      if (isNavTarget) {
        const lock = { id };
        navScrollLockRef.current = lock;
        setActiveSection(id);

        const releaseLock = () => {
          if (navScrollLockRef.current === lock) navScrollLockRef.current = null;
        };

        if (!reduceMotion && 'onscrollend' in window) {
          window.addEventListener('scrollend', releaseLock, { once: true });
        }
        // Firefox/Chromium smooth scrolls normally finish well before this.
        // This also prevents a stale lock if scrollend is unsupported or the
        // scroll is interrupted.
        window.setTimeout(releaseLock, reduceMotion ? 0 : 1400);
      }

      element.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
      if (options.highlight) {
        element.classList.remove('return-recap-highlight');
        void element.offsetWidth;
        element.classList.add('return-recap-highlight');
        window.setTimeout(() => element.classList.remove('return-recap-highlight'), 1600);
      }
    };

    if (showDataPage) {
      window.history.pushState(null, '', window.location.pathname + window.location.search);
      setShowDataPage(false);
      window.setTimeout(move, 0);
    } else {
      move();
    }
  };

  const backToAnalytics = () => {
    window.history.pushState(null, '', window.location.pathname + window.location.search);
    setShowDataPage(false);
    window.scrollTo({ top: 0, behavior: 'auto' });
  };

  if (error) return <div className="load-state">Failed to load Guild Saga data: {String(error)}</div>;
  if (!data) return <div className="load-state">Loading Guild Saga analytics…</div>;

  const recapChanges = returnRecap?.changes || [];
  const recapSince = formatRecapSinceShort(returnRecap?.previousVisitedAt);

  return (
    <main className="app-shell">
      <header className="site-header">
        <div className="header-inner">
          <button className="brand" type="button" onClick={() => scrollToSection('overview')}>
            <BrandHeroMark candidate={heroIdentityCandidate} />
            <span className="brand-copy"><strong>Guild Saga Heroes</strong><small>Analytics</small></span>
          </button>
          <div className="header-actions">
            <FreshnessChip data={data} verifiedAt={dataVerifiedAt} pipelineFreshness={pipelineFreshness} />
          </div>
        </div>
        <nav className="primary-nav" aria-label="Primary">
          <div className="nav-inner">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`${item.icon === 'home' ? 'nav-home-button ' : ''}${!showDataPage && activeSection === (item.target || item.id) ? 'is-active' : ''}`.trim()}
                aria-label={item.label}
                title={item.label}
                onClick={() => scrollToSection(item.target || item.id)}
              >
                {item.icon === 'home' ? (
                  <HomeIcon />
                ) : (
                  <>
                    <CategoryIcon category={item.category} className="nav-category-icon" />
                    <span className="nav-label">{item.label}</span>
                  </>
                )}
              </button>
            ))}
          </div>
        </nav>
      </header>

      <div className="content-shell">
        {showDataPage ? (
          <DataPage onBack={backToAnalytics} />
        ) : (
          <>
            {recapChanges.length > 0 && (
              <section className="return-recap-banner" aria-label="Changes since your last visit">
                <div className="return-recap-banner-intro">
                  <strong>Since your last visit</strong>
                  <span>{recapChanges.length} {recapChanges.length === 1 ? 'change' : 'changes'}{recapSince ? ` since ${recapSince}` : ''}</span>
                </div>
                <div className="return-recap-banner-content">
                  <div className={`return-recap-banner-changes${returnRecapExpanded ? ' is-expanded' : ''}`}>
                    {recapChanges.slice(0, RETURN_RECAP_VISIBLE_LIMIT).map((change, index) => (
                      <button
                        key={`${change.category}-${change.text}-${index}`}
                        type="button"
                        className="return-recap-banner-item"
                        data-category={change.category.toLowerCase()}
                        onClick={() => scrollToSection(change.section, { highlight: true })}
                      >
                        <CategoryIcon category={change.category.toLowerCase()} className="return-recap-category-icon" />
                        <span className="return-recap-banner-text">{change.text}</span>
                      </button>
                    ))}
                    {recapChanges.length > RETURN_RECAP_VISIBLE_LIMIT && (
                      <button
                        type="button"
                        className="return-recap-banner-item return-recap-banner-more-toggle"
                        aria-expanded={returnRecapExpanded}
                        onClick={() => setReturnRecapExpanded((value) => !value)}
                      >
                        <span className="return-recap-more-icon" aria-hidden="true">
                          <i /><i /><i />
                        </span>
                        <span className="return-recap-banner-text">
                          {returnRecapExpanded ? 'Show less' : `View ${recapChanges.length - RETURN_RECAP_VISIBLE_LIMIT} more`}
                        </span>
                      </button>
                    )}
                    {returnRecapExpanded && recapChanges.slice(RETURN_RECAP_VISIBLE_LIMIT).map((change, index) => (
                      <button
                        key={`more-${change.category}-${change.text}-${index}`}
                        type="button"
                        className="return-recap-banner-item return-recap-banner-extra-item"
                        data-category={change.category.toLowerCase()}
                        onClick={() => scrollToSection(change.section, { highlight: true })}
                      >
                        <CategoryIcon category={change.category.toLowerCase()} className="return-recap-category-icon" />
                        <span className="return-recap-banner-text">{change.text}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </section>
            )}
            <div className="single-page">
              <section id="overview" className="scroll-section scroll-section-overview" data-nav-section>
                <Overview data={data} onHeroIdentityCandidate={setHeroIdentityCandidate} />
              </section>
              <section id="ownership" className="scroll-section ownership-section" data-nav-section>
                <Ownership data={data} />
              </section>
              <section id="market" className="scroll-section" data-nav-section>
                <Market data={data} />
              </section>
              <section id="collection" className="scroll-section" data-nav-section>
                <Collection data={data} />
              </section>
              <section id="economy" className="scroll-section" data-nav-section>
                <Funding data={data} />
              </section>
              <section id="team" className="scroll-section team-scroll-section" data-nav-section>
                <Team />
              </section>
            </div>
            <SiteFooter />
          </>
        )}
      </div>
    </main>
  );
}
