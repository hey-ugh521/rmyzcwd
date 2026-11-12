import type { ComponentType, SVGProps } from "react";
import {
  IconStudio, IconCreate, IconTasks, IconGallery, IconChat, IconSystem, IconSettings,
} from "../components/icons";

export interface NavItem {
  to: string;
  label: string;
  desc: string;
  icon: ComponentType<SVGProps<SVGSVGElement> & { size?: number }>;
  keywords: string;
  end?: boolean;
}

export const NAV: NavItem[] = [
  { to: "/", label: "工作台", desc: "总览与快捷创作", icon: IconStudio, keywords: "home studio dashboard 首页 总览 工作台", end: true },
  { to: "/create", label: "创作", desc: "图像与语音生成", icon: IconCreate, keywords: "create generate image voice 生成 图片 语音 创作 画" },
  { to: "/tasks", label: "任务", desc: "队列与执行状态", icon: IconTasks, keywords: "tasks queue 任务 队列 进度 取消" },
  { to: "/gallery", label: "作品", desc: "成果库与收藏", icon: IconGallery, keywords: "gallery works 作品 图片 收藏 库" },
  { to: "/chat", label: "对话", desc: "本机创作助手", icon: IconChat, keywords: "chat assistant 对话 聊天 助手 小莫 凛" },
  { to: "/system", label: "系统", desc: "服务健康与资源", icon: IconSystem, keywords: "system health services gpu 系统 服务 健康 资源 显存" },
  { to: "/settings", label: "设置", desc: "外观与连接", icon: IconSettings, keywords: "settings theme connection 设置 外观 主题 连接 偏好" },
];