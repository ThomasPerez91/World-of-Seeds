import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Database,
  Download,
  File,
  FileVideo,
  Folder,
  FolderInput,
  Gauge,
  Home,
  Info,
  LoaderCircle,
  LockKeyhole,
  ListOrdered,
  MonitorDown,
  Pencil,
  RefreshCw,
  RotateCw,
  Save,
  Search,
  Server,
  ShieldCheck,
  Settings2,
  Sprout,
  Trash2,
  TriangleAlert,
  X,
  type LucideProps,
} from "lucide-react";

type AppIconProps = Omit<LucideProps, "aria-hidden" | "focusable">;

const decorative = {
  "aria-hidden": true,
  focusable: false,
} as const;

export function BrandIcon(props: AppIconProps) {
  return <Sprout {...decorative} {...props} />;
}

export function HomeIcon(props: AppIconProps) {
  return <Home {...decorative} {...props} />;
}

export function ActivityIcon(props: AppIconProps) {
  return <Activity {...decorative} {...props} />;
}

export function StorageIcon(props: AppIconProps) {
  return <Database {...decorative} {...props} />;
}

export function LocalDownloadIcon(props: AppIconProps) {
  return <MonitorDown {...decorative} {...props} />;
}

export function SearchIcon(props: AppIconProps) {
  return <Search {...decorative} {...props} />;
}

export function FolderIcon(props: AppIconProps) {
  return <Folder {...decorative} {...props} />;
}

export function FileIcon(props: AppIconProps) {
  return <File {...decorative} {...props} />;
}

export function VideoFileIcon(props: AppIconProps) {
  return <FileVideo {...decorative} {...props} />;
}

export function LockedEntryIcon(props: AppIconProps) {
  return <LockKeyhole {...decorative} {...props} />;
}

export function DownloadIcon(props: AppIconProps) {
  return <Download {...decorative} {...props} />;
}

export function RenameIcon(props: AppIconProps) {
  return <Pencil {...decorative} {...props} />;
}

export function MoveIcon(props: AppIconProps) {
  return <FolderInput {...decorative} {...props} />;
}

export function DeleteIcon(props: AppIconProps) {
  const iconRef = useRef<SVGSVGElement>(null);
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    const button = iconRef.current?.closest("button");
    if (!(button instanceof HTMLButtonElement) || button.closest(".torrent-card-actions") === null) {
      return;
    }

    const handleClick = (event: MouseEvent) => {
      if (armed) {
        window.setTimeout(() => setArmed(false), 0);
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      setArmed(true);
    };
    const handleBlur = () => setArmed(false);

    button.setAttribute("aria-pressed", armed ? "true" : "false");
    button.addEventListener("click", handleClick, true);
    button.addEventListener("blur", handleBlur);
    return () => {
      button.removeEventListener("click", handleClick, true);
      button.removeEventListener("blur", handleBlur);
      button.removeAttribute("aria-pressed");
    };
  }, [armed]);

  return armed
    ? <Check ref={iconRef} {...decorative} {...props} />
    : <Trash2 ref={iconRef} {...decorative} {...props} />;
}

export function OpenIcon(props: AppIconProps) {
  return <ChevronRight {...decorative} {...props} />;
}

export function AccountMenuIcon(props: AppIconProps) {
  return <ChevronDown {...decorative} {...props} />;
}

export function BackIcon(props: AppIconProps) {
  return <ArrowLeft {...decorative} {...props} />;
}

export function CloseIcon(props: AppIconProps) {
  return <X {...decorative} {...props} />;
}

export function NewGreedyServiceIcon(props: AppIconProps) {
  return <Server {...decorative} {...props} />;
}

export function QBittorrentServiceIcon(props: AppIconProps) {
  return <Gauge {...decorative} {...props} />;
}

export function RefreshIcon(props: AppIconProps) {
  return <RefreshCw {...decorative} {...props} />;
}

export function SettingsIcon(props: AppIconProps) {
  return <Settings2 {...decorative} {...props} />;
}

export function AdminIcon(props: AppIconProps) {
  return <ShieldCheck {...decorative} {...props} />;
}

export function SaveIcon(props: AppIconProps) {
  return <Save {...decorative} {...props} />;
}

export function RestartIcon(props: AppIconProps) {
  return <RotateCw {...decorative} {...props} />;
}

export function SuccessIcon(props: AppIconProps) {
  return <CheckCircle2 {...decorative} {...props} />;
}

export function ErrorIcon(props: AppIconProps) {
  return <CircleAlert {...decorative} {...props} />;
}

export function WarningIcon(props: AppIconProps) {
  return <TriangleAlert {...decorative} {...props} />;
}

export function InfoIcon(props: AppIconProps) {
  return <Info {...decorative} {...props} />;
}

export function LoadingIcon(props: AppIconProps) {
  return <LoaderCircle {...decorative} {...props} />;
}

export function QueueIcon(props: AppIconProps) {
  return <ListOrdered {...decorative} {...props} />;
}
