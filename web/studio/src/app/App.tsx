import { HashRouter } from "react-router-dom";
import { Providers } from "./providers";
import { AppRoutes } from "./router";
import { CommandPalette } from "./CommandPalette";
import { Onboarding } from "./Onboarding";
import { ToastHost, ConfirmHost } from "../components/overlays";

export function App() {
  return (
    <Providers>
      <HashRouter>
        <AppRoutes />
        <CommandPalette />
        <Onboarding />
      </HashRouter>
      <ToastHost />
      <ConfirmHost />
    </Providers>
  );
}