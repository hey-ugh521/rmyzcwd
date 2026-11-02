# LangBot 图片送达补丁（Möbius 专用）

LangBot 容器内有两处本地补丁，**容器重建/升级后需要重新应用**。

## 补丁 1：关闭 QQ 流式输出

文件：`/app/src/langbot/pkg/pipeline/process/handlers/chat.py`

原因：QQ 适配器开启 token 级流式时，回复被拆成几十个 chunk 逐条发送，
图片 URL 桥接（按完整文本匹配 URL）无法命中，导致 QQ 只收到一段文本链接。

做法：把
```python
is_stream = await query.adapter.is_stream_output_supported()
```
替换为
```python
is_stream = False  # Möbius: QQ 不做 token 级流式，整条发送以便图片 URL 桥接
```

## 补丁 2：图片 URL → 图片消息（桥接函数）

文件：`/app/src/langbot/pkg/pipeline/respback/respback.py`

原因：网关返回的图片地址是内网地址（`host.docker.internal:8899`），
QQ 客户端解析不了；必须在 LangBot 侧把 URL 下载成 base64 图片段再发送。
原实现只按裸 URL 匹配，遇到 `**加粗**`/`[文字](链接)` 会带上多余字符导致下载失败。

新实现：
- 先匹配 Markdown 图片/链接 `![alt](url)`、`[text](url)`
- 再匹配裸 URL，并去掉尾部标点、`*`、`` ` ``、`_` 等
- 下载成功 → `platform_message.Image(base64=...)` 追加到消息链，并从文本中删掉 URL

## 应用方式（宿主）

```powershell
# 1) 取出文件
docker cp langbot:/app/src/langbot/pkg/pipeline/process/handlers/chat.py      .\logs\chat.py
docker cp langbot:/app/src/langbot/pkg/pipeline/respback/respback.py         .\logs\respback.py

# 2) 打补丁（幂等）
D:\AI\qqbot-mcp\Scripts\python.exe ops\patches\mobius_chat_streamoff_patch.py .\logs\chat.py
D:\AI\qqbot-mcp\Scripts\python.exe ops\patches\mobius_respback_patch.py      .\logs\respback.py

# 3) 回写并重启
docker cp .\logs\chat.py      langbot:/app/src/langbot/pkg/pipeline/process/handlers/chat.py
docker cp .\logs\respback.py  langbot:/app/src/langbot/pkg/pipeline/respback/respback.py
docker exec langbot python -c "import py_compile; py_compile.compile('/app/src/langbot/pkg/pipeline/process/handlers/chat.py', doraise=True); py_compile.compile('/app/src/langbot/pkg/pipeline/respback/respback.py', doraise=True)"
docker restart langbot
```

## 验证

- 发一条生图请求（如「画一只白鹭」）
- LangBot 日志应出现：`已把 1 张图片 URL 转为图片消息`
- QQ 端应收到**图片本身**，而不是一条 `http://host.docker.internal...` 文本

## 注意

- 两个 `.mobius.bak` 备份文件在容器内同目录
- 补丁只在容器文件系统，**重建容器后必须重跑**
- 端口收口（网关只绑 127.0.0.1）本身不是问题：LangBot 容器可通过
  `host.docker.internal:8899` 访问网关（已验证返回 200 image/png）
