import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import type { ChatMessage, ChatEvent } from "../../api/types";
import { getApi } from "../../api";
import { useSessions, useMessages, useCharacters, useTasks, useResetSession, qk } from "../../api/hooks";
import { useApiMode } from "../../api/hooks";
import { normalizeStatus } from "../../api/normalize";
import { confirmAction, toast } from "../../store/feedback";
import { playSound } from "../../lib/sound";
import { cn, uid, fmtRelative, fmtClock } from "../../lib/utils";
import { Card, Button, Select, Skeleton, EmptyState, Badge, Tooltip, StatusBadge } from "../../components/ui";
import { TaskDetail } from "../../components/TaskDetail";
import { ImageTile, AudioPlayer, VoiceGlyph } from "../../components/media";
import {
  IconChat, IconSend, IconStop, IconPlus, IconRefresh, IconBot, IconUser,
  IconImage, IconMic, IconWand, IconClose, IconSpinner,
} from "../../components/icons";

const SUGGESTIONS = [
  "画一张赛博朋克都市夜景，霓虹反光",
  "来一张黄昏海边的灯塔，写实摄影",
  "帮我画水墨山水，留白远山",
  "现在系统状态怎么样？",
];

function TaskEmbed({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  const { data } = useTasks();
  const t = data?.tasks.find((x) => x.id === id);
  if (!t) return null;
  const st = normalizeStatus(t.status);
  if (t.kind === "image") {
    return (
      <button onClick={() => onOpen(id)} className="group relative mt-2 block w-40 overflow-hidden rounded-lg ring-1 ring-line-soft">
        {st === "completed" && t.result?.url ? (
          <ImageTile src={t.result.url} alt={t.prompt || "生成结果"} className="aspect-square w-full" />
        ) : (
          <span className="grid aspect-square w-full place-items-center bg-raised">
            {st === "failed" ? <IconClose size={20} className="text-danger" /> : <IconSpinner size={20} className="text-brand-soft" />}
          </span>
        )}
        <span className="absolute left-1.5 top-1.5"><StatusBadge status={t.status} /></span>
      </button>
    );
  }
  return (
    <div className="mt-2 w-full max-w-sm">
      {st === "completed" && (t.result?.url || t.result?.audio_url)
        ? <AudioPlayer compact src={t.result?.url || t.result?.audio_url} />
        : <div className="flex items-center gap-2 rounded-md border border-line-soft bg-raised/50 px-3 py-2 text-xs text-muted"><VoiceGlyph className="h-6 w-6" /> {st === "failed" ? "合成失败" : "合成中…"}</div>}
    </div>
  );
}

function Bubble({ m, onOpenTask }: { m: ChatMessage; onOpenTask: (id: string) => void }) {
  const user = m.role === "user";
  return (
    <div className={cn("flex gap-3", user && "flex-row-reverse")}>
      <span className={cn("grid h-8 w-8 flex-none place-items-center rounded-lg ring-1",
        user ? "bg-brand/15 text-brand-soft ring-brand/25" : "bg-magenta/12 text-magenta ring-magenta/25")}>
        {user ? <IconUser size={16} /> : <IconBot size={16} />}
      </span>
      <div className={cn("min-w-0 max-w-[78%]", user && "items-end")}>
        <div className={cn("rounded-xl px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap break-words",
          user ? "bg-brand/15 text-ink" : "border border-line-soft bg-raised/60 text-ink",
          m.error && "border-danger/30 bg-danger/10")}>
          {m.text || (m.streaming ? <span className="inline-flex items-center gap-1.5 text-muted"><IconSpinner size={13} /> 思考中…</span> : "")}
          {m.streaming && m.text && <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-brand-soft align-middle" />}
        </div>
        {m.task_ids?.map((id) => <TaskEmbed key={id} id={id} onOpen={onOpenTask} />)}
        <p className={cn("mt-1 text-[10px] text-faint", user && "text-right")}>{fmtClock(m.created_at)}</p>
      </div>
    </div>
  );
}

export function ChatPage() {
  const { sessionId } = useParams();
  const sid = sessionId || "session:web:local";
  const navigate = useNavigate();
  const qc = useQueryClient();
  const mode = useApiMode();
  const sessions = useSessions();
  const messages = useMessages(sid);
  const chars = useCharacters();
  const reset = useResetSession();

  const [local, setLocal] = useState<ChatMessage[]>([]);
  const [streamText, setStreamText] = useState("");
  const [streamId, setStreamId] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [input, setInput] = useState("");
  const [character, setCharacter] = useState("mobius");
  const [detail, setDetail] = useState<string | null>(null);
  const [showSessions, setShowSessions] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const charList = chars.data?.characters ?? [];
  const sessionList = sessions.data?.sessions ?? [];
  const currentSession = sessionList.find((s) => s.session_id === sid);

  useEffect(() => {
    if (currentSession?.character) setCharacter(currentSession.character);
  }, [currentSession?.character]);

  const displayed = useMemo(() => {
    const base = messages.data?.messages ?? [];
    const arr = [...base, ...local];
    if (streaming) arr.push({ id: streamId || "streaming", role: "assistant", text: streamText, created_at: Date.now(), streaming: true });
    return arr;
  }, [messages.data, local, streaming, streamText, streamId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [displayed.length, streamText]);

  // 切换会话 / 卸载时中止流
  useEffect(() => {
    setLocal([]); setStreamText(""); setStreaming(false); setStreamId(null);
    return () => { abortRef.current?.abort(); };
  }, [sid]);

  const newSession = () => {
    const id = `session:web:${uid("s").slice(2, 8)}`;
    navigate(`/chat/${encodeURIComponent(id)}`);
    setShowSessions(false);
    toast.info("已新建会话");
  };

  const send = async (textArg?: string) => {
    const text = (textArg ?? input).trim();
    if (!text || streaming) return;
    setInput("");
    playSound("submit");
    const userMsg: ChatMessage = { id: uid("m"), role: "user", text, created_at: Date.now() };
    setLocal((l) => [...l, userMsg]);
    setStreaming(true); setStreamText(""); setStreamId(null);
    const controller = new AbortController();
    abortRef.current = controller;
    let acc = "";
    let replyId = uid("r");
    let taskIds: string[] = [];
    try {
      await getApi().chatStream(
        { source: "web", user_id: "web:local", session_id: sid, text, character: character || null },
        (ev: ChatEvent) => {
          if (ev.type === "meta") { replyId = ev.reply_id; setStreamId(ev.reply_id); }
          else if (ev.type === "text") { acc += ev.delta; setStreamText(acc); }
          else if (ev.type === "done") { acc = ev.reply || acc; taskIds = ev.task_ids ?? []; }
          else if (ev.type === "error") { throw new Error(ev.message || ev.error); }
        },
        controller.signal,
      );
      const assistant: ChatMessage = { id: replyId, role: "assistant", text: acc, created_at: Date.now(), task_ids: taskIds };
      setLocal((l) => [...l, assistant]);
      setStreaming(false); setStreamText(""); setStreamId(null);
      if (taskIds.length) playSound("submit");
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.messages(mode, sid) }),
        qc.invalidateQueries({ queryKey: qk.sessions(mode) }),
        qc.invalidateQueries({ queryKey: qk.tasks(mode) }),
      ]);
      setLocal([]);
    } catch (e) {
      setStreaming(false); setStreamText(""); setStreamId(null);
      if ((e as Error)?.name === "AbortError") {
        if (acc) setLocal((l) => [...l, { id: replyId, role: "assistant", text: acc + " …（已停止）", created_at: Date.now(), task_ids: taskIds }]);
        toast.info("已停止生成");
      } else {
        setLocal((l) => [...l, { id: replyId, role: "assistant", text: "", created_at: Date.now(), error: (e as Error)?.message || "对话出错" }]);
        toast.error("对话出错", (e as Error)?.message);
      }
    } finally {
      abortRef.current = null;
    }
  };

  const stop = () => { abortRef.current?.abort(); };

  const doReset = async () => {
    const ok = await confirmAction({ title: "清空会话", tone: "danger", confirmLabel: "清空", message: "确定清空当前会话的全部消息？此操作不可撤销。" });
    if (ok) { reset.mutate(sid); setLocal([]); }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };

  return (
    <div className="flex h-[calc(100vh-var(--topbar-h)-3rem)] gap-4">
      {/* 会话列表 */}
      <Card className={cn("flex w-64 flex-none flex-col overflow-hidden", showSessions ? "absolute inset-x-4 z-30 h-[70vh]" : "hidden lg:flex")}>
        <div className="flex items-center gap-2 border-b border-line-soft p-3">
          <IconChat size={16} className="text-brand-soft" />
          <span className="text-sm font-semibold text-ink">会话</span>
          <Tooltip label="新建会话"><Button variant="ghost" size="icon" className="ml-auto h-7 w-7" onClick={newSession} aria-label="新建会话"><IconPlus size={15} /></Button></Tooltip>
          <Button variant="ghost" size="icon" className="h-7 w-7 lg:hidden" onClick={() => setShowSessions(false)} aria-label="关闭"><IconClose size={15} /></Button>
        </div>
        <div className="scroll-y flex-1 p-2">
          {sessions.isLoading ? <Skeleton className="h-20 w-full" /> : sessionList.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-faint">暂无会话</p>
          ) : sessionList.map((s) => {
            const c = charList.find((x) => x.id === s.character);
            return (
              <button key={s.session_id} onClick={() => { navigate(`/chat/${encodeURIComponent(s.session_id)}`); setShowSessions(false); }}
                className={cn("mb-1 flex w-full items-start gap-2.5 rounded-lg p-2.5 text-left transition-colors",
                  s.session_id === sid ? "bg-brand/12 ring-1 ring-brand/25" : "hover:bg-raised/60")}>
                <span className="grid h-8 w-8 flex-none place-items-center rounded-lg bg-magenta/12 text-magenta ring-1 ring-magenta/20"><IconBot size={15} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium text-ink">{s.title || c?.name || "会话"}</span>
                  <span className="block truncate text-[11px] text-faint">{s.preview || "…"}</span>
                  <span className="mt-0.5 block text-[10px] text-faint">{fmtRelative(s.updated_at)} · {s.message_count ?? 0} 条</span>
                </span>
              </button>
            );
          })}
        </div>
      </Card>

      {/* 主聊天区 */}
      <Card className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <div className="flex items-center gap-2 border-b border-line-soft px-4 py-2.5">
          <Button variant="ghost" size="icon" className="h-8 w-8 lg:hidden" onClick={() => setShowSessions(true)} aria-label="会话列表"><IconChat size={16} /></Button>
          <span className="grid h-8 w-8 flex-none place-items-center rounded-lg bg-magenta/12 text-magenta ring-1 ring-magenta/20"><IconBot size={16} /></span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-ink">{currentSession?.title || "创作助手"}</p>
            <p className="text-[11px] text-faint">{charList.find((c) => c.id === character)?.name || "小莫"} · 本机流式对话</p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Select size="sm" value={character} onChange={setCharacter} ariaLabel="角色" className="w-28"
              options={charList.map((c) => ({ value: c.id, label: c.name || c.id }))} />
            <Tooltip label="清空会话"><Button variant="ghost" size="icon" className="h-8 w-8" onClick={doReset} aria-label="清空会话"><IconRefresh size={15} /></Button></Tooltip>
          </div>
        </div>

        <div ref={scrollRef} className="scroll-y flex-1 space-y-4 px-4 py-5">
          {messages.isLoading ? (
            <div className="space-y-4">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 w-2/3" />)}</div>
          ) : displayed.length === 0 ? (
            <div className="grid h-full place-items-center">
              <div className="max-w-md text-center">
                <span className="mx-auto mb-4 grid h-16 w-16 place-items-center rounded-2xl bg-gradient-to-br from-brand/20 to-magenta/15 ring-1 ring-brand/25"><IconWand size={28} className="text-brand-soft" /></span>
                <h3 className="font-display text-lg font-semibold text-ink">想创作点什么？</h3>
                <p className="mt-1.5 text-sm text-muted">直接对话，或说「画…」让我生成图像。所有结果只在本机显示。</p>
                <div className="mt-5 flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} onClick={() => send(s)} className="chip hover:border-brand/40 hover:text-brand-soft">{s}</button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            displayed.map((m) => <Bubble key={m.id} m={m} onOpenTask={setDetail} />)
          )}
        </div>

        {/* 输入区 */}
        <div className="border-t border-line-soft p-3">
          <div className="flex items-end gap-2 rounded-xl border border-line bg-base/50 p-2 focus-within:border-brand/50">
            <textarea
              ref={taRef} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKeyDown}
              rows={1} placeholder="输入消息，Enter 发送 · Shift+Enter 换行…"
              className="scroll-y max-h-32 min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-sm text-ink outline-none placeholder:text-faint"
              aria-label="消息输入"
            />
            {streaming ? (
              <Button variant="danger" size="icon" onClick={stop} aria-label="停止生成"><IconStop size={17} /></Button>
            ) : (
              <Button variant="primary" size="icon" onClick={() => send()} disabled={!input.trim()} aria-label="发送"><IconSend size={17} /></Button>
            )}
          </div>
          <div className="mt-1.5 flex items-center gap-2 px-1 text-[10px] text-faint">
            <span className="flex items-center gap-1"><IconImage size={11} /> 说「画…」生成图像</span>
            <span className="flex items-center gap-1"><IconMic size={11} /> 结果自动收录作品库</span>
            {streaming && <Badge tone="brand" className="ml-auto" dot>流式输出中</Badge>}
          </div>
        </div>
      </Card>

      <TaskDetail taskId={detail} onClose={() => setDetail(null)} />
    </div>
  );
}