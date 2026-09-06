export const WALLET_EXPLORER_STORAGE_KEY = 'guild-saga-wallet-explorer-v1';

const BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const BASE58_INDEX = new Map([...BASE58_ALPHABET].map((char, index) => [char, index]));

export function isSolanaAddress(value) {
  const address = String(value || '').trim();
  if (address.length < 32 || address.length > 44) return false;

  let numeric = 0n;
  for (const char of address) {
    const digit = BASE58_INDEX.get(char);
    if (digit === undefined) return false;
    numeric = numeric * 58n + BigInt(digit);
  }

  let payloadBytes = 0;
  let remaining = numeric;
  while (remaining > 0n) {
    payloadBytes += 1;
    remaining >>= 8n;
  }

  let leadingZeroBytes = 0;
  while (leadingZeroBytes < address.length && address[leadingZeroBytes] === '1') {
    leadingZeroBytes += 1;
  }

  return payloadBytes + leadingZeroBytes === 32;
}

export function parseWalletAddresses(value) {
  const raw = String(value || '').trim();
  if (!raw) return { addresses: [], invalid: [] };

  const tokens = raw
    .split(/[\s,;]+/)
    .map((token) => token.trim())
    .filter(Boolean);

  const addresses = [];
  const invalid = [];
  const seen = new Set();

  for (const token of tokens) {
    if (!isSolanaAddress(token)) {
      invalid.push(token);
      continue;
    }
    if (!seen.has(token)) {
      seen.add(token);
      addresses.push(token);
    }
  }

  return { addresses, invalid };
}

export function readStoredWallets() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(WALLET_EXPLORER_STORAGE_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];
    return [...new Set(parsed.map((value) => String(value || '').trim()).filter(isSolanaAddress))];
  } catch {
    return [];
  }
}

export function writeStoredWallets(addresses) {
  const next = [...new Set((addresses || []).filter(isSolanaAddress))];
  try {
    if (next.length) {
      window.localStorage.setItem(WALLET_EXPLORER_STORAGE_KEY, JSON.stringify(next));
    } else {
      window.localStorage.removeItem(WALLET_EXPLORER_STORAGE_KEY);
    }
  } catch {
    // Private / restrictive browser modes may block storage. The current page
    // remains fully functional for the session.
  }
  return next;
}

export function shortenWallet(address, { compact = false } = {}) {
  const value = String(address || '');
  if (!value) return '';
  if (compact) return `${value.slice(0, 4)}…`;
  return `${value.slice(0, 5)}…${value.slice(-4)}`;
}

function uniqueSortedNumbers(values) {
  return [...new Set(values)].sort((a, b) => a - b);
}

function rankAgainst(values, amount) {
  if (!amount || !Array.isArray(values) || !values.length) return null;
  const rank = 1 + values.reduce((count, value) => count + (Number(value) > amount ? 1 : 0), 0);
  return {
    rank,
    universe: values.length,
    topPct: (rank / values.length) * 100,
  };
}

function safeHeroRow(data, heroNumber) {
  const row = data?.heroes?.[heroNumber];
  if (!Array.isArray(row)) return null;
  return {
    number: heroNumber,
    rarity: data.lookups.rarities[row[0]] || 'Unknown',
    staked: Number(row[1]) === 1,
    quest: data.lookups.quest_buckets[row[2]] || 'Not staked',
    stakeSince: row[3] || '',
    lastQuest: row[4] || '',
  };
}

function safeMintRow(data, mintIndex) {
  const row = data?.mints?.[mintIndex];
  if (!Array.isArray(row)) return null;
  return {
    index: mintIndex,
    hero: Number.isInteger(row[0]) ? row[0] : null,
    utc: row[1] || '',
    phase: data.lookups.mint_phases[row[2]] || 'Public mint',
  };
}

function safeSaleRow(data, saleIndex) {
  const row = data?.sales?.[saleIndex];
  if (!Array.isArray(row)) return null;
  return {
    index: saleIndex,
    hero: Number(row[0]),
    utc: row[1] || '',
    sol: Number(row[2] || 0),
    marketplace: data.lookups.marketplaces[row[3]] || 'Marketplace',
    signature: row[4] || '',
  };
}

export function buildWalletView(data, addresses) {
  const selected = [...new Set((addresses || []).filter(Boolean))];
  const profiles = selected.map((address) => data?.wallets?.[address] || [[], [], [], []]);

  const currentHeroes = uniqueSortedNumbers(profiles.flatMap((row) => row[0] || []));
  const mintIndices = uniqueSortedNumbers(profiles.flatMap((row) => row[1] || []));
  const buyIndices = uniqueSortedNumbers(profiles.flatMap((row) => row[2] || []));
  const sellIndices = uniqueSortedNumbers(profiles.flatMap((row) => row[3] || []));
  const buySet = new Set(buyIndices);
  const sellSet = new Set(sellIndices);

  const heroes = currentHeroes.map((number) => safeHeroRow(data, number)).filter(Boolean);
  const stakedHeroes = heroes.filter((hero) => hero.staked);
  const rarityCounts = Object.fromEntries(data.lookups.rarities.map((name) => [name, 0]));
  const questCounts = Object.fromEntries(data.lookups.quest_buckets.slice(1).map((name) => [name, 0]));

  heroes.forEach((hero) => {
    if (rarityCounts[hero.rarity] !== undefined) rarityCounts[hero.rarity] += 1;
    if (hero.staked && questCounts[hero.quest] !== undefined) questCounts[hero.quest] += 1;
  });

  const mints = mintIndices.map((index) => safeMintRow(data, index)).filter(Boolean);
  const mintedHeroSet = new Set(mints.map((row) => row.hero).filter(Number.isInteger));
  const originalMintsHeld = currentHeroes.filter((number) => mintedHeroSet.has(number)).length;
  const mintPhaseCounts = {};
  mints.forEach((row) => {
    mintPhaseCounts[row.phase] = (mintPhaseCounts[row.phase] || 0) + 1;
  });

  // Supported-market economics deliberately use a Hero-level state machine.
  // A sale contributes to SOL received only if the selected wallet set has a
  // prior supported-market purchase of that same Hero. Sales of mints, OTC /
  // transferred Heroes, prizes, etc. are kept visible but excluded from the
  // matched cash-flow totals.
  const allMarketIndices = uniqueSortedNumbers([...buyIndices, ...sellIndices]);
  const marketAcquiredHeroes = new Set();
  const marketEvents = [];
  let purchaseCount = 0;
  let marketSpentSol = 0;
  let matchedResaleCount = 0;
  let matchedReceivedSol = 0;
  let unmatchedSaleCount = 0;
  let internalMarketTransfers = 0;

  for (const index of allMarketIndices) {
    const sale = safeSaleRow(data, index);
    if (!sale) continue;
    const bought = buySet.has(index);
    const sold = sellSet.has(index);

    if (bought && sold) {
      internalMarketTransfers += 1;
      continue;
    }

    if (bought) {
      purchaseCount += 1;
      marketSpentSol += sale.sol;
      marketAcquiredHeroes.add(sale.hero);
      marketEvents.push({ ...sale, kind: 'buy', matched: true });
      continue;
    }

    if (sold) {
      const matched = marketAcquiredHeroes.has(sale.hero);
      if (matched) {
        matchedResaleCount += 1;
        matchedReceivedSol += sale.sol;
        marketAcquiredHeroes.delete(sale.hero);
      } else {
        unmatchedSaleCount += 1;
      }
      marketEvents.push({ ...sale, kind: 'sell', matched });
    }
  }

  // For current holdings, use the open supported-market acquisition trail,
  // not merely "ever purchased". A Hero that was market-bought, later sold,
  // and is now owned again without another supported-market buy is correctly
  // classified as having no visible market-buy trail. Ordinary/OTC transfers
  // remain intentionally outside this dataset, so this is a conservative
  // provenance signal rather than a claim of complete transfer history.
  const noSupportedPurchase = currentHeroes.filter((number) => !marketAcquiredHeroes.has(number)).length;
  const collection = data.collection;
  const stakingPct = currentHeroes.length ? (stakedHeroes.length / currentHeroes.length) * 100 : 0;
  const supplyPct = collection.active_supply ? (currentHeroes.length / collection.active_supply) * 100 : 0;
  const ownershipRank = rankAgainst(data.benchmarks.holder_balances, currentHeroes.length);
  const buyerRank = rankAgainst(data.benchmarks.market_purchase_counts, purchaseCount);
  const minterRank = rankAgainst(data.benchmarks.public_mint_counts, mints.length);

  const stakeDates = stakedHeroes.map((hero) => hero.stakeSince).filter(Boolean).sort();
  const questDates = stakedHeroes.map((hero) => hero.lastQuest).filter(Boolean).sort();

  const timeline = [
    ...mints.map((row) => ({
      kind: 'mint',
      utc: row.utc,
      hero: row.hero,
      phase: row.phase,
    })),
    ...marketEvents,
  ].sort((a, b) => String(b.utc).localeCompare(String(a.utc)));

  return {
    addresses: selected,
    knownWallets: selected.filter((address) => Boolean(data.wallets[address])).length,
    heroes,
    heroCount: currentHeroes.length,
    stakedCount: stakedHeroes.length,
    stakingPct,
    supplyPct,
    ownershipRank,
    rarityCounts,
    questCounts,
    longestStakeSince: stakeDates[0] || '',
    newestStakeSince: stakeDates.at(-1) || '',
    mostRecentQuest: questDates.at(-1) || '',
    mints,
    mintCount: mints.length,
    mintPhaseCounts,
    originalMintsHeld,
    publicMintSol: mints.length * 1.5,
    minterRank,
    purchaseCount,
    marketSpentSol,
    matchedResaleCount,
    matchedReceivedSol,
    matchedNetFlowSol: matchedReceivedSol - marketSpentSol,
    unmatchedSaleCount,
    internalMarketTransfers,
    noSupportedPurchase,
    buyerRank,
    timeline,
    collection,
  };
}
