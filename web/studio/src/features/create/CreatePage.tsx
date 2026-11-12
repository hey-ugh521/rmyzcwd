import { useEffect, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import type { Task } from "../../api/types";
import { Tabs } from "../../components/ui";
import { IconImage, IconMic } from "../../components/icons";
import { ImageCreate } from "./ImageCreate";
import { VoiceCreate } from "./VoiceCreate";

export function CreatePage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const prefill = (location.state as { prefill?: Task } | null)?.prefill ?? null;
  const initial = params.get("tab") === "voice" ? "voice" : "image";
  const [tab, setTab] = useState<"image" | "voice">(initial);

  useEffect(() => {
    const t = params.get("tab");
    if (t === "voice" || t === "image") setTab(t);
  }, [params]);

  // 预填来自任务详情「重新创作」：按其类型切换
  useEffect(() => {
    if (prefill) setTab(prefill.kind === "image" ? "image" : "voice");
  }, [prefill]);

  const change = (v: "image" | "voice") => { setTab(v); setParams({ tab: v }, { replace: true }); };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <Tabs
          value={tab} onChange={change}
          tabs={[
            { value: "image", label: <span className="flex items-center gap-1.5"><IconImage size={15} /> 图像</span> },
            { value: "voice", label: <span className="flex items-center gap-1.5"><IconMic size={15} /> 语音</span> },
          ]}
        />
        <p className="text-xs text-faint">
          {tab === "image" ? "调整参数后提交，结果实时显示在画布并自动收录到作品库。" : "输入文本，选择音色与语速，合成后本机试听。"}
        </p>
        {prefill && <span className="ml-auto rounded-full border border-brand/30 bg-brand/10 px-2.5 py-1 text-[11px] text-brand-soft">已预填任务参数</span>}
      </div>

      {tab === "image" ? <ImageCreate prefill={prefill} /> : <VoiceCreate prefill={prefill} />}
    </div>
  );
}