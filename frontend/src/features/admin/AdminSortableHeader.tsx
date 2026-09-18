import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

export type AdminSortOrder = "asc" | "desc";

export function AdminSortableHeader({
  active,
  align = "left",
  direction,
  label,
  onSort,
  sortLabel,
}: {
  active: boolean;
  align?: "left" | "center" | "right";
  direction: AdminSortOrder;
  label: string;
  onSort: () => void;
  sortLabel: string;
}) {
  const Icon = !active ? ChevronsUpDown : direction === "asc" ? ArrowUp : ArrowDown;

  return (
    <th
      className={`admin-table-column-${align}`}
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        className="admin-table-sort"
        onClick={onSort}
        aria-label={sortLabel}
      >
        <span>{label}</span>
        <Icon aria-hidden="true" />
      </button>
    </th>
  );
}
