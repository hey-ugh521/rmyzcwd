import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Task, TaskStatus } from "../../api/types";
import { useTasks, useCancelTask } from "../../api/hooks";
import { normalizeStatus, statusText, kindText, isActive, ALL_STATUSES, statusTone } from "../../api/normalize";
import { confirmAction, toast } from "../../store/feedback";
import { playSound } from "../../lib/sound";
import { cn, fmtRelative, fmtDuration, toMs, fuzzyScore } from "../../lib/utils";
import { Card, Button, StatusBadge, KindBadge, Badge, Chip, Select, Input, Skeleton, EmptyState, ProgressBar, Tabs, Tooltip, Led } from "../../components/ui";
import { TaskDetail } from "../../components/TaskDetail";
import { ImageTile, VoiceGlyph } from "../../components/media";
import {
  IconTasks, IconSearch, IconStop, IconRefresh, IconEye, IconWand, IconClose,
  IconSort, IconCheck, IconClock, IconLayers,
} from "../../components/icons";

function dur(t: Task) {
  const s = toMs(t.started_at), f = toMs(t.finished_at);
  if (!Number.isNaN(s) && !Number.isNaN(f)) return fmtDuration((f - s) / 1000);
  if (!Number.isNaN(s) && isActive(t.status)) return fmtDuration((Date.now() - s) / 1000) + "…";
  return "—";
}

export function TasksPage() {
  const { data, isLoading, isError, refetch, isFetching } = useTasks();
  const cancel = useCancelTask();
  const navigate = useNavigate();
  const [status, setStatus] = useState<"all" | TaskStatus>("all");
  const [kind, setKind] = useState<"all" | "image" | "speech">("all");
  const [source, setSource] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("newest");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<string | null>(null);

  const all = data?.tasks ?? [];
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: all.length };
    ALL_STATUSES.forEach((s) => { c[s] = all.filter((t) => normalizeStatus(t.status) === s).length; });
    return c;
  }, [all]);
  const sources = useMemo(() => [...new Set(all.map((t) => t.source || "web"))], [all]);

  const filtered = useMemo(() => {
    let list = all.filter((t) => {
      if (status !== "all" && normalizeStatus(t.status) !== status) return false;
      if (kind !== "all" && t.kind !== kind) return false;
      if (source && (t.source || "web") !== source) return false;
      if (query && fuzzyScore(query, `${t.prompt || ""} ${t.id} ${t.character || ""}`) <= 0) return false;
      return true;
    });
    list = [...list].sort((a, b) => {
      if (sort === "oldest") return (toMs(a.created_at) || 0) - (toMs(b.created_at) || 0);
      if (sort === "status") return normalizeStatus(a.status).localeCompare(normalizeStatus(b.status));
      return (toMs(b.created_at) || 0) - (toMs(a.created_at) || 0);
    });
    return list;
  }, [all, status, kind, source, query, sort]);

  const activeSelected = filtered.filter((t) => selected.has(t.id) && isActive(t.status));
  const allSelected = filtered.length > 0 && filtered.every((t) => selected.has(t.id));

  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(filtered.map((t) => t.id)));

  const cancelOne = async (t: Task) => {
    const ok = await confirmAction({ title: "取消任务", tone: "danger", confirmLabel: "取消任务", message: `确定取消「${(t.prompt || t.id).slice(0, 28)}」？终态由执行端确认。` });
    if (ok) { playSound("cancel"); cancel.mutate(t.id); }
  };
  const bulkCancel = async () => {
    if (!activeSelected.length) { toast.info("没有可取消的任务", "仅排队 / 运行中的任务可取消。"); return; }
    const ok = await confirmAction({ title: `批量取消 ${activeSelected.length} 个任务`, tone: "danger", confirmLabel: "全部取消", message: "确定取消选中的进行中任务？已消耗的算力不会退回。" });
    if (!ok) return;
    playSound("cancel");
    for (const t of activeSelected) cancel.mutate(t.id);
    setSelected(new Set());
  };

  const reCreate = (t: Task) => navigate(`/create?tab=${t.kind === "image" ? "image" : "voice"}`, { state: { prefill: t } });

  return (
    <div className="space-y-4">
      {/* 工具栏 */}
      <Card className="p-3.5">
        <div className="flex flex-wrap items-center gap-2.5">
          <div className="relative min-w-[180px] flex-1">
            <IconSearch size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索提示词 / 任务 ID / 角色…" className="pl-9" aria-label="搜索任务" />
          </div>
          <Tabs size="sm" value={kind} onChange={setKind} tabs={[
            { value: "all", label: "全部" }, { value: "image", label: "图片" }, { value: "speech", label: "语音" },
          ]} />
          <Select size="sm" value={source} onChange={setSource} ariaLabel="来源" className="w-32"
            options={[{ value: "", label: "全部来源" }, ...sources.map((s) => ({ value: s, label: s }))]} />
          <Select size="sm" value={sort} onChange={setSort} ariaLabel="排序" className="w-32"
            options={[{ value: "newest", label: "最新优先" }, { value: "oldest", label: "最早优先" }, { value: "status", label: "按状态" }]} />
          <Tooltip label="刷新">
            <Button variant="ghost" size="icon" onClick={() => refetch()} aria-label="刷新"><IconRefresh size={16} className={cn(isFetching && "animate-spin-slow")} /></Button>
          </Tooltip>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <Chip active={status === "all"} onClick={() => setStatus("all")}>全部 <span className="tnum opacity-70">{counts.all}</span></Chip>
          {ALL_STATUSES.map((s) => (
            <Chip key={s} active={status === s} onClick={() => setStatus(s)}>
              <Led tone={statusTone(s)} /> {statusText(s)} <span className="tnum opacity-70">{counts[s] ?? 0}</span>
            </Chip>
          ))}
        </div>
      </Card>

      {/* 批量操作条 */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 rounded-lg border border-brand/30 bg-brand/10 px-4 py-2.5 animate-fade-up">
          <IconCheck size={16} className="text-brand-soft" />
          <span className="text-sm text-ink">已选 {selected.size} 项</span>
          <span className="text-xs text-faint">（{activeSelected.length} 个可取消）</span>
          <div className="ml-auto flex gap-2">
            <Button variant="danger" size="sm" icon={<IconStop size={14} />} loading={cancel.isPending} onClick={bulkCancel}>批量取消</Button>
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>清除选择</Button>
          </div>
        </div>
      )}

      {/* 列表 */}
      <Card className="overflow-hidden">
        <div className="flex items-center gap-3 border-b border-line-soft px-4 py-2.5">
          <button onClick={toggleAll} aria-label="全选" className={cn("grid h-5 w-5 place-items-center rounded border transition-colors",
            allSelected ? "border-brand bg-brand text-white" : "border-line bg-base/50 text-transparent hover:border-brand/50")}>
            <IconCheck size={13} />
          </button>
          <span className="text-xs text-muted">{filtered.length} 个任务</span>
          <span className="ml-auto flex items-center gap-1 text-[11px] text-faint"><IconSort size={12} /> {sort === "newest" ? "最新" : sort === "oldest" ? "最早" : "状态"}</span>
        </div>

        {isLoading ? (
          <div className="space-y-2 p-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}</div>
        ) : isError ? (
          <EmptyState icon={<IconTasks size={22} />} title="加载失败" desc="无法获取任务列表，请检查本机服务连接。"
            action={<Button variant="primary" size="sm" icon={<IconRefresh size={14} />} onClick={() => refetch()}>重试</Button>} />
        ) : filtered.length === 0 ? (
          <EmptyState icon={<IconTasks size={22} />} title="没有符合条件的任务" desc="调整筛选条件，或开始一次新的创作。"
            action={<Button variant="primary" size="sm" icon={<IconWand size={14} />} onClick={() => navigate("/create")}>新建创作</Button>} />
        ) : (
          <ul className="divide-y divide-line-soft">
            {filtered.map((t) => {
              const active = isActive(t.status);
              const isSel = selected.has(t.id);
              return (
                <li key={t.id} className={cn("group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-raised/40", isSel && "bg-brand/8")}>
                  <button onClick={() => toggle(t.id)} aria-label={`选择 ${t.id}`} className={cn("grid h-5 w-5 flex-none place-items-center rounded border transition-colors",
                    isSel ? "border-brand bg-brand text-white" : "border-line bg-base/50 text-transparent hover:border-brand/50")}>
                    <IconCheck size={13} />
                  </button>

                  {/* 缩略 */}
                  <button onClick={() => setDetail(t.id)} className="h-11 w-11 flex-none overflow-hidden rounded-md ring-1 ring-line-soft" aria-label="查看详情">
                    {t.kind === "image" && t.result?.url ? (
                      <ImageTile src={t.result.url} alt="" rounded="rounded-none" className="h-full w-full" />
                    ) : t.kind === "image" ? (
                      <span className="grid h-full w-full place-items-center bg-raised"><IconLayers size={16} className="text-faint" /></span>
                    ) : (
                      <span className="grid h-full w-full place-items-center bg-cyan/10"><VoiceGlyph className="h-7 w-7" /></span>
                    )}
                  </button>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <button onClick={() => setDetail(t.id)} className="truncate text-sm text-ink hover:text-brand-soft" title={t.prompt}>
                        {t.prompt || t.id}
                      </button>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-faint">
                      <KindBadge kind={t.kind} />
                      <Badge tone="muted">{t.source || "web"}</Badge>
                      {t.character && <span>{t.character}</span>}
                      <span className="tnum inline-flex items-center gap-1"><IconClock size={11} />{dur(t)}</span>
                      <span>{fmtRelative(t.created_at)}</span>
                    </div>
                    {active && <ProgressBar indeterminate className="mt-2 max-w-xs" tone={normalizeStatus(t.status) === "pending" ? "warning" : "brand"} />}
                  </div>

                  <div className="hidden flex-none sm:block"><StatusBadge status={t.status} /></div>

                  <div className="flex flex-none items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    <Tooltip label="详情"><Button variant="ghost" size="icon" onClick={() => setDetail(t.id)} aria-label="详情"><IconEye size={15} /></Button></Tooltip>
                    <Tooltip label="重新创作"><Button variant="ghost" size="icon" onClick={() => reCreate(t)} aria-label="重新创作"><IconWand size={15} /></Button></Tooltip>
                    {active && <Tooltip label="取消"><Button variant="ghost" size="icon" onClick={() => cancelOne(t)} aria-label="取消"><IconStop size={15} className="text-danger" /></Button></Tooltip>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <TaskDetail taskId={detail} onClose={() => setDetail(null)} />
    </div>
  );
}