export function formatBytes(value: number | null, nullLabel = "—"): string {
  if (value === null) return nullLabel;
  if (value === 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go", "To", "Po"];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const amount = value / 1024 ** exponent;
  return `${new Intl.NumberFormat("fr-FR", {
    maximumFractionDigits: amount >= 10 || exponent === 0 ? 0 : 1,
  }).format(amount)} ${units[exponent]}`;
}
