import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Task } from "../../api/types";
import { useTasks } from "../../api/hooks";
import { normalizeStatus, kindText } from "../../api/normalize";
import { useLibrary, DEFAULT_FILTERS, type GalleryFilters } from "../../store/library";
import { confirmAction, toast } from "../../store/feedback";
import { playSound } from "../../lib/sound";
import { cn, fmtRelative, fuzzyScore, toMs, downloadUrl } from "../../lib/utils";
import { Card, Button, Chip, Select, Input, Skeleton, EmptyState, Tabs, Tooltip, Badge } from "../../components/ui";
import { Modal } from "../../components/overlays";
import { TaskDetail } from "../../components/TaskDetail";
import { ImageTile, AudioPlayer, Lightbox, VoiceGlyph, mediaSrc } from "../../components/media";
import {
  IconGallery, IconSearch, IconStar, IconTrash, IconEye, IconDownload, IconGrip,
  IconRefresh, IconWand, IconCheck, IconPlus, IconClose, IconMic, IconImage, IconSort,
} from "../../components/icons";

export function GalleryPage() {
  const { data, isLoading, isError, refetch } = useTasks();
  const navigate = useNavigate();
  const lib = useLibrary();
  const [filters, setFilters] = useState<GalleryFilters>(DEFAULT_FILTERS);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<{ src: string; title: string } | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [viewName, setViewName] = useState("");

  const set = (patch: Partial<GalleryFilters>) => setFilters((f) => ({ ...f, ...patch }));

  const works = useMemo(() => {
    const base = (data?.tasks ?? []).filter((t) => normalizeStatus(t.status) === "completed" && (t.result?.url || t.result?.audio_url) && !lib.hidden.includes(t.id));
    let list = base.filter((t) => {
      if (filters.kind !== "all" && t.kind !== filters.kind) return false;
      if (filters.source && (t.source || "web") !== filters.source) return false;
      if (filters.favOnly && !lib.favorites.includes(t.id)) return false;
      if (filters.query && fuzzyScore(filters.query, `${t.prompt || ""} ${t.character || ""}`) <= 0) return false;
      return true;
    });
    const orderIdx = (id: string) => { const i = lib.order.indexOf(id); return i < 0 ? 1e9 : i; };
    list = [...list].sort((a, b) => {
      if (filters.sort === "custom") return orderIdx(a.id) - orderIdx(b.id) || (toMs(b.created_at) || 0) - (toMs(a.created_at) || 0);
      if (filters.sort === "oldest") return (toMs(a.created_at) || 0) - (toMs(b.created_at) || 0);
      return (toMs(b.created_at) || 0) - (toMs(a.created_at) || 0);
    });
    return list;
  }, [data, filters, lib.hidden, lib.favorites, lib.order]);

  const sources = useMemo(() => [...new Set((data?.tasks ?? []).map((t) => t.source || "web"))], [data]);
  const allSelected = works.length > 0 && works.every((t) => selected.has(t.id));
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const removeIds = (ids: string[]) => {
    ids.forEach((id) => lib.hide(id));
    setSelected(new Set());
    playSound("cancel");
    toast.undoable(`已从作品库移除 ${ids.length} 项`, () => { ids.forEach((id) => lib.unhide(id)); toast.success("已恢复"); }, "仅本机视图隐藏，原始任务与文件不受影响。");
  };
  const removeOne = async (t: Task) => {
    const ok = await confirmAction({ title: "从作品库移除", tone: "danger", confirmLabel: "移除", message: `确定移除「${(t.prompt || t.id).slice(0, 24)}」？这是本机视图隐藏，可撤销，不会删除原始任务或文件。` });
    if (ok) removeIds([t.id]);
  };
  const bulkRemove = async () => {
    if (!selected.size) return;
    const ok = await confirmAction({ title: `移除 ${selected.size} 项`, tone: "danger", confirmLabel: "移除", message: "确定从作品库移除选中项？可撤销，不影响原始任务。" });
    if (ok) removeIds([...selected]);
  };
  const bulkFav = () => {
    const ids = [...selected];
    const allFav = ids.every((id) => lib.favorites.includes(id));
    ids.forEach((id) => { if (allFav ? lib.favorites.includes(id) : !lib.favorites.includes(id)) lib.toggleFav(id); });
    toast.success(allFav ? "已批量取消收藏" : "已批量收藏");
  };

  const onDrop = (targetId: string) => {
    if (!dragId || dragId === targetId) { setDragId(null); setOverId(null); return; }
    const ids = works.map((t) => t.id);
    const from = ids.indexOf(dragId), to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    lib.setOrder(ids);
    setDragId(null); setOverId(null);
    playSound("click");
  };

  const saveView = () => {
    if (!viewName.trim()) { toast.warning("请输入视图名称"); return; }
    lib.saveView(viewName.trim(), filters);
    setViewName(""); setSaveOpen(false);
    toast.success("视图已保存", viewName.trim());
  };

  const download = async (t: Task) => {
    const url = t.result?.url || t.result?.audio_url;
    if (!url) return;
    await downloadUrl(mediaSrc(url), t.result?.filename || (t.kind === "image" ? "mobius.png" : "mobius.wav"));
    toast.success("已开始下载");
  };

  return (
    <div className="space-y-4">
      {/* 工具栏 */}
      <Card className="p-3.5">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative min-w-[180px] flex-1">
            <IconSearch size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
            <Input value={filters.query} onChange={(e) => set({ query: e.target.value })} placeholder="搜索作品…" className="pl-9" aria-label="搜索作品" />
          </div>
          <Tabs size="sm" value={filters.kind} onChange={(v) => set({ kind: v })} tabs={[
            { value: "all", label: "全部" }, { value: "image", label: <span className="flex items-center gap-1"><IconImage size={13} />图片</span> }, { value: "speech", label: <span className="flex items-center gap-1"><IconMic size={13} />语音</span> },
          ]} />
          <Select size="sm" value={filters.source} onChange={(v) => set({ source: v })} ariaLabel="来源" className="w-32"
            options={[{ value: "", label: "全部来源" }, ...sources.map((s) => ({ value: s, label: s }))]} />
          <Select size="sm" value={filters.sort} onChange={(v) => set({ sort: v as GalleryFilters["sort"] })} ariaLabel="排序" className="w-32"
            options={[{ value: "newest", label: "最新优先" }, { value: "oldest", label: "最早优先" }, { value: "custom", label: "自定义排序" }]} />
          <Chip active={filters.favOnly} onClick={() => set({ favOnly: !filters.favOnly })}><IconStar size={13} className={cn(filters.favOnly && "fill-warning text-warning")} /> 收藏</Chip>
          <Tooltip label="刷新"><Button variant="ghost" size="icon" onClick={() => refetch()} aria-label="刷新"><IconRefresh size={16} /></Button></Tooltip>
        </div>

        {/* 保存的视图 */}
        <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-line-soft pt-3">
          <span className="section-title mr-1">视图</span>
          {lib.views.length === 0 && <span className="text-[11px] text-faint">暂无保存的视图</span>}
          {lib.views.map((v) => (
            <span key={v.id} className="group inline-flex items-center gap-1 rounded-full border border-line bg-raised/60 pl-2.5 pr-1 h-7 text-xs text-muted">
              <button onClick={() => { setFilters(v.filters); toast.info("已应用视图", v.name); }} className="hover:text-brand-soft">{v.name}</button>
              <button onClick={async () => { const ok = await confirmAction({ title: "删除视图", tone: "danger", confirmLabel: "删除", message: `删除保存的视图「${v.name}」？` }); if (ok) lib.deleteView(v.id); }}
                className="grid h-4 w-4 place-items-center rounded-full text-faint hover:bg-danger/15 hover:text-danger" aria-label={`删除视图 ${v.name}`}><IconClose size={11} /></button>
            </span>
          ))}
          <Button variant="ghost" size="sm" className="ml-auto" icon={<IconPlus size={13} />} onClick={() => setSaveOpen(true)}>保存当前视图</Button>
        </div>
      </Card>

      {/* 批量条 */}
      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-brand/30 bg-brand/10 px-4 py-2.5 animate-fade-up">
          <IconCheck size={16} className="text-brand-soft" />
          <span className="text-sm text-ink">已选 {selected.size} 项</span>
          <div className="ml-auto flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" icon={<IconStar size={14} />} onClick={bulkFav}>收藏/取消</Button>
            <Button variant="danger" size="sm" icon={<IconTrash size={14} />} onClick={bulkRemove}>移除</Button>
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>清除</Button>
          </div>
        </div>
      )}

      {filters.sort === "custom" && works.length > 1 && (
        <p className="flex items-center gap-1.5 text-[11px] text-faint"><IconGrip size={13} /> 拖动卡片可调整顺序（本机保存）。{lib.order.length > 0 && <button className="text-brand-soft hover:underline" onClick={() => { lib.setOrder([]); toast.info("已重置自定义排序"); }}>重置排序</button>}</p>
      )}

      {/* 作品网格 */}
      {isLoading ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="aspect-square w-full" />)}</div>
      ) : isError ? (
        <EmptyState icon={<IconGallery size={22} />} title="加载失败" desc="无法获取作品，请检查本机服务连接。" action={<Button variant="primary" size="sm" icon={<IconRefresh size={14} />} onClick={() => refetch()}>重试</Button>} />
      ) : works.length === 0 ? (
        <EmptyState icon={<IconGallery size={22} />} title={lib.hidden.length ? "作品库为空" : "还没有作品"} desc={lib.hidden.length ? "所有作品都被移除了，可恢复或重新创作。" : "完成的图像与语音会自动收录到这里。"}
          action={<div className="flex gap-2">{lib.hidden.length > 0 && <Button variant="ghost" size="sm" onClick={() => { lib.hidden.forEach((id) => lib.unhide(id)); toast.success("已恢复全部"); }}>恢复移除项</Button>}<Button variant="primary" size="sm" icon={<IconWand size={14} />} onClick={() => navigate("/create")}>开始创作</Button></div>} />
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {works.map((t) => {
            const isSel = selected.has(t.id);
            const isFav = lib.favorites.includes(t.id);
            const draggable = filters.sort === "custom";
            return (
              <div key={t.id}
                draggable={draggable}
                onDragStart={() => setDragId(t.id)}
                onDragOver={(e) => { if (draggable) { e.preventDefault(); setOverId(t.id); } }}
                onDragLeave={() => setOverId((o) => (o === t.id ? null : o))}
                onDrop={() => onDrop(t.id)}
                onDragEnd={() => { setDragId(null); setOverId(null); }}
                className={cn("group relative", dragId === t.id && "opacity-40", overId === t.id && dragId !== t.id && "ring-2 ring-brand rounded-xl")}
              >
                <Card className={cn("overflow-hidden p-0", isSel && "ring-2 ring-brand")}>
                  {/* 选择框 */}
                  <button onClick={() => toggle(t.id)} aria-label={`选择 ${t.id}`}
                    className={cn("absolute left-2 top-2 z-10 grid h-5 w-5 place-items-center rounded border transition-all",
                      isSel ? "border-brand bg-brand text-white opacity-100" : "border-line bg-base/70 text-transparent opacity-0 backdrop-blur group-hover:opacity-100 hover:border-brand/60")}>
                    <IconCheck size={13} />
                  </button>
                  {/* 收藏 */}
                  <button onClick={() => { lib.toggleFav(t.id); toast.success(isFav ? "已取消收藏" : "已收藏"); }} aria-label={isFav ? "取消收藏" : "收藏"}
                    className={cn("absolute right-2 top-2 z-10 grid h-7 w-7 place-items-center rounded-md bg-base/70 backdrop-blur transition-all",
                      isFav ? "text-warning opacity-100" : "text-ink opacity-0 group-hover:opacity-100")}>
                    <IconStar size={15} className={cn(isFav && "fill-warning")} />
                  </button>

                  {t.kind === "image" ? (
                    <ImageTile src={t.result?.url} alt={t.prompt || "作品"} className="aspect-square w-full" onClick={() => setDetail(t.id)} />
                  ) : (
                    <button onClick={() => setDetail(t.id)} className="grid aspect-square w-full place-items-center bg-gradient-to-br from-cyan/12 to-brand/10">
                      <VoiceGlyph className="h-14 w-14" />
                    </button>
                  )}

                  <div className="p-3">
                    <p className="truncate text-xs text-ink" title={t.prompt}>{t.prompt || kindText(t.kind)}</p>
                    <div className="mt-1.5 flex items-center gap-2 text-[10px] text-faint">
                      <Badge tone="muted">{t.source || "web"}</Badge>
                      <span>{fmtRelative(t.created_at)}</span>
                      {draggable && <IconGrip size={12} className="ml-auto cursor-grab text-faint" />}
                    </div>
                    {t.kind !== "image" && (t.result?.url || t.result?.audio_url) && (
                      <AudioPlayer compact src={t.result?.url || t.result?.audio_url} className="mt-2" />
                    )}
                    <div className="mt-2 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                      <Tooltip label="详情"><Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setDetail(t.id)} aria-label="详情"><IconEye size={14} /></Button></Tooltip>
                      <Tooltip label="下载"><Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => download(t)} aria-label="下载"><IconDownload size={14} /></Button></Tooltip>
                      <Tooltip label="重新创作"><Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => navigate(`/create?tab=${t.kind === "image" ? "image" : "voice"}`, { state: { prefill: t } })} aria-label="重新创作"><IconWand size={14} /></Button></Tooltip>
                      <Tooltip label="移除"><Button variant="ghost" size="icon" className="h-7 w-7 ml-auto" onClick={() => removeOne(t)} aria-label="移除"><IconTrash size={14} className="text-danger" /></Button></Tooltip>
                    </div>
                  </div>
                </Card>
              </div>
            );
          })}
        </div>
      )}

      <p className="text-center text-[11px] text-faint">作品库由已完成任务派生 · 移除/收藏/排序仅保存在本机视图</p>

      <TaskDetail taskId={detail} onClose={() => setDetail(null)} />
      <Lightbox open={!!lightbox} onClose={() => setLightbox(null)} src={lightbox?.src} title={lightbox?.title} />

      <Modal open={saveOpen} onClose={() => setSaveOpen(false)} size="sm" title="保存当前视图"
        subtitle="保存筛选与排序，便于下次快速切换"
        footer={<><Button variant="ghost" onClick={() => setSaveOpen(false)}>取消</Button><Button variant="primary" onClick={saveView}>保存</Button></>}>
        <Input autoFocus value={viewName} onChange={(e) => setViewName(e.target.value)} placeholder="视图名称，如「凛的竖版作品」"
          onKeyDown={(e) => { if (e.key === "Enter") saveView(); }} aria-label="视图名称" />
        <div className="mt-3 flex flex-wrap gap-1.5 text-[11px] text-faint">
          <Badge tone="muted">{filters.kind === "all" ? "全部类型" : filters.kind === "image" ? "图片" : "语音"}</Badge>
          <Badge tone="muted">{filters.source || "全部来源"}</Badge>
          <Badge tone="muted">{filters.sort === "newest" ? "最新" : filters.sort === "oldest" ? "最早" : "自定义"}</Badge>
          {filters.favOnly && <Badge tone="warning">仅收藏</Badge>}
          {filters.query && <Badge tone="info">“{filters.query}”</Badge>}
        </div>
      </Modal>
    </div>
  );
}