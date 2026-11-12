import { create } from "zustand";

// 作品库为前端派生视图（后端暂无媒体聚合 API）。
// 移除=本地隐藏（可撤销），收藏/排序/保存视图均为本机 UI 状态，不含任何密钥或真实用户数据。
export interface GalleryFilters {
  kind: "all" | "image" | "speech";
  source: string; // "" = 全部
  query: string;
  sort: "newest" | "oldest" | "custom";
  favOnly: boolean;
}

export interface SavedView {
  id: string;
  name: string;
  filters: GalleryFilters;
}

export const DEFAULT_FILTERS: GalleryFilters = {
  kind: "all", source: "", query: "", sort: "newest", favOnly: false,
};

interface LibraryState {
  hidden: string[];
  favorites: string[];
  order: string[];
  views: SavedView[];
  hide: (id: string) => void;
  unhide: (id: string) => void;
  toggleFav: (id: string) => void;
  setOrder: (ids: string[]) => void;
  saveView: (name: string, filters: GalleryFilters) => string;
  deleteView: (id: string) => void;
  renameView: (id: string, name: string) => void;
  resetLibrary: () => void;
}

const LS_KEY = "mobius-studio-library";

function load(): Partial<LibraryState> {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}
function persist(s: LibraryState) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      hidden: s.hidden, favorites: s.favorites, order: s.order, views: s.views,
    }));
  } catch { /* ignore */ }
}

const saved = load();
const uidView = () => `view_${Math.random().toString(36).slice(2, 9)}`;

export const useLibrary = create<LibraryState>((set, get) => ({
  hidden: saved.hidden ?? [],
  favorites: saved.favorites ?? [],
  order: saved.order ?? [],
  views: saved.views ?? [],
  hide: (id) => { set((s) => ({ hidden: [...new Set([...s.hidden, id])] })); persist(get()); },
  unhide: (id) => { set((s) => ({ hidden: s.hidden.filter((x) => x !== id) })); persist(get()); },
  toggleFav: (id) => {
    set((s) => ({ favorites: s.favorites.includes(id) ? s.favorites.filter((x) => x !== id) : [...s.favorites, id] }));
    persist(get());
  },
  setOrder: (ids) => { set({ order: ids }); persist(get()); },
  saveView: (name, filters) => {
    const id = uidView();
    set((s) => ({ views: [...s.views, { id, name, filters }] }));
    persist(get());
    return id;
  },
  deleteView: (id) => { set((s) => ({ views: s.views.filter((v) => v.id !== id) })); persist(get()); },
  renameView: (id, name) => { set((s) => ({ views: s.views.map((v) => v.id === id ? { ...v, name } : v) })); persist(get()); },
  resetLibrary: () => { set({ hidden: [], favorites: [], order: [], views: [] }); persist(get()); },
}));