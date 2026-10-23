export function formatBytes(
  value: number | null,
  nullLabel = "—",
  locale = "fr-FR",
): string {
  if (value === null || !Number.isFinite(value) || value < 0) return nullLabel;
  const units = locale.startsWith("fr")
    ? ["o", "Ko", "Mo", "Go", "To", "Po"]
    : ["B", "KB", "MB", "GB", "TB", "PB"];
  if (value < 1) return `0 ${units[0]}`;
  let exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  let amount = value / 1024 ** exponent;
  const fractionDigits = (scaled: number, unit: number) => (scaled >= 10 || unit === 0 ? 0 : 1);
  // 1 023,96 Ko must read "1 Mo", never "1 024 Ko".
  const factor = 10 ** fractionDigits(amount, exponent);
  if (Math.round(amount * factor) / factor >= 1024 && exponent < units.length - 1) {
    exponent += 1;
    amount = value / 1024 ** exponent;
  }
  return `${new Intl.NumberFormat(locale, {
    maximumFractionDigits: fractionDigits(amount, exponent),
  }).format(amount)} ${units[exponent]}`;
}
