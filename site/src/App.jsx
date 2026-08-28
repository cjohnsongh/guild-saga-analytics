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

const GOLD_MASTER_UPDATED_AT = '2026-08-26T14:30:00Z';

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
const HERO_IDENTITY_DELAY_MS = 3000;
const HERO_IDENTITY_FADE_MS = 360;
const heroSourceImageCache = new Map();

const LABYRINTHS_SLIDES = [
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
  { id: 'overview', label: 'Overview', target: 'overview' },
  { id: 'ownership', label: 'Ownership', target: 'ownership' },
  { id: 'market', label: 'Market', target: 'market' },
  { id: 'collection', label: 'Collection', target: 'collection' },
  { id: 'economy', label: 'Economy', target: 'economy' },
];

const COLORS = {
  text: '#eeeeee',
  muted: '#aaa9b7',
  faint: '#777682',
  grid: '#29292f',
  axis: '#50505a',
  accent: '#668a95',
  accentSoft: '#789aa3',
  accentDark: '#4f7079',
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
  ownershipSupply: '#4f7079',
  staking: '#668a95',
  mint: '#668a95',
  wallet: '#668a95',
  resale: '#668a95',
  resaleDark: '#4f7079',
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
  return fetch(path).then((response) => {
    if (!response.ok) throw new Error(`${path}: ${response.status}`);
    return response.json();
  });
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
      lineStyle: { color: '#7c7c7c', width: 1, type: 'dashed' },
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

function makeOwnershipOption(tiers) {
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
          formatter: (p) => formatPercent(p.value),
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
        interval: 0,
        formatter: (value, index) => (index % 3 === 0 ? value : ''),
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
      axisPointer: { type: 'cross', crossStyle: { color: '#747480', type: 'dashed' } },
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
      { type: 'inside', xAxisIndex: [0, 1], filterMode: 'filter', zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false },
    ],
    xAxis: [
      commonTimeAxis(0, startDate, endDate, false),
      commonTimeAxis(1, startDate, endDate, true),
    ],
    yAxis: [
      valueAxis(0, floorScale, formatAxisDecimal, { position: 'right' }),
      valueAxis(0, volumeScale, () => '', { position: 'left', split: false, show: false }),
      valueAxis(1, listingScale, (value) => formatInt(value), { position: 'right' }),
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


function installMarketYAxisDrag(chart) {
  const zr = chart.getZr();
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

  const hitRightAxis = (event) => {
    const { x, y } = pointOf(event);
    const width = chart.getWidth();
    for (const gridIndex of [0, 1]) {
      const grid = chart.getModel().getComponent('grid', gridIndex)?.coordinateSystem;
      const rect = grid?.getRect?.();
      if (!rect) continue;
      const inY = y >= rect.y && y <= rect.y + rect.height;
      const inAxisGutter = x >= rect.x + rect.width - 5 && x <= width;
      if (inY && inAxisGutter) return gridIndex === 0 ? 0 : 2;
    }
    return null;
  };

  const setCursor = (event) => {
    chart.getDom().style.cursor = hitRightAxis(event) !== null ? 'ns-resize' : '';
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

  const onMouseDown = (event) => {
    const raw = event.event;
    if (raw?.button !== undefined && raw.button !== 0) return;
    const axisIndex = hitRightAxis(event);
    if (axisIndex === null) return;

    const option = chart.getOption().yAxis?.[axisIndex] || {};
    const min = Number(option.min);
    const max = Number(option.max);
    if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return;

    const { y } = pointOf(event);
    drag = {
      axisIndex,
      startY: y,
      startMin: min,
      startMax: max,
      startSpan: max - min,
      center: (min + max) / 2,
    };
    chart.getDom().style.cursor = 'ns-resize';
    raw?.preventDefault?.();
  };

  const onMouseMove = (event) => {
    if (!drag) {
      setCursor(event);
      return;
    }

    const { y } = pointOf(event);
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
    event.event?.preventDefault?.();
  };

  const stopDrag = () => {
    drag = null;
    chart.getDom().style.cursor = '';
  };

  const onDoubleClick = (event) => {
    const axisIndex = hitRightAxis(event);
    if (axisIndex === null) return;
    const axis = initial[axisIndex];
    if (!axis || !Number.isFinite(axis.min) || !Number.isFinite(axis.max)) return;
    const yAxis = [{}, {}, {}];
    yAxis[axisIndex] = axis;
    chart.setOption({ yAxis }, { lazyUpdate: true });
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
    chart.getDom().style.cursor = '';
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
      axisLabel: { color: COLORS.muted, fontSize: 12, interval: 0 },
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
      axisPointer: { type: 'cross', crossStyle: { color: '#747480', type: 'dashed' } },
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
    yAxis: valueAxis(0, scale, (value) => formatInt(value), { position: 'left' }),
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
      orient: 'vertical',
      right: 10,
      top: 'middle',
      data: order,
      textStyle: { color: COLORS.muted, fontSize: 13 },
      itemWidth: 10,
      itemHeight: 10,
    },
    series: [{
      type: 'pie',
      radius: ['38%', '82%'],
      center: ['42%', '52%'],
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
      axisPointer: { type: 'cross', crossStyle: { color: '#747480', type: 'dashed' } },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
    },
    xAxis: monthAxis(rows),
    yAxis: valueAxis(0, scale, (value) => formatInt(value), { position: 'left' }),
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
      axisPointer: { type: 'cross', crossStyle: { color: '#747480', type: 'dashed' } },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
    },
    xAxis: monthAxis(rows),
    yAxis: valueAxis(0, scale, (value) => `$${formatInt(value)}`, { position: 'left' }),
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
      axisPointer: { type: 'cross', crossStyle: { color: '#747480', type: 'dashed' } },
      backgroundColor: '#17171b',
      borderColor: '#45454d',
      textStyle: { color: '#efeff3', fontSize: 13 },
      formatter: (params) => {
        const item = params?.[0];
        return item ? `<strong>${item.axisValue}</strong><br/>${formatSol(item.value, 3)}` : '';
      },
    },
    xAxis: monthAxis(rows),
    yAxis: valueAxis(0, scale, (value) => formatAxisDecimal(value), { position: 'left' }),
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
      <button type="button" className="image-lightbox-close" aria-label="Close image viewer" onClick={onClose}>×</button>
      {items.length > 1 && (
        <button
          type="button"
          className="image-lightbox-arrow image-lightbox-prev"
          aria-label="Previous image"
          onClick={() => onChange((index - 1 + items.length) % items.length)}
        >‹</button>
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
        >›</button>
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
  const [currentUrl, setCurrentUrl] = useState(HERO_ZERO_FACE_PFP);
  const [nextUrl, setNextUrl] = useState(null);
  const [nextVisible, setNextVisible] = useState(false);
  const candidateVersionRef = useRef(0);
  const currentBlobUrlRef = useRef(null);
  const nextBlobUrlRef = useRef(null);
  const currentFaviconBlobUrlRef = useRef(null);
  const fadeTimerRef = useRef(null);

  useEffect(() => {
    const version = ++candidateVersionRef.current;
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
        nextBlobUrlRef.current = url;
        setNextVisible(false);
        setNextUrl(url);

        // The favicon intentionally snaps to the settled Hero. The in-page mark
        // crossfades over the old Hero instead of changing abruptly.
        const previousFaviconBlobUrl = currentFaviconBlobUrlRef.current;
        currentFaviconBlobUrlRef.current = faviconUrl;
        setHeroFavicon(faviconUrl);
        if (previousFaviconBlobUrl) URL.revokeObjectURL(previousFaviconBlobUrl);

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
    }, HERO_IDENTITY_DELAY_MS);

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
      <img className="brand-mark-image" src={currentUrl} alt="" />
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
  const [mobileView, setMobileView] = useState('original');
  const [heroLightboxIndex, setHeroLightboxIndex] = useState(null);
  const [heroInput, setHeroInput] = useState('0');
  const [heroId, setHeroId] = useState(0);
  const [pfpColor, setPfpColor] = useState(() => getHeroDefaultColor(0));
  const [pfpImages, setPfpImages] = useState({
    body: HERO_ZERO_BODY_PFP,
    face: HERO_ZERO_FACE_PFP,
  });
  const generationIdRef = useRef(0);
  const activeBlobUrlsRef = useRef([]);

  useEffect(() => {
    onIdentityCandidate?.({ heroId, color: pfpColor });
  }, [heroId, pfpColor, onIdentityCandidate]);

  useEffect(() => {
    let cancelled = false;
    const generationId = ++generationIdRef.current;

    const generate = async () => {
      try {
        const sourceImage = await loadHeroSourceImage(heroId);
        const [bodyBlob, faceBlob] = await Promise.all([
          makeHeroPfpBlob(sourceImage, pfpColor, 'body'),
          makeHeroPfpBlob(sourceImage, pfpColor, 'face'),
        ]);

        const nextUrls = [URL.createObjectURL(bodyBlob), URL.createObjectURL(faceBlob)];
        if (cancelled || generationId !== generationIdRef.current) {
          nextUrls.forEach((url) => URL.revokeObjectURL(url));
          return;
        }

        const previousUrls = activeBlobUrlsRef.current;
        activeBlobUrlsRef.current = nextUrls;
        setPfpImages({ body: nextUrls[0], face: nextUrls[1] });
        previousUrls.forEach((url) => URL.revokeObjectURL(url));
      } catch (error) {
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
      src: getHeroOriginalUrl(heroId),
      alt: `Guild Saga Hero #${heroId} original NFT image`,
    },
    {
      id: 'body',
      label: 'Body PFP',
      src: pfpImages.body,
      alt: `Guild Saga Hero #${heroId} body profile picture`,
    },
    {
      id: 'face',
      label: 'Face PFP',
      src: pfpImages.face,
      alt: `Guild Saga Hero #${heroId} face profile picture`,
    },
  ], [heroId, pfpImages]);

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
          This site tracks the collection's supply, holders, staking activity, burns and secondary-market history using on-chain data.
        </p>
        <div className="intro-actions hero-trade-actions">
          <span>Trade:</span>
          <a href="https://www.tensor.trade/trade/guild_saga_heroes" target="_blank" rel="noreferrer">Tensor</a>
          <a href="https://magiceden.io/marketplace/guild_saga_heroes" target="_blank" rel="noreferrer">Magic Eden</a>
        </div>
      </div>

      <div className="hero-browser">
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
          label={`Guild Saga Hero #${heroId} image viewer`}
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

function getLabyrinthsCarouselLayout() {
  const width = window.innerWidth;
  if (width <= 520) return { step: 84, offset: 8 };
  if (width <= 780) return { step: 50, offset: 0 };
  if (width <= 1120) return { step: 100 / 3, offset: 0 };
  return { step: 25, offset: 0 };
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

  const [carouselIndex, setCarouselIndex] = useState(START_INDEX);
  const [carouselPaused, setCarouselPaused] = useState(false);
  const [carouselTransition, setCarouselTransition] = useState(true);
  const [lightboxIndex, setLightboxIndex] = useState(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [carouselLayout, setCarouselLayout] = useState(() => getLabyrinthsCarouselLayout());
  const carouselLocked = useRef(false);

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(media.matches);
    update();
    media.addEventListener?.('change', update);
    return () => media.removeEventListener?.('change', update);
  }, []);

  useEffect(() => {
    const update = () => setCarouselLayout(getLabyrinthsCarouselLayout());
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
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
      <div className="labyrinths-heading-row">
        <div>
          <h2 id="labyrinths-showcase-title">Guild Saga: Labyrinths</h2>
          <p>A tactical RPG from Ocelot Technologies where Guild Saga Heroes can be used alongside standard adventurers.</p>
        </div>
        <a className="epic-wishlist-link" href="https://store.epicgames.com/p/guild-saga-labyrinths-ca0f96?lang=en-US" target="_blank" rel="noreferrer">
          <EpicGamesIcon />
          <span>Wishlist on Epic Games Store</span>
        </a>
      </div>

      <div
        className="labyrinths-carousel"
        onMouseEnter={() => setCarouselPaused(true)}
        onMouseLeave={() => setCarouselPaused(false)}
      >
        <div className="labyrinths-carousel-viewport">
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
            ><span aria-hidden="true">‹</span></button>
            <button
              type="button"
              className="labyrinths-arrow labyrinths-arrow-next"
              aria-label="Next screenshots"
              onClick={() => moveCarousel(1)}
            ><span aria-hidden="true">›</span></button>
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

function Overview({ onHeroIdentityCandidate }) {
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
      <section className="section-heading">
        <h2>Market</h2>
      </section>

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
          <Chart option={chartOption} onInit={installMarketYAxisDrag} />
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
  const ownershipOption = useMemo(() => makeOwnershipOption(tiers), [tiers]);

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
      <section className="section-heading ownership-main-heading">
        <h2 id="ownership-title">Ownership</h2>
      </section>

      <div className="ownership-staking-grid">
        <section className="joint-domain-panel" aria-labelledby="holder-distribution-title">
          <section className="snapshot-strip joint-snapshot ownership-snapshot" aria-label="Ownership statistics">
            <SnapshotStat label="Active Supply" value={formatInt(data.summary.hero.active_supply)} />
            <SnapshotStat label="Holders" value={formatInt(totalHolders)} />
            <SnapshotStat label="1–4 Heroes" value={formatInt(small?.holder_count)} sub={`${formatPercent(totalHolders ? Number(small?.holder_count || 0) / totalHolders * 100 : 0)} of holders`} />
            <SnapshotStat label="50+ Heroes" value={formatInt(largeHolders)} sub={`${formatPercent(largeSupply)} of active supply`} />
          </section>
          <div className="joint-chart-head">
            <strong id="holder-distribution-title">Holder Distribution</strong>
            <span>Share of holders vs share of active supply</span>
          </div>
          <div className="domain-chart joint-domain-chart"><Chart option={ownershipOption} /></div>
        </section>

        <section className="joint-domain-panel" aria-labelledby="quest-activity-title">
          <section className="snapshot-strip joint-snapshot staking-snapshot" aria-label="Staking and quest statistics">
            <SnapshotStat label="Staked" value={formatInt(staked)} sub={`${formatPercent(data.summary.hero.staked_supply_pct)} of active supply`} />
            <SnapshotStat label="Active ≤7d" value={formatInt(active7)} sub={`${formatPercent(staked ? active7 / staked * 100 : 0)} of staked`} />
            <SnapshotStat label="Active ≤30d" value={formatInt(active30)} sub={`${formatPercent(staked ? active30 / staked * 100 : 0)} of staked`} />
            <SnapshotStat label="Idle 1+ Year" value={formatInt(idleYearCombined)} sub={`${formatPercent(staked ? idleYearCombined / staked * 100 : 0)} of staked`} />
          </section>
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
      <section className="section-heading"><h2>Collection</h2></section>

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
          <div className="domain-chart-block compact-chart-block burn-chart-block">
            <div className="section-bar"><span className="section-title">Burn history</span></div>
            <div className="domain-chart"><Chart option={burnOption} /></div>
          </div>
          <div className="domain-chart-block compact-chart-block burn-chart-block">
            <div className="section-bar"><span className="section-title">Burned by rarity</span></div>
            <div className="domain-chart"><Chart option={rarityOption} /></div>
          </div>
        </div>
      </section>
    </div>
  );
}

function Economy({ data }) {
  const treasury = data.treasury;
  const solOption = useMemo(() => makeSolConversionOption(treasury.conversion_history), [treasury.conversion_history]);
  const usdcOption = useMemo(() => makeUsdcConversionOption(treasury.conversion_history), [treasury.conversion_history]);
  const splitOption = useMemo(() => makeTreasurySplitOption(treasury.initial_mint_treasury_split), [treasury.initial_mint_treasury_split]);
  const royaltiesOption = useMemo(() => makeRoyaltiesOption(data.market.monthly_activity), [data.market.monthly_activity]);

  return (
    <div className="page-stack domain-page economy-page">
      <section className="section-heading"><h2>Economy</h2></section>

      <section className="snapshot-strip snapshot-strip-three">
        <SnapshotStat label="Public Mint Proceeds" value={formatSol(data.summary.launch.public_mint_proceeds_sol, 0)} />
        <SnapshotStat label="Guild Saga Royalties" value={formatSol(data.summary.market.guild_saga_royalties_sol, 2)} />
        <SnapshotStat label="Mint + Royalties" value={formatSol(data.summary.market.mint_plus_royalties_sol, 2)} />
      </section>

      <section className="economy-context economy-context-intro">
        <p>
          The 9,900 public Guild Saga Heroes minted for <strong>1.5 SOL each</strong>, generating
          <strong> 14,850 SOL</strong> in mint proceeds. Secondary-market royalties are tracked separately from the mint.
        </p>
      </section>

      <section className="domain-chart-block">
        <div className="section-bar">
          <span className="section-title">Royalties over time</span>
          <strong>Guild Saga 90% wallet</strong>
        </div>
        <div className="section-note">Monthly royalties received from secondary-market sales</div>
        <div className="domain-chart economy-royalties-chart"><Chart option={royaltiesOption} /></div>
      </section>

      <section className="economy-section">
        <div className="section-bar"><span className="section-title">Mint proceeds split</span><strong>Feb 26, 2022</strong></div>
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
            <div className="domain-chart economy-conversion-chart"><Chart option={solOption} /></div>
          </div>
          <div className="economy-chart-card">
            <div className="economy-chart-head">
              <strong>USDC Purchased</strong>
              <span>Monthly USDC received and cumulative USDC purchased</span>
            </div>
            <div className="domain-chart economy-conversion-chart"><Chart option={usdcOption} /></div>
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

function SiteFooter() {
  return (
    <footer className="site-footer">
      <a className="data-page-link" href="#data">Data & methodology</a>
      <div className="freshness-chip"><i aria-hidden="true" /><span>Updated {formatUpdatedUtc(GOLD_MASTER_UPDATED_AT)}</span></div>
    </footer>
  );
}

function DataPage({ onBack }) {
  const files = [
    ['summary.json', 'Current headline collection and market metrics.'],
    ['hero-state.json', 'Holder tiers, staking and quest state, burn history, and burned rarity.'],
    ['floor-listings.json', 'Historical floor price and listing-count observations.'],
    ['market-history.json', 'Secondary-market activity, first-resale timing, and royalty history.'],
    ['market-daily.json', 'Daily secondary sales and SOL volume used by the market explorer.'],
    ['launch.json', 'Original mint history, mint phases, and public mint distribution.'],
    ['treasury.json', 'Mint-proceeds split and project-connected economic aggregates.'],
  ];

  return (
    <div className="data-page page-stack">
      <section className="data-page-heading">
        <div>
          <span className="eyebrow">Data</span>
          <h1>Data & methodology</h1>
          <p>Files used by this site, what they contain, and how to interpret the main metrics.</p>
        </div>
        <button className="secondary-button" type="button" onClick={onBack}>Back to analytics</button>
      </section>

      <section className="data-file-list">
        {files.map(([name, description]) => (
          <a key={name} href={`/data/${name}`} target="_blank" rel="noreferrer" className="data-file-row">
            <code>{name}</code>
            <span>{description}</span>
            <b aria-hidden="true">↗</b>
          </a>
        ))}
      </section>

      <section className="methodology-grid">
        <div>
          <h2>Current supply</h2>
          <p>Active supply is original supply minus confirmed burns. Staked and listed percentages use active supply as their denominator.</p>
        </div>
        <div>
          <h2>Holders</h2>
          <p>Holder counts and tier percentages use the site's current holder methodology. Exact tier counts are exposed in the collection-state file.</p>
        </div>
        <div>
          <h2>Market</h2>
          <p>Floor and listings use the recorded marketplace snapshots in the history file. Secondary sales are separated from ordinary transfers.</p>
        </div>
        <div>
          <h2>Economy</h2>
          <p>Wallet attribution and exchange hops are described with the level of certainty supported by the on-chain evidence; inferred associations are not presented as proof of identity.</p>
        </div>
      </section>

      <div className="freshness-chip"><i aria-hidden="true" /><span>Updated {formatUpdatedUtc(GOLD_MASTER_UPDATED_AT)}</span></div>
    </div>
  );
}

export default function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [activeSection, setActiveSection] = useState('overview');
  const [showDataPage, setShowDataPage] = useState(() => window.location.hash === '#data');
  const [heroIdentityCandidate, setHeroIdentityCandidate] = useState(() => ({
    heroId: 0,
    color: getHeroDefaultColor(0),
  }));

  useEffect(() => {
    Promise.all(DATA_PATHS.map(fetchJson))
      .then(([summary, hero, launch, market, floor, treasury, dailyMarket]) => {
        setData({ summary, hero, launch, market, floor, treasury, dailyMarket });
      })
      .catch(setError);
  }, []);

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
      const marker = window.scrollY + 150;
      let current = 'overview';
      const targets = [...new Set(NAV_ITEMS.map((item) => item.target || item.id))];
      for (const target of targets) {
        const element = document.getElementById(target);
        if (element && element.offsetTop <= marker) current = target;
      }
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

  const scrollToSection = (id) => {
    const move = () => {
      const element = document.getElementById(id);
      if (!element) return;
      const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      element.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
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

  return (
    <main className="app-shell">
      <header className="site-header">
        <div className="header-inner">
          <button className="brand" type="button" onClick={() => scrollToSection('overview')}>
            <BrandHeroMark candidate={heroIdentityCandidate} />
            <span><strong>Guild Saga Heroes</strong><small>Analytics</small></span>
          </button>
        </div>
        <nav className="primary-nav" aria-label="Primary">
          <div className="nav-inner">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={!showDataPage && activeSection === (item.target || item.id) ? 'is-active' : ''}
                onClick={() => scrollToSection(item.target || item.id)}
              >
                {item.label}
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
            <div className="single-page">
              <section id="overview" className="scroll-section scroll-section-overview" data-nav-section>
                <Overview onHeroIdentityCandidate={setHeroIdentityCandidate} />
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
                <Economy data={data} />
              </section>
            </div>
            <SiteFooter />
          </>
        )}
      </div>
    </main>
  );
}
