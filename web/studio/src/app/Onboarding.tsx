import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useUI } from "../store/ui";
import { useConnection } from "../api/client";
import { Modal } from "../components/overlays";
import { Button, Badge } from "../components/ui";
import { IconLogo, IconImage, IconMic, IconChat, IconSystem, IconLock, IconArrowRight } from "../components/icons";

const FEATURES = [
  { icon: <IconImage size={18} />, tone: "text-magenta", title: "图像创作", desc: "多底模 + LoRA，参数实时可调" },
  { icon: <IconMic size={18} />, tone: "text-cyan", title: "语音合成", desc: "Kokoro / 克隆音色，本机播放" },
  { icon: <IconChat size={18} />, tone: "text-brand-soft", title: "创作助手", desc: "对话即创作，流式回复" },
  { icon: <IconSystem size={18} />, tone: "text-success", title: "系统健康", desc: "服务状态 / GPU 资源一目了然" },
];

export function Onboarding() {
  const done = useUI((s) => s.onboardingDone);
  const dismiss = useUI((s) => s.dismissOnboarding);
  const mode = useConnection((s) => s.mode);
  const navigate = useNavigate();
  const [step, setStep] = useState(0);

  if (done) return null;

  const finish = (to?: string) => { dismiss(); if (to) navigate(to); };

  return (
    <Modal
      open onClose={() => finish()} size="lg"
      icon={<span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-brand/25 to-magenta/20 ring-1 ring-brand/30"><IconLogo size={24} className="text-brand-soft" /></span>}
      title="欢迎使用 Möbius Studio"
      subtitle="你的本机 AI 创作工作台 · 数据不出设备"
      footer={
        <div className="flex w-full items-center gap-2">
          <button onClick={() => finish()} className="text-xs text-faint hover:text-muted">不再显示</button>
          <div className="ml-auto flex items-center gap-2">
            {step === 0 ? (
              <Button variant="primary" onClick={() => setStep(1)} icon={<IconArrowRight size={16} />}>下一步</Button>
            ) : (
              <>
                <Button variant="ghost" onClick={() => setStep(0)}>上一步</Button>
                <Button variant="primary" onClick={() => finish("/create")} icon={<IconArrowRight size={16} />}>开始创作</Button>
              </>
            )}
          </div>
        </div>
      }
    >
      {step === 0 ? (
        <div className="space-y-4">
          <p className="leading-relaxed text-muted">
            Möbius 运行在你的 Windows 本机：Core 负责会话与任务编排，Media Gateway 桥接 ComfyUI 图像引擎与语音合成。
            Studio 是面向创作的主界面，所有图片与音频只在本机生成与显示。
          </p>
          <div className="grid grid-cols-2 gap-2.5">
            {FEATURES.map((f) => (
              <div key={f.title} className="card flex items-start gap-3 p-3">
                <span className={`mt-0.5 flex-none ${f.tone}`}>{f.icon}</span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink">{f.title}</p>
                  <p className="mt-0.5 text-[11px] leading-snug text-muted">{f.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-2 rounded-lg border border-line-soft bg-raised/50 p-3">
            <IconLock size={18} className="flex-none text-success" />
            <p className="text-xs leading-relaxed text-muted">
              无账号、无云同步、无遥测。不读取 secrets.json 或用户文件系统；媒体不会上传到任何外部服务。
            </p>
          </div>
          <div className="rounded-lg border border-line-soft bg-raised/50 p-3">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-ink">当前数据源</p>
              <Badge tone={mode === "mock" ? "warning" : "success"} dot>{mode === "mock" ? "演示数据" : "本机服务"}</Badge>
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-muted">
              {mode === "mock"
                ? "未检测到本机 Core，已启用演示数据：任务会真实推进、预览图与音频由本机程序化生成，便于先熟悉界面。连接真实服务后，在顶栏或「设置」切换到本机即可。"
                : "已连接本机 Core / Gateway，所有操作将作用于真实任务队列。变更类操作（启停服务、取消任务、删除）均会二次确认。"}
            </p>
          </div>
          <p className="text-xs text-faint">提示：按 <span className="kbd">Ctrl/⌘ K</span> 随时打开命令面板快速跳转。</p>
        </div>
      )}
    </Modal>
  );
}