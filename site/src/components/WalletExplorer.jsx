import React, { useEffect, useMemo, useState } from 'react';
import Chart from './Chart.jsx';
import { getHeroDefaultColor, getHeroSourceUrl } from '../lib/heroPfp.js';
import {
  buildWalletView,
  parseWalletAddresses,
  shortenWallet,
} from '../lib/walletExplorer.js';

const WALLET_DATA_URL = '/data/wallet-explorer.json';
const HERO_PAGE_SIZE = 48;
const TIMELINE_PAGE_SIZE = 24;

const RARITY_COLORS = {
  Bronze: '#956639',
  Silver: '#757c9b',
  Gold: '#fbd364',
  Elven: '#faebc8',
  Arcane: '#8041a0',
};

const COLORS = {
  text: '#eeeeee',
  muted: '#aaa9b7',
  grid: '#29292f',
  axis: '#50505a',
  accent: '#668a95',
  accentDark: '#45636b',
};

let walletDataCache = null;
let walletDataPromise = null;

function loadWalletData() {
  if (walletDataCache) return Promise.resolve(walletDataCache);
  if (!walletDataPromise) {
    walletDataPromise = fetch(WALLET_DATA_URL, { cache: 'no-cache' })
      .then((response) => {
        if (!response.ok) throw new Error(`${WALLET_DATA_URL}: ${response.status}`);
        return response.json();
      })
      .then((data) => {
        walletDataCache = data;
        return data;
      })
      .finally(() => {
        walletDataPromise = null;
      });
  }
  return walletDataPromise;
}

function formatInt(value) {
  return Number(value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function formatDecimal(value, maximumFractionDigits = 1) {
  return Number(value || 0).toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits,
  });
}

function formatPercent(value, maximumFractionDigits = 1) {
  return `${formatDecimal(value, maximumFractionDigits)}%`;
}

function formatSol(value, maximumFractionDigits = 2) {
  return `${formatDecimal(value, maximumFractionDigits)} SOL`;
}

function formatRank(rank) {
  return rank ? `#${formatInt(rank.rank)}` : '—';
}

function formatTopPct(rank) {
  if (!rank) return '';
  const digits = rank.topPct < 1 ? 2 : rank.topPct < 10 ? 1 : 0;
  return `Top ${formatPercent(rank.topPct, digits)}`;
}

function formatDate(value, options = {}) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: options.year === false ? undefined : 'numeric',
  }).format(date);
}

function formatDurationSince(value, asOf) {
  if (!value) return '—';
  const start = new Date(value);
  const end = asOf ? new Date(asOf) : new Date();
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return '—';
  const days = Math.max(0, Math.floor((end - start) / 86400000));
  if (days < 60) return `${formatInt(days)}d`;
  if (days < 730) return `${formatDecimal(days / 30.4375, 1)}mo`;
  return `${formatDecimal(days / 365.25, 1)}y`;
}

function WalletInfo({ children }) {
  return (
    <div className="wallet-info-note">
      <span className="wallet-info-icon" aria-hidden="true">i</span>
      <span>{children}</span>
    </div>
  );
}

function WalletStat({ label, value, sub, title }) {
  return (
    <div className="wallet-stat" title={title}>
      <span className="wallet-stat-label">{label}</span>
      <strong className="wallet-stat-value">{value}</strong>
      <small className={`wallet-stat-detail${sub ? '' : ' is-empty'}`} aria-hidden={sub ? undefined : true}>
        {sub || '\u00A0'}
      </small>
    </div>
  );
}

function WalletSectionHeading({ title, note }) {
  return (
    <div className="wallet-section-heading">
      <div>
        <h2>{title}</h2>
        {note && <p>{note}</p>}
      </div>
    </div>
  );
}

function makeRarityOption(stats, data) {
  const rarities = data.lookups.rarities;
  const rows = rarities.map((rarity, index) => ({
    rarity,
    count: stats.rarityCounts[rarity] || 0,
    collection: Number(data.collection.rarity_counts[index] || 0),
  }));

  return {
    animationDuration: 260,
    tooltip: {
      trigger: 'item',
      backgroundColor: '#17171b',
      borderColor: '#34343c',
      textStyle: { color: COLORS.text },
      formatter: (p) => {
        const row = rows[p.dataIndex];
        const walletPct = stats.heroCount ? (row.count / stats.heroCount) * 100 : 0;
        const collectionPct = data.collection.active_supply ? (row.collection / data.collection.active_supply) * 100 : 0;
        return `<strong>${row.rarity}</strong><br/>${formatInt(row.count)} Heroes (${formatPercent(walletPct)})<br/>Collection: ${formatPercent(collectionPct)}`;
      },
    },
    legend: { show: false },
    series: [{
      type: 'pie',
      radius: ['50%', '78%'],
      center: ['50%', '50%'],
      minAngle: 2,
      avoidLabelOverlap: true,
      label: { show: false },
      emphasis: { scaleSize: 5 },
      data: rows.map((row) => ({
        name: row.rarity,
        value: row.count,
        itemStyle: { color: RARITY_COLORS[row.rarity] || COLORS.accent },
      })),
    }],
  };
}

function makeQuestOption(stats) {
  const rows = Object.entries(stats.questCounts).filter(([, value]) => Number(value) > 0);
  const dataRows = rows.length ? rows : [['No staked Heroes', 0]];
  return {
    animationDuration: 260,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#17171b',
      borderColor: '#34343c',
      textStyle: { color: COLORS.text },
      formatter: (params) => {
        const p = params?.[0];
        return p ? `<strong>${p.name}</strong><br/>${formatInt(p.value)} Heroes` : '';
      },
    },
    grid: { left: 52, right: 14, top: 18, bottom: 72 },
    xAxis: {
      type: 'category',
      data: dataRows.map(([name]) => ({
        'Active 0–7d': '0–7d',
        'Idle 8–30d': '8–30d',
        'Idle 31–90d': '31–90d',
        'Idle 91–180d': '91–180d',
        'Idle 181–365d': '181–365d',
        'Idle 1+ year': '1y+',
        'Never quested': 'Never',
      }[name] || name)),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisLabel: { color: COLORS.muted, fontSize: 11, interval: 0, rotate: dataRows.length > 5 ? 20 : 0 },
    },
    yAxis: {
      type: 'value',
      min: 0,
      minInterval: 1,
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { color: COLORS.muted, fontSize: 11 },
      splitLine: { lineStyle: { color: COLORS.grid } },
    },
    series: [{
      type: 'bar',
      data: dataRows.map(([, value]) => value),
      barMaxWidth: 70,
      itemStyle: { color: COLORS.accent },
      label: {
        show: true,
        position: 'top',
        color: '#dedde7',
        fontSize: 11,
        formatter: (p) => p.value ? formatInt(p.value) : '',
      },
    }],
  };
}

function percentileCopy(rank) {
  if (!rank) return 'No qualifying activity';
  return formatTopPct(rank);
}

export function OwnershipWalletEntry({ savedWallets, onExplore }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const savedPreview = savedWallets.map((address) => shortenWallet(address, { compact: true })).join(', ');
  const placeholder = savedPreview || 'Enter a Solana wallet address';

  const submit = (event) => {
    event.preventDefault();
    const typed = value.trim();
    if (!typed && savedWallets.length) {
      setError('');
      onExplore(savedWallets);
      return;
    }

    const parsed = parseWalletAddresses(typed);
    if (!parsed.addresses.length || parsed.invalid.length) {
      setError('Enter a valid Solana wallet address. You can paste multiple addresses separated by spaces or commas.');
      return;
    }
    setError('');
    onExplore(parsed.addresses);
  };

  return (
    <section className="ownership-wallet-entry" aria-labelledby="ownership-wallet-entry-title">
      <div className="ownership-wallet-entry-copy">
        <span className="ownership-wallet-entry-icon" aria-hidden="true">
          <span className="category-icon" data-category="ownership" />
        </span>
        <div>
          <strong id="ownership-wallet-entry-title">Explore your ownership</strong>
          <span>Wallet portfolio, staking, rarity and history</span>
        </div>
      </div>
      <form className="ownership-wallet-entry-form" onSubmit={submit}>
        <input
          type="text"
          inputMode="text"
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck="false"
          aria-label="Solana wallet address"
          aria-invalid={Boolean(error) || undefined}
          placeholder={placeholder}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            if (error) setError('');
          }}
        />
        <button type="submit" className="wallet-primary-button">Explore Ownership</button>
      </form>
      {error && <span className="ownership-wallet-entry-error" role="alert">{error}</span>}
    </section>
  );
}

function WalletManager({ wallets, activeWallet, onActiveWalletChange, onWalletsChange }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');

  const addWallets = (event) => {
    event.preventDefault();
    const parsed = parseWalletAddresses(value);
    if (!parsed.addresses.length || parsed.invalid.length) {
      setError('Enter a valid Solana wallet address. Multiple addresses can be pasted at once.');
      return;
    }
    const next = [...new Set([...wallets, ...parsed.addresses])];
    onWalletsChange(next);
    onActiveWalletChange(next.length > 1 ? 'all' : next[0] || 'all');
    setValue('');
    setError('');
  };

  const removeWallet = (address) => {
    const next = wallets.filter((wallet) => wallet !== address);
    onWalletsChange(next);
    if (activeWallet === address) {
      onActiveWalletChange(next.length > 1 ? 'all' : next[0] || 'all');
    }
  };

  return (
    <section className="wallet-manager" aria-labelledby="wallet-manager-title">
      <div className="wallet-manager-head">
        <div>
          <span className="eyebrow">Wallets</span>
          <h2 id="wallet-manager-title">Choose the addresses to explore</h2>
        </div>
        {wallets.length > 0 && (
          <button
            type="button"
            className="wallet-clear-button"
            onClick={() => {
              onWalletsChange([]);
              onActiveWalletChange('all');
            }}
          >
            Clear wallets
          </button>
        )}
      </div>

      <form className="wallet-manager-form" onSubmit={addWallets}>
        <input
          type="text"
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck="false"
          aria-label="Add Solana wallet address"
          aria-invalid={Boolean(error) || undefined}
          placeholder="Add a Solana wallet address"
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            if (error) setError('');
          }}
        />
        <button type="submit" className="wallet-primary-button">Add wallet</button>
      </form>
      {error && <p className="wallet-manager-error" role="alert">{error}</p>}

      {wallets.length > 0 && (
        <div className="wallet-address-list" aria-label="Saved wallets">
          {wallets.map((address) => (
            <span className="wallet-address-chip" key={address} title={address}>
              <span>{shortenWallet(address)}</span>
              <button type="button" aria-label={`Remove ${shortenWallet(address)}`} onClick={() => removeWallet(address)}>×</button>
            </span>
          ))}
        </div>
      )}

      {wallets.length > 1 && (
        <div className="wallet-view-switcher" aria-label="Wallet view">
          <button
            type="button"
            className={activeWallet === 'all' ? 'is-active' : ''}
            aria-pressed={activeWallet === 'all'}
            onClick={() => onActiveWalletChange('all')}
          >
            All wallets
          </button>
          {wallets.map((address) => (
            <button
              type="button"
              key={address}
              className={activeWallet === address ? 'is-active' : ''}
              aria-pressed={activeWallet === address}
              title={address}
              onClick={() => onActiveWalletChange(address)}
            >
              {shortenWallet(address)}
            </button>
          ))}
        </div>
      )}

      {wallets.length > 1 && activeWallet === 'all' && (
        <WalletInfo>Everything below treats the saved addresses as one combined ownership profile. Switch to an individual wallet above whenever you want to inspect it on its own.</WalletInfo>
      )}
      <WalletInfo>Addresses are saved only in this browser. Wallet Explorer downloads one published Guild Saga index; typing an address does not trigger an address-specific lookup or wallet connection.</WalletInfo>
    </section>
  );
}

function WalletLoadingState() {
  return (
    <div className="wallet-loading-state" aria-hidden="true">
      <div className="wallet-loading-rail"><i /><i /><i /><i /><i /></div>
      <div className="wallet-loading-panels"><i /><i /></div>
    </div>
  );
}

function WalletEmptyState() {
  return (
    <section className="wallet-empty-state">
      <span className="category-icon" data-category="ownership" aria-hidden="true" />
      <strong>Add a wallet to explore its Guild Saga history</strong>
      <p>Current Heroes, staking and quest state, rarity, public mint history and validated marketplace activity all stay on this page.</p>
    </section>
  );
}

function WalletNoActivity() {
  return (
    <section className="wallet-empty-state wallet-no-activity">
      <strong>No Guild Saga activity found</strong>
      <p>The published dataset contains no current Hero ownership, public mint, or supported-market activity for this wallet view.</p>
    </section>
  );
}

function PortfolioOverview({ stats, data }) {
  const rarityOption = useMemo(() => makeRarityOption(stats, data), [stats, data]);
  const questOption = useMemo(() => makeQuestOption(stats), [stats]);
  const collectionStaking = Number(stats.collection.staked_supply_pct || 0);

  return (
    <>
      <section className="wallet-stat-rail wallet-overview-rail" aria-label="Ownership overview">
        <WalletStat label="Heroes" value={formatInt(stats.heroCount)} />
        <WalletStat
          label="Staked"
          value={formatInt(stats.stakedCount)}
          sub={`${formatPercent(stats.stakingPct)} of Heroes`}
        />
        <WalletStat
          label="Active supply"
          value={formatPercent(stats.supplyPct, stats.supplyPct < 0.1 ? 2 : 1)}
          sub={`${formatInt(stats.heroCount)} of ${formatInt(stats.collection.active_supply)}`}
        />
        <WalletStat
          label="Ownership rank"
          value={formatRank(stats.ownershipRank)}
          sub={formatTopPct(stats.ownershipRank)}
          title="For multiple selected wallets, the combined Hero count is ranked against individual current holders."
        />
        <WalletStat
          label="No market buy trail"
          value={formatInt(stats.noSupportedPurchase)}
          sub={stats.heroCount ? `${formatPercent((stats.noSupportedPurchase / stats.heroCount) * 100)} of Heroes` : ''}
          title="Current Heroes without an open supported-market purchase trail in this wallet view. This can include original mints, transfers, OTC deals, prizes or other activity outside supported marketplace records."
        />
      </section>

      <section className="wallet-analytics-grid">
        <article className="wallet-analytics-panel">
          <div className="wallet-panel-head">
            <div>
              <strong>Rarity</strong>
              <span>Your current Heroes</span>
            </div>
          </div>
          {stats.heroCount ? <div className="wallet-chart"><Chart option={rarityOption} /></div> : <div className="wallet-chart-empty">No current Heroes</div>}
          <div className="wallet-rarity-breakdown">
            {data.lookups.rarities.map((rarity, index) => {
              const count = stats.rarityCounts[rarity] || 0;
              const walletPct = stats.heroCount ? (count / stats.heroCount) * 100 : 0;
              const collectionCount = Number(data.collection.rarity_counts[index] || 0);
              const collectionPct = data.collection.active_supply ? (collectionCount / data.collection.active_supply) * 100 : 0;
              return (
                <div className="wallet-rarity-row" key={rarity}>
                  <span><i style={{ backgroundColor: RARITY_COLORS[rarity] }} />{rarity}</span>
                  <strong>{formatInt(count)} <small>{formatPercent(walletPct)}</small></strong>
                  <em>Collection {formatPercent(collectionPct)}</em>
                </div>
              );
            })}
          </div>
        </article>

        <article className="wallet-analytics-panel">
          <div className="wallet-panel-head">
            <div>
              <strong>World Mode</strong>
              <span>Current staking and quest activity</span>
            </div>
          </div>
          {stats.stakedCount ? <div className="wallet-chart"><Chart option={questOption} /></div> : <div className="wallet-chart-empty">No currently staked Heroes</div>}
          <div className="wallet-world-summary">
            <div><span>Staking rate</span><strong>{formatPercent(stats.stakingPct)}</strong><small>Collection {formatPercent(collectionStaking)}</small></div>
            <div><span>Longest current stake</span><strong>{formatDurationSince(stats.longestStakeSince, data.as_of.hero)}</strong><small>{formatDate(stats.longestStakeSince)}</small></div>
            <div><span>Most recent quest</span><strong>{stats.mostRecentQuest ? formatDate(stats.mostRecentQuest, { year: false }) : '—'}</strong><small>{stats.mostRecentQuest ? formatDate(stats.mostRecentQuest) : 'No qualifying quest found'}</small></div>
          </div>
        </article>
      </section>
    </>
  );
}

function HeroGallery({ stats }) {
  const [rarity, setRarity] = useState('all');
  const [status, setStatus] = useState('all');
  const [sort, setSort] = useState('number');
  const [visibleCount, setVisibleCount] = useState(HERO_PAGE_SIZE);

  useEffect(() => {
    setVisibleCount(HERO_PAGE_SIZE);
  }, [rarity, status, sort, stats.addresses.join('|')]);

  const filtered = useMemo(() => {
    const next = stats.heroes.filter((hero) => {
      if (rarity !== 'all' && hero.rarity !== rarity) return false;
      if (status === 'staked' && !hero.staked) return false;
      if (status === 'unstaked' && hero.staked) return false;
      return true;
    });

    next.sort((a, b) => {
      if (sort === 'rarity') {
        const order = ['Arcane', 'Elven', 'Gold', 'Silver', 'Bronze'];
        return order.indexOf(a.rarity) - order.indexOf(b.rarity) || a.number - b.number;
      }
      if (sort === 'quest') {
        if (a.staked !== b.staked) return a.staked ? -1 : 1;
        return String(a.lastQuest || '').localeCompare(String(b.lastQuest || '')) || a.number - b.number;
      }
      return a.number - b.number;
    });
    return next;
  }, [stats.heroes, rarity, status, sort]);

  return (
    <section className="wallet-section wallet-hero-section">
      <WalletSectionHeading title="Heroes" note="The current beneficial ownership resolved by the same staking-aware state used on the main dashboard." />
      <div className="wallet-gallery-toolbar">
        <span>{formatInt(filtered.length)} {filtered.length === 1 ? 'Hero' : 'Heroes'}</span>
        <div className="wallet-gallery-filters">
          <label>
            <span>Rarity</span>
            <select value={rarity} onChange={(event) => setRarity(event.target.value)}>
              <option value="all">All</option>
              {Object.keys(RARITY_COLORS).map((name) => <option value={name} key={name}>{name}</option>)}
            </select>
          </label>
          <label>
            <span>Status</span>
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="all">All</option>
              <option value="staked">Staked</option>
              <option value="unstaked">Unstaked</option>
            </select>
          </label>
          <label>
            <span>Sort</span>
            <select value={sort} onChange={(event) => setSort(event.target.value)}>
              <option value="number">Hero #</option>
              <option value="rarity">Rarity</option>
              <option value="quest">Quest activity</option>
            </select>
          </label>
        </div>
      </div>

      {filtered.length ? (
        <>
          <div className="wallet-hero-grid">
            {filtered.slice(0, visibleCount).map((hero) => (
              <article className="wallet-hero-card" key={hero.number}>
                <span className="wallet-hero-art" style={{ backgroundColor: getHeroDefaultColor(hero.number) }}>
                  <img src={getHeroSourceUrl(hero.number)} alt="" draggable="false" />
                </span>
                <span className="wallet-hero-card-copy">
                  <strong>#{hero.number}</strong>
                  <small><i style={{ backgroundColor: RARITY_COLORS[hero.rarity] }} />{hero.rarity}</small>
                  <em>{hero.staked ? hero.quest : 'Unstaked'}</em>
                </span>
              </article>
            ))}
          </div>
          {visibleCount < filtered.length && (
            <button className="wallet-show-more" type="button" onClick={() => setVisibleCount((count) => count + HERO_PAGE_SIZE)}>
              Show more Heroes
            </button>
          )}
        </>
      ) : (
        <div className="wallet-inline-empty">No Heroes match these filters.</div>
      )}
    </section>
  );
}

function MarketActivity({ stats }) {
  return (
    <section className="wallet-section">
      <WalletSectionHeading
        title="Supported market activity"
        note="Validated marketplace activity for this wallet view. Ordinary transfers and OTC deals are not treated as marketplace purchases."
      />
      <div className="wallet-stat-rail wallet-market-rail" aria-label="Supported market statistics">
        <WalletStat label="Market purchases" value={formatInt(stats.purchaseCount)} sub={percentileCopy(stats.buyerRank)} />
        <WalletStat label="SOL spent" value={formatSol(stats.marketSpentSol, 2)} sub="Supported-market buys" />
        <WalletStat label="Matched resales" value={formatInt(stats.matchedResaleCount)} sub="Prior market purchase found" />
        <WalletStat label="SOL received" value={formatSol(stats.matchedReceivedSol, 2)} sub="Matched resales only" />
        <WalletStat
          label="Net market flow"
          value={formatSol(stats.matchedNetFlowSol, 2)}
          sub="Received − spent"
          title="This is supported-market cash flow, not profit or cost basis."
        />
        <WalletStat label="Unmatched market sales" value={formatInt(stats.unmatchedSaleCount)} sub="Excluded from received / net" />
      </div>
      <WalletInfo>
        SOL received and net market flow only count a sale when an earlier supported-market purchase of that same Hero is visible in this wallet view. {stats.unmatchedSaleCount ? `${formatInt(stats.unmatchedSaleCount)} other market ${stats.unmatchedSaleCount === 1 ? 'sale is' : 'sales are'} still shown in history but excluded from those totals because the acquisition is not visible in supported-market data.` : 'Every market sale in this view has a prior supported-market purchase of the same Hero.'} These are market-only cash-flow figures, not profit or cost basis.
      </WalletInfo>
    </section>
  );
}

function MintHistory({ stats }) {
  const phaseText = Object.entries(stats.mintPhaseCounts)
    .map(([phase, count]) => `${phase}: ${formatInt(count)}`)
    .join(' · ');

  return (
    <section className="wallet-section">
      <WalletSectionHeading title="Original mint" note="Original public Candy Machine mints attributed to this wallet view." />
      <div className="wallet-stat-rail wallet-mint-rail" aria-label="Original mint statistics">
        <WalletStat label="Original mints" value={formatInt(stats.mintCount)} sub={percentileCopy(stats.minterRank)} />
        <WalletStat label="Mint spend" value={formatSol(stats.publicMintSol, 1)} sub="1.5 SOL per public mint" />
        <WalletStat
          label="Still held"
          value={formatInt(stats.originalMintsHeld)}
          sub={stats.mintCount ? `${formatPercent((stats.originalMintsHeld / stats.mintCount) * 100)} of original mints` : ''}
        />
      </div>
      {phaseText && <div className="wallet-phase-summary">{phaseText}</div>}
    </section>
  );
}

function ActivityTimeline({ stats }) {
  const [filter, setFilter] = useState('all');
  const [visibleCount, setVisibleCount] = useState(TIMELINE_PAGE_SIZE);

  useEffect(() => {
    setVisibleCount(TIMELINE_PAGE_SIZE);
  }, [filter, stats.addresses.join('|')]);

  const rows = useMemo(() => stats.timeline.filter((row) => {
    if (filter === 'all') return true;
    return row.kind === filter;
  }), [stats.timeline, filter]);

  return (
    <section className="wallet-section wallet-history-section">
      <WalletSectionHeading title="History" note="Public mints and supported-market transactions, newest first." />
      <div className="wallet-history-filter" aria-label="History filter">
        {[
          ['all', 'All'],
          ['buy', 'Purchases'],
          ['sell', 'Sales'],
          ['mint', 'Mints'],
        ].map(([id, label]) => (
          <button type="button" key={id} className={filter === id ? 'is-active' : ''} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>
        ))}
      </div>

      {rows.length ? (
        <>
          <div className="wallet-history-list">
            {rows.slice(0, visibleCount).map((row, index) => {
              const heroLabel = Number.isInteger(row.hero) ? `Hero #${row.hero}` : 'Public mint';
              const kindLabel = row.kind === 'buy' ? 'Bought' : row.kind === 'sell' ? 'Sold' : 'Minted';
              return (
                <article className="wallet-history-row" key={`${row.kind}-${row.index ?? row.utc}-${index}`}>
                  <time dateTime={row.utc}>{formatDate(row.utc)}</time>
                  <span className={`wallet-history-kind is-${row.kind}`}>{kindLabel}</span>
                  <div className="wallet-history-main">
                    <strong>{heroLabel}</strong>
                    <span>
                      {row.kind === 'mint'
                        ? row.phase
                        : `${formatSol(row.sol, 3)} · ${row.marketplace}${row.kind === 'sell' && !row.matched ? ' · unmatched acquisition' : ''}`}
                    </span>
                  </div>
                  {row.signature ? (
                    <a href={`https://explorer.solana.com/tx/${row.signature}`} target="_blank" rel="noreferrer" aria-label={`View transaction for ${heroLabel}`}>
                      Transaction ↗
                    </a>
                  ) : <span className="wallet-history-spacer" />}
                </article>
              );
            })}
          </div>
          {visibleCount < rows.length && (
            <button className="wallet-show-more" type="button" onClick={() => setVisibleCount((count) => count + TIMELINE_PAGE_SIZE)}>
              Show more history
            </button>
          )}
        </>
      ) : <div className="wallet-inline-empty">No activity in this category.</div>}
    </section>
  );
}

function ComparisonSection({ stats }) {
  return (
    <section className="wallet-section">
      <WalletSectionHeading title="Compared with the collection" note="How this ownership profile compares with current holders and collection activity." />
      <div className="wallet-comparison-grid">
        <article>
          <span>Ownership</span>
          <strong>{formatRank(stats.ownershipRank)}</strong>
          <small>{formatTopPct(stats.ownershipRank) || 'No current Heroes'}</small>
        </article>
        <article>
          <span>Staking rate</span>
          <strong>{formatPercent(stats.stakingPct)}</strong>
          <small>Collection {formatPercent(stats.collection.staked_supply_pct)}</small>
        </article>
        <article>
          <span>Market purchases</span>
          <strong>{formatInt(stats.purchaseCount)}</strong>
          <small>{percentileCopy(stats.buyerRank)}</small>
        </article>
        <article>
          <span>Original mints</span>
          <strong>{formatInt(stats.mintCount)}</strong>
          <small>{percentileCopy(stats.minterRank)}</small>
        </article>
      </div>
    </section>
  );
}

export function WalletExplorerPage({ wallets, onWalletsChange, onBack }) {
  const [activeWallet, setActiveWallet] = useState(() => wallets.length > 1 ? 'all' : wallets[0] || 'all');
  const [data, setData] = useState(walletDataCache);
  const [error, setError] = useState(null);
  const [showLoading, setShowLoading] = useState(false);

  useEffect(() => {
    if (activeWallet !== 'all' && !wallets.includes(activeWallet)) {
      setActiveWallet(wallets.length > 1 ? 'all' : wallets[0] || 'all');
    }
  }, [wallets, activeWallet]);

  useEffect(() => {
    if (!wallets.length || data) return undefined;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (!cancelled) setShowLoading(true);
    }, 180);

    loadWalletData()
      .then((next) => {
        if (!cancelled) {
          setData(next);
          setError(null);
          setShowLoading(false);
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          console.error('Wallet Explorer data load failed.', loadError);
          setError(loadError);
          setShowLoading(false);
        }
      });

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [wallets.length, data]);

  const viewWallets = activeWallet === 'all' ? wallets : wallets.filter((address) => address === activeWallet);
  const stats = useMemo(() => data && viewWallets.length ? buildWalletView(data, viewWallets) : null, [data, viewWallets.join('|')]);
  const hasAnyActivity = stats && (
    stats.heroCount || stats.mintCount || stats.purchaseCount || stats.timeline.some((row) => row.kind === 'sell')
  );

  return (
    <div className="wallet-page page-stack">
      <section className="wallet-page-heading">
        <div>
          <span className="eyebrow">Ownership</span>
          <h1>Explore Ownership</h1>
          <p>Combine your Guild Saga wallets into one portfolio, or inspect them individually, using the same published state behind the analytics dashboard.</p>
        </div>
        <button className="secondary-button" type="button" onClick={onBack}>Back to analytics</button>
      </section>

      <WalletManager
        wallets={wallets}
        activeWallet={activeWallet}
        onActiveWalletChange={setActiveWallet}
        onWalletsChange={onWalletsChange}
      />

      {!wallets.length && <WalletEmptyState />}
      {wallets.length > 0 && !data && !error && showLoading && <WalletLoadingState />}
      {wallets.length > 0 && !data && !error && <span className="sr-only" role="status">Loading published Wallet Explorer data.</span>}
      {error && (
        <section className="wallet-empty-state wallet-no-activity" role="status">
          <strong>Wallet Explorer data is temporarily unavailable</strong>
          <p>The rest of the analytics site is unaffected. Try this page again after refreshing.</p>
        </section>
      )}

      {data && stats && !hasAnyActivity && <WalletNoActivity />}
      {data && stats && hasAnyActivity && (
        <>
          <PortfolioOverview stats={stats} data={data} />
          <HeroGallery stats={stats} />
          <ComparisonSection stats={stats} />
          <MarketActivity stats={stats} />
          <MintHistory stats={stats} />
          <ActivityTimeline stats={stats} />
        </>
      )}
    </div>
  );
}
