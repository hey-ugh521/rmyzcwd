import type {
  ModelProfile, LoraInfo, VoiceInfo, WorkflowInfo, ProviderInfo, Character,
  ServiceInfo, MediaConfig, Task, ChatSession, ChatMessage,
} from "../types";

export const MODELS: ModelProfile[] = [
  { id: "qwen_image_2_1", name: "千问图像 2.1", workflow: "qwen_image", size: [1328, 1328], default: true, available: true, notes: "通用写实 / 中文语义强，默认底模", params: { steps: 20, cfg: 4.0 } },
  { id: "illustrious_xl", name: "Illustrious XL", workflow: "portrait", checkpoint: "Illustrious-XL-v1.0.safetensors", size: [832, 1216], available: true, notes: "二次元 / 角色一致性，支持 LoRA", params: { steps: 28, cfg: 5.5 } },
  { id: "flux3", name: "FLUX.3 dev", workflow: "flux", size: [1024, 1024], available: true, notes: "高质量但较慢（GGUF，约 60–120s）", params: { steps: 24, cfg: 3.5 } },
  { id: "sdxl_base", name: "SDXL Base 1.0", workflow: "txt2img", checkpoint: "sd_xl_base_1.0.safetensors", size: [1024, 1024], available: true, notes: "通用 SDXL 底模", params: { steps: 30, cfg: 7.0 } },
  { id: "wan_video", name: "Wan 2.2 视频", workflow: "wan_video", size: [832, 480], available: false, notes: "超出本机显存，暂不可用" },
];

export const LORAS: LoraInfo[] = [
  { id: "lora_rin", name: "rin_anime", filename: "rin_anime.safetensors", strength: 0.8, trigger: "rin, 1girl, silver hair" },
  { id: "lora_makima", name: "makima", filename: "makima.safetensors", strength: 0.75, trigger: "makima, red hair, suit" },
  { id: "lora_watercolor", name: "watercolor_style", filename: "watercolor.safetensors", strength: 0.6, trigger: "watercolor, painting" },
  { id: "lora_cyber", name: "cyberpunk_neon", filename: "cyber_neon.safetensors", strength: 0.7, trigger: "neon, cyberpunk city" },
  { id: "lora_ink", name: "ink_sketch", filename: "ink_sketch.safetensors", strength: 0.65, trigger: "monochrome, ink sketch" },
];

export const VOICES: VoiceInfo[] = [
  { id: "af_heart", name: "Heart · 女声", engine: "kokoro", lang: "en", gender: "female" },
  { id: "af_bella", name: "Bella · 女声", engine: "kokoro", lang: "en", gender: "female" },
  { id: "am_adam", name: "Adam · 男声", engine: "kokoro", lang: "en", gender: "male" },
  { id: "zf_xiaobei", name: "小北 · 中文女声", engine: "kokoro", lang: "zh", gender: "female" },
  { id: "zm_yunjian", name: "云健 · 中文男声", engine: "kokoro", lang: "zh", gender: "male" },
  { id: "gsv_rin", name: "凛 · 克隆音色", engine: "gpt-sovits", lang: "zh", gender: "female" },
  { id: "gsv_narrator", name: "旁白 · 克隆音色", engine: "gpt-sovits", lang: "zh", gender: "male" },
];

export const WORKFLOWS: WorkflowInfo[] = [
  { id: "portrait", name: "人像竖版 (Illustrious)", kind: "image" },
  { id: "comic-portrait", name: "漫画人像", kind: "image" },
  { id: "qwen_image", name: "千问图像", kind: "image" },
  { id: "flux", name: "FLUX 高质量", kind: "image" },
  { id: "txt2img", name: "通用文生图 (SDXL)", kind: "image" },
  { id: "img2img", name: "图生图", kind: "image", needs_reference: true },
];

export const PROVIDERS: ProviderInfo[] = [
  { id: "comfyui", name: "ComfyUI", kind: "image", capabilities: ["txt2img", "img2img", "lora", "controlnet"], available: true },
  { id: "kokoro", name: "Kokoro TTS", kind: "tts", capabilities: ["zh", "en", "cpu"], available: true },
  { id: "gpt-sovits", name: "GPT-SoVITS", kind: "voice", capabilities: ["clone", "gpu"], available: true },
];

export const CHARACTERS: Character[] = [
  { id: "mobius", name: "小莫", style_prompt: "", avatar: "", media_policy: { allow_image: true, allow_voice: true }, voice: { engine: "kokoro", voice: "zf_xiaobei", speed: 1.0 } },
  { id: "rin", name: "凛", style_prompt: "1girl, silver hair, red eyes, anime style, detailed", negative_prompt: "lowres, bad anatomy", workflow: "portrait", checkpoint: "Illustrious-XL-v1.0.safetensors", size: [832, 1216], loras: [{ name: "rin_anime", strength: 0.8 }], media_policy: { allow_image: true, allow_voice: true }, voice: { engine: "gpt-sovits", voice: "gsv_rin", speed: 1.0 } },
  { id: "narrator", name: "旁白", style_prompt: "", media_policy: { allow_image: false, allow_voice: true }, voice: { engine: "gpt-sovits", voice: "gsv_narrator", speed: 0.95 } },
];

export const SERVICES: ServiceInfo[] = [
  { name: "core", label: "Core 核心", state: "ready", enabled: true, autostart: true, port: 8900, url: "http://127.0.0.1:8900", detail: "会话 / 任务 / 记忆 / 编排" },
  { name: "gateway", label: "Media Gateway", state: "ready", enabled: true, autostart: true, port: 8899, url: "http://127.0.0.1:8899", detail: "图片 / TTS / ComfyUI 桥接" },
  { name: "comfyui", label: "ComfyUI", state: "ready", enabled: true, autostart: false, port: 8188, url: "http://127.0.0.1:8188", detail: "图像生成引擎", login: null },
  { name: "kokoro", label: "Kokoro TTS", state: "ready", enabled: true, autostart: false, port: 7870, detail: "CPU 语音合成" },
  { name: "gsv", label: "GPT-SoVITS", state: "down", enabled: true, autostart: false, port: 7871, detail: "音色克隆（GPU）" },
  { name: "postgres", label: "PostgreSQL", state: "ready", enabled: true, autostart: true, port: 55432, detail: "任务 / 记忆持久化" },
  { name: "langbot", label: "LangBot", state: "down", enabled: false, autostart: false, port: 5300, detail: "QQ 入口（可选）" },
  { name: "napcat", label: "NapCat", state: "disabled", enabled: false, autostart: false, detail: "QQ 协议端（可选）" },
];

export const MEDIA_CONFIG: MediaConfig = { mode: "on_demand", idle_sleep_sec: 600, prewarm_on_start: false };

export const RESOURCES = {
  gpu: { name: "NVIDIA GeForce RTX 4070", vram_total_mb: 12288, vram_used_mb: 5240, vram_free_mb: 7048, utilization: 38, temperature: 56 },
  queue: { running: 1, pending: 1 },
  leases: { gpu_heavy: { owner: "comfyui", held_sec: 42 }, cpu: { owner: "kokoro", held_sec: 3 } },
};

interface SeedSpec { prompt: string; kind: "image" | "speech"; status: string; model?: string; workflow?: string; character?: string; w?: number; h?: number; seed?: number; ageMin: number; durSec?: number; error?: string; source?: string; voice?: string }

const SEED_SPECS: SeedSpec[] = [
  { prompt: "银发红瞳少女，赛博朋克都市夜景，霓虹反光，电影感", kind: "image", status: "completed", model: "illustrious_xl", workflow: "portrait", character: "rin", w: 832, h: 1216, seed: 184523, ageMin: 6, durSec: 34, source: "web" },
  { prompt: "黄昏的海边灯塔，暖色天空，海浪，写实摄影", kind: "image", status: "completed", model: "qwen_image_2_1", workflow: "qwen_image", w: 1328, h: 1328, seed: 77120, ageMin: 22, durSec: 41, source: "web" },
  { prompt: "水墨风格的山水，留白，远山，孤舟", kind: "image", status: "completed", model: "sdxl_base", workflow: "txt2img", w: 1024, h: 1024, seed: 30918, ageMin: 48, durSec: 28, source: "console" },
  { prompt: " neon city street at night, rain reflections, cyberpunk, cinematic", kind: "image", status: "completed", model: "flux3", workflow: "flux", w: 1024, h: 1024, seed: 55231, ageMin: 75, durSec: 96, source: "qq" },
  { prompt: "春日樱花树下，花瓣飘落，少女背影， anime style", kind: "image", status: "completed", model: "illustrious_xl", workflow: "comic-portrait", w: 832, h: 1216, seed: 90218, ageMin: 130, durSec: 31, source: "web" },
  { prompt: "深海发光水母，幽蓝， volumetric light", kind: "image", status: "completed", model: "qwen_image_2_1", workflow: "qwen_image", w: 1328, h: 1328, seed: 41872, ageMin: 190, durSec: 39, source: "web" },
  { prompt: "森林晨雾，阳光穿过树梢，鹿，写实", kind: "image", status: "failed", model: "flux3", workflow: "flux", w: 1024, h: 1024, seed: 61190, ageMin: 35, error: "comfyui_unavailable", source: "web" },
  { prompt: "抽象几何海报，品红与青，包豪斯风格", kind: "image", status: "completed", model: "sdxl_base", workflow: "txt2img", w: 1024, h: 1024, seed: 12098, ageMin: 260, durSec: 26, source: "console" },
  { prompt: "雪山日出，金色山顶，云海，风光摄影", kind: "image", status: "cancelled", model: "qwen_image_2_1", workflow: "qwen_image", w: 1328, h: 1328, seed: 88123, ageMin: 15, source: "web" },
  { prompt: "复古胶片人像，暖调，浅景深", kind: "image", status: "submission_unknown", model: "illustrious_xl", workflow: "portrait", w: 832, h: 1216, seed: 33471, ageMin: 320, source: "qq" },
  { prompt: "机械心脏，蒸汽朋克，黄铜齿轮，微距", kind: "image", status: "running", model: "flux3", workflow: "flux", w: 1024, h: 1024, seed: 50231, ageMin: 0.4, source: "web" },
  { prompt: "雨后京都小巷，石板路，灯笼，黄昏", kind: "image", status: "pending", model: "qwen_image_2_1", workflow: "qwen_image", w: 1328, h: 1328, seed: 70912, ageMin: 0.1, source: "web" },
  { prompt: "欢迎使用 Möbius Studio，这是本机语音合成演示。", kind: "speech", status: "completed", voice: "zf_xiaobei", ageMin: 12, durSec: 3, source: "web" },
  { prompt: "今天天气不错，要不要出去走走？", kind: "speech", status: "completed", voice: "gsv_rin", character: "rin", ageMin: 95, durSec: 4, source: "qq" },
  { prompt: "系统已完成自检，所有服务运行正常。", kind: "speech", status: "failed", voice: "am_adam", ageMin: 200, error: "resource_busy", source: "console" },
];

export function buildSeedTasks(
  now: number,
  art: (opts: { seed: number; prompt: string; width: number; height: number }) => string,
  wav: (text: string, voice: string, speed?: number) => string
): Task[] {
  return SEED_SPECS.map((s, i) => {
    const created = now - s.ageMin * 60000;
    const started = s.status === "pending" ? null : created + 1500;
    const finished = ["completed", "failed", "cancelled"].includes(s.status)
      ? (started ?? created) + (s.durSec ?? 20) * 1000 : null;
    const w = s.w ?? 1024, h = s.h ?? 1024, seed = s.seed ?? 1000 + i;
    let result: Task["result"] = null;
    if (s.status === "completed") {
      result = s.kind === "image"
        ? { url: art({ seed, prompt: s.prompt, width: w, height: h }), filename: `mobius_${seed}.png`, width: w, height: h, seed }
        : { url: wav(s.prompt, s.voice ?? "zf_xiaobei"), filename: `tts_${seed}.wav`, seconds: s.durSec ?? 3 };
    }
    return {
      id: `t_seed_${String(i + 1).padStart(3, "0")}`,
      kind: s.kind, status: s.status, source: s.source ?? "web",
      user_id: "web:local", session_id: "session:web:local",
      character: s.character ?? null, prompt: s.prompt,
      params: s.kind === "image"
        ? { model: s.model, workflow: s.workflow, width: w, height: h, seed, steps: 28, cfg: 5.5 }
        : { voice: s.voice, engine: s.voice?.startsWith("gsv") ? "gpt-sovits" : "kokoro", speed: 1.0 },
      result, error: s.error ?? null, error_code: s.error ?? null,
      remote_id: s.status === "pending" ? null : `p_mock_${seed}`,
      created_at: created, started_at: started, finished_at: finished,
      cancel_requested: s.status === "cancelled", attempts: s.status === "failed" ? 1 : 1,
      version: 2, hint: s.status === "submission_unknown" ? "恢复时无法确认该任务是否执行过；为避免重复消耗 GPU，不会自动重新提交，请人工确认后取消或重新提交。" : undefined,
    };
  });
}

export const CHAT_SESSIONS: ChatSession[] = [
  { session_id: "session:web:local", title: "创作助手", user_id: "web:local", character: "mobius", updated_at: 0, message_count: 4, preview: "帮我画一张赛博朋克都市夜景" },
  { session_id: "session:web:rin", title: "与凛闲聊", user_id: "web:local", character: "rin", updated_at: 0, message_count: 2, preview: "今天过得怎么样？" },
];

export const CHAT_MESSAGES: Record<string, ChatMessage[]> = {
  "session:web:local": [
    { id: "m1", role: "user", text: "帮我画一张赛博朋克都市夜景，霓虹反光那种", created_at: 0 },
    { id: "m2", role: "assistant", text: "好的，我用 Illustrious 底模 + 凛的角色档案生成了一张竖版夜景。霓虹与雨面反光都加上了，你看看氛围对不对？需要更冷或更暖我可以再调。", created_at: 0, task_ids: ["t_seed_001"] },
    { id: "m3", role: "user", text: "不错，再来一张黄昏海边的", created_at: 0 },
    { id: "m4", role: "assistant", text: "已生成黄昏海边灯塔，暖色天空 + 海浪。这张用的是千问图像底模，写实感更强。", created_at: 0, task_ids: ["t_seed_002"] },
  ],
  "session:web:rin": [
    { id: "r1", role: "user", text: "今天过得怎么样？", created_at: 0 },
    { id: "r2", role: "assistant", text: "还不错哦～刚帮你渲染了几张图。你呢，今天有什么想创作的吗？", created_at: 0 },
  ],
};

export const STATE_HISTORY = [
  { ts: 0, service: "comfyui", from: "down", to: "ready", detail: "媒体唤醒成功" },
  { ts: 0, service: "gsv", from: "ready", to: "down", detail: "空闲休眠" },
  { ts: 0, service: "gateway", from: "starting", to: "ready", detail: "健康检查通过" },
  { ts: 0, service: "core", from: "starting", to: "ready", detail: "启动完成" },
  { ts: 0, service: "kokoro", from: "down", to: "ready", detail: "模型加载完成" },
];