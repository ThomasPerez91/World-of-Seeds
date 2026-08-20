import { type FileEntry } from "../api/client";

const compoundExtensions = [".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".user.js"];

export function splitDisplayName(entry: FileEntry): { basename: string; extension: string } {
  if (entry.kind !== "file") return { basename: entry.name, extension: "" };
  if (entry.name.startsWith(".") && entry.name.indexOf(".", 1) === -1) {
    return { basename: entry.name, extension: "" };
  }
  const lowered = entry.name.toLowerCase();
  const compound = compoundExtensions.find(
    (extension) => lowered.endsWith(extension) && entry.name.length > extension.length,
  );
  if (compound !== undefined) {
    return {
      basename: entry.name.slice(0, -compound.length),
      extension: entry.name.slice(-compound.length),
    };
  }
  const dot = entry.name.lastIndexOf(".");
  if (dot <= 0 || dot === entry.name.length - 1) {
    return { basename: entry.name, extension: "" };
  }
  return { basename: entry.name.slice(0, dot), extension: entry.name.slice(dot) };
}
