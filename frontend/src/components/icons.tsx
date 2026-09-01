import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Download,
  File,
  FileVideo,
  Folder,
  FolderInput,
  LockKeyhole,
  Info,
  LoaderCircle,
  ListOrdered,
  Pencil,
  Gauge,
  RefreshCw,
  RotateCw,
  Save,
  Server,
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
  return <Trash2 {...decorative} {...props} />;
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
