/* Blocking, same-origin bootstrap: also the provider's single browser theme adapter. */
window.wosTheme = (() => {
  const key = "wos.preferred-theme";
  const valid = (value) => ["light", "dark", "system"].includes(value);
  const read = () => {
    try {
      const value = localStorage.getItem(key);
      return valid(value) ? value : "system";
    } catch {
      return "system";
    }
  };
  const resolve = (preferred) => preferred === "system"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : preferred;
  const apply = (preferred) => {
    const effective = resolve(preferred);
    document.documentElement.dataset.theme = effective;
    document.documentElement.style.colorScheme = effective;
    try { localStorage.setItem(key, preferred); } catch { /* Storage may be disabled. */ }
    return effective;
  };
  apply(read());
  return { read, resolve, apply };
})();
