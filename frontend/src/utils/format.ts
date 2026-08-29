export function formatBytes(
  value: number | null,
  nullLabel = "—",
  locale = "fr-FR",
): string {
  if (value === null) return nullLabel;
  const units = locale.startsWith("fr")
    ? ["o", "Ko", "Mo", "Go", "To", "Po"]
    : ["B", "KB", "MB", "GB", "TB", "PB"];
  if (value === 0) return `0 ${units[0]}`;
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const amount = value / 1024 ** exponent;
  return `${new Intl.NumberFormat(locale, {
    maximumFractionDigits: amount >= 10 || exponent === 0 ? 0 : 1,
  }).format(amount)} ${units[exponent]}`;
}
