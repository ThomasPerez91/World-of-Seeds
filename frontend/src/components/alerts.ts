import Swal from "sweetalert2";
import "sweetalert2/dist/sweetalert2.min.css";

const alert = Swal.mixin({
  background: "#101d18",
  color: "#dfe8e2",
  confirmButtonColor: "#6f9b62",
  cancelButtonColor: "#405047",
  customClass: {
    popup: "wos-alert-popup",
    confirmButton: "wos-alert-confirm",
    cancelButton: "wos-alert-cancel",
  },
  focusCancel: false,
  reverseButtons: true,
});

export async function showOperationSuccess(message: string): Promise<void> {
  await alert.fire({
    icon: "success",
    title: "Opération réussie",
    text: message,
    confirmButtonText: "Fermer",
  });
}

export async function showOperationError(message: string): Promise<void> {
  await alert.fire({
    icon: "error",
    title: "Action impossible",
    text: message,
    confirmButtonText: "Fermer",
  });
}

export async function confirmOperation({
  title,
  message,
  confirmText,
  destructive = false,
}: {
  title: string;
  message: string;
  confirmText: string;
  destructive?: boolean;
}): Promise<boolean> {
  const returnFocusTo =
    document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const result = await alert.fire({
    icon: destructive ? "warning" : "question",
    title,
    text: message,
    showCancelButton: true,
    confirmButtonText: confirmText,
    cancelButtonText: "Annuler",
    focusCancel: destructive,
    customClass: {
      popup: "wos-alert-popup",
      confirmButton: destructive ? "wos-alert-confirm destructive" : "wos-alert-confirm",
      cancelButton: "wos-alert-cancel",
    },
    didOpen: () => {
      const initialButton = destructive ? Swal.getCancelButton() : Swal.getConfirmButton();
      initialButton?.focus();
    },
  });
  if (returnFocusTo?.isConnected) returnFocusTo.focus();
  return result.isConfirmed;
}
