import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./Layout";
import { StudioHome } from "../features/studio/StudioHome";
import { CreatePage } from "../features/create/CreatePage";
import { TasksPage } from "../features/tasks/TasksPage";
import { GalleryPage } from "../features/gallery/GalleryPage";
import { CharactersPage } from "../features/characters/CharactersPage";
import { RegistryPage } from "../features/registry/RegistryPage";
import { ChatPage } from "../features/chat/ChatPage";
import { SystemPage } from "../features/system/SystemPage";
import { SettingsPage } from "../features/settings/SettingsPage";

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<StudioHome />} />
        <Route path="create" element={<CreatePage />} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="gallery" element={<GalleryPage />} />
        <Route path="characters" element={<CharactersPage />} />
        <Route path="registry" element={<RegistryPage />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="chat/:sessionId" element={<ChatPage />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}