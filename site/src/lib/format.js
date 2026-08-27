export const num = (v, digits = 0) => Number(v).toLocaleString('en-US', {
  minimumFractionDigits: digits,
  maximumFractionDigits: digits,
});

export const compactDate = (value) => {
  const d = new Date(value);
  return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit', timeZone: 'UTC' });
};
