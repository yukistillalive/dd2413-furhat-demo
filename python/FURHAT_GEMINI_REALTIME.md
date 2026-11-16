# Furhat 本地 Python + Gemini：Realtime WebSocket 速查

面向场景（本 lab 实际用法）：

- **不**编写、不上传 Kotlin Skill；**不**使用 Furhat Skill SDK（`furhat.say` 等）。
- 在**你自己的电脑**上直接运行 Python 脚本（如 `python my_gemini_bridge.py`）。
- 用 **Gemini API Key**（环境变量或 `.env`）连接 Google 模型（文本 / Live 语音 / 多模态）。
- 用 **WebSocket** 连接 Furhat **Realtime API**（`ws://<机器人IP>:9000/v1/events`），收发 `request.*` / `response.*`。
- **本机不承担拾音/摄像**：不使用电脑麦克风、摄像头、USB 传感器；用户对着 **Furhat 说话**，视觉与音频 I/O **一律经机器人** 进入 Python，再转发云端 Gemini。

本文汇总 Furhat 侧 JSON、`furhat_realtime_api` 封装，以及「PC = 网桥 + 业务逻辑」时的分阶段清单。

> 官方事件全集：[Realtime API Events](https://docs.furhat.io/realtime-api/events)  
> 连接与鉴权：[Realtime API 介绍](https://docs.furhat.io/realtime-api/intro)  
> 本仓库参考实现：`openai_realtime.py`、`openai_realtime_vision.py`（把 OpenAI 换成 Gemini 即可）

---

## 1. 整体架构（双连接）

```
  用户（在场）──说话/被拍摄──► Furhat 本体传感器
                                    │
                    ┌───────────────┴────────────────┐
                    │ 机器人 Realtime :9000           │
                    │ 麦 → response.audio.data        │
                    │ 相机 → response.camera.data     │
                    │ 执行 ← request.speak.* / face.* │
                    └───────────────┬────────────────┘
                                    │ WebSocket (LAN)
┌───────────────────────────────────▼───────────────────────────────────┐
│  本地 PC（无本机麦/摄像头参与）                                           │
│  Python：协议桥 + 轮次状态机 +（可选）对 JPEG 的离线图像处理                │
│     ├─► 云端 Gemini（API Key，HTTPS / Live WS）  文本·音频·多模态         │
│     └─► 仅 Furhat WebSocket，不调用 getUserMedia / PyAudio 采本机麦        │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.1 本机职责边界

| 本机 **做** | 本机 **不做** |
|-------------|----------------|
| 运行 Python，维护 Furhat WS 与 Gemini 连接 | `navigator.mediaDevices`、本机麦克风/摄像头 |
| 转发 **机器人** PCM JPEG（Base64）↔ 云端 | 用 PyAudio/SoundDevice 从 PC 采集环境声 |
| 转发/汇总 Gemini 回复为 `request.speak.*` 等 | 让 PC 扬声器代替 Furhat 出声（除非调试） |
| 对话状态、提示词、工具调用、可选 CV **处理 Furhat 传来的图** | 读取 `telepresence/` 式「用浏览器麦驱动机器人说话」 |
| `.env` 中的 `GEMINI_API_KEY` | 在 PC 上挂额外物理传感器（本期不考虑） |

| 组件 | 谁负责 |
|------|--------|
| 云端大模型（Gemini） | **Google API** |
| 桥接与编排 | **本地 Python**（中间不经过本机声学/光学硬件） |
| 拾音、播音、口型、相机、注视、脸型、手势、LED | **Furhat**（Python 只发 WS 指令） |

**数据路径（语音，与 `openai_realtime.py` 同构）**

1. 用户对 **Furhat** 说话 → `request.audio.start` → `response.audio.data.microphone`（**机器人麦**，非 PC 麦）。
2. Python 将 PCM 送入 Gemini Live（采样率/编码在 PC 上 **转码**，不 **采集**）。
3. Gemini 输出音频 → `request.speak.audio.start/data/end` → **Furhat 扬声器 + 口型**。
4. `response.speak.end` 后再 `request.audio.start`，继续听机器人侧环境。

**Gemini 凭证（本地）**：与仓库里 OpenAI 示例类似，可用 `.env`：

```
GEMINI_API_KEY=your_key_here
```

**与机器人上 Idle Skill 的关系**：Realtime 启用后，由你的 Python 发 `request.*` 驱动机器人。若机器人仍挂着自带对话 Skill，可能与 Realtime **抢** speak/listen——在 Web 界面停掉前台 Skill 或确认仅 Realtime 控机即可（**无需**你会 Kotlin）。

**Gemini 实时对话推荐形态**（对照 `openai_realtime.py`，协议换成 Gemini Live）：

1. `AsyncFurhatClient` + 事件回调（勿用同步 `FurhatClient` 做双向 PCM）。
2. **上行**：机器人麦 → `response.audio.data.microphone` → 转送 Gemini（字段名虽叫 `microphone`，指 **Furhat 通道**，不是 PC 设备）。
3. **下行**：Gemini PCM → `request.speak.audio.*`（`lipsync: true`）→ Furhat 发声。
4. **轮次**：机器人说话前 `request.audio.stop`；`response.speak.end` 后再开机器人麦。
5. **视觉（二期）**：仅 `response.camera.data.image`（**机器人相机**）进 Gemini；若做 CV，在 Python 里处理该 JPEG，不打开本机摄像头。

---

## 2. 连接约定

| 项 | 值 |
|----|-----|
| WebSocket URL | `ws://<ROBOT_IP>:9000/v1/events` |
| 消息格式 | JSON，必含 `"type": "request.*"` 或 `"response.*"` |
| 可选关联 ID | `"request_id": "uuid"`，对应响应带相同 `request_id` |
| 鉴权（若启用） | 连接后首条：`{"type":"request.auth","key":"<AUTH_KEY>"}` → `response.auth` |

**Furhat 侧：库 or 原生 WebSocket**

推荐（与本 lab 一致）：

```bash
pip install furhat_realtime_api
```

```python
from furhat_realtime_api import AsyncFurhatClient, Events

furhat = AsyncFurhatClient("192.168.x.x", auth_key=None)  # 局域网常无 key；跨网见官方鉴权
await furhat.connect()
furhat.add_handler(Events.response_audio_data, on_audio)
```

也可不用官方库，自行 `websockets` 连 `ws://<IP>:9000/v1/events`，发送下文 JSON 即可。仓库 `telepresence/` 使用**浏览器本机麦**推机器人，**不符合**本 lab 边界，仅作 WS 报文格式参考。

**Gemini 侧**：另起连接（`google-genai` SDK、Live WebSocket 等），与 Furhat WS **并行**；采样率、分片格式在 Python 里做转换，Furhat 事件类型不变。

---

## 3. WebSocket `request.*` 格式与 Python 封装对照

下列 JSON 是 Furhat WebSocket 上实际发送的载荷；`furhat_realtime_api` 只是把同样字段包成 `request_*` 方法。**Gemini 实时桥接请用 `AsyncFurhatClient`**；同步 `FurhatClient` 仅适合 `hello_world` 类顺序脚本。

### 3.1 说话（Speak）

**文本 TTS**

```json
{
  "type": "request.speak.text",
  "text": "你好，我是 Furhat。",
  "abort": false,
  "monitor_words": false
}
```

| Python | 说明 |
|--------|------|
| `request_speak_text(text, abort=..., wait=...)` | 同步客户端默认 `wait=True, abort=True` |
| `request_speak_stop()` | 打断当前/排队语音 |

**URL 音频（WAV）**

```json
{
  "type": "request.speak.audio",
  "url": "https://example.com/speech.wav",
  "text": "AUDIO",
  "lipsync": true,
  "abort": false
}
```

| Python | `request_speak_audio(url, text=..., lipsync=..., wait=...)` |

**流式 PCM（Gemini / 外部 TTS 常用）**

```json
{"type": "request.speak.audio.start", "sample_rate": 24000, "lipsync": true}
{"type": "request.speak.audio.data", "audio": "<Base64, 16-bit mono LE>"}
{"type": "request.speak.audio.end"}
```

| Python | `request_speak_audio_start` / `_data` / `_end` |

**相关响应**：`response.speak.start`、`response.speak.end`（含 `aborted`、`failed`）、可选 `response.speak.word`。

---

### 3.2 聆听（Listen，机器人侧 ASR）

**配置语言/偏置词**

```json
{
  "type": "request.listen.config",
  "languages": ["en-US", "zh-CN"],
  "phrases": ["Furhat", "Gemini"]
}
```

| Python | `request_listen_config(languages, phrases)` |

**开始/停止识别**

```json
{
  "type": "request.listen.start",
  "partial": false,
  "concat": true,
  "stop_no_speech": true,
  "stop_robot_start": true,
  "stop_user_end": true,
  "resume_robot_end": false,
  "no_speech_timeout": 8.0,
  "end_speech_timeout": 1.0
}
```

```json
{"type": "request.listen.stop"}
```

| Python | Async：`request_listen_start(...)`、`request_listen_stop()`；Sync：`request_listen_start()` **阻塞至** `response.hear.end`，返回 `text` 字符串 |

**相关响应**：`response.listen.start/end`、`response.hear.start/end`、`response.hear.partial`（`partial: true` 时）。

> **与 Gemini 实时音频并存时**：优先用 **3.8 原始麦克风流**，勿与 `listen.start` 混用且不协调状态。

---

### 3.3 音色（Voice）

**查询**

```json
{
  "type": "request.voice.status",
  "voice_id": true,
  "voice_list": true
}
```

**设置（多字段组合匹配）**

```json
{
  "type": "request.voice.config",
  "name": "neural",
  "gender": "male",
  "language": "en-US",
  "input_language": true
}
```

| Python | `request_voice_status()`、`request_voice_config(voice_id=..., name=..., ...)` |

**响应**：`response.voice.status` → `voice_id`、`voice_list`。

---

### 3.4 注意力 / 头姿（Attention & Head）

**注视用户**

```json
{
  "type": "request.attend.user",
  "user_id": "closest",
  "slack_pitch": 15,
  "slack_yaw": 5,
  "slack_timeout": 3000,
  "speed": "medium"
}
```

**注视空间点（米，相对机器人）**

```json
{
  "type": "request.attend.location",
  "x": 0.2,
  "y": 1.5,
  "z": 2.0,
  "speed": "medium"
}
```

**取消注视**（文档有，Python 客户端需自发自定义 JSON）

```json
{"type": "request.attend.nobody"}
```

| Python | `request_attend_user(user_id="closest")`、`request_attend_location(x,y,z)` |

**手动头姿（覆盖自动 gaze）**

```json
{
  "type": "request.face.headpose",
  "yaw": 10,
  "pitch": -5,
  "roll": 0,
  "relative": false,
  "speed": "medium"
}
```

| Python | `request_face_headpose(yaw, pitch, roll, relative)` |

**响应**：`response.attend.status`（`target`、`current`）。

---

### 3.5 面部外观与表情（Face）

**切换面具/角色（仅已安装 `face_id`）**

```json
{
  "type": "request.face.config",
  "face_id": "some_character",
  "visibility": true,
  "microexpressions": true,
  "blinking": true,
  "head_sway": false
}
```

**连续表情参数（blendshape 等）**

```json
{
  "type": "request.face.params",
  "params": {
    "smile": 0.8,
    "brow_raise": 0.3
  }
}
```

```json
{"type": "request.face.reset"}
```

**查询脸型列表**

```json
{
  "type": "request.face.status",
  "face_id": true,
  "face_list": true
}
```

| Python | `request_face_config`、`request_face_params`、`request_face_reset`、`request_face_status` |

**响应**：`response.face.status`。

> 遥操作脸驱宜 **节流 ~20–50 Hz**（本仓库 `telepresence/config.js` 对 `face.params` 约 20ms）。

---

### 3.6 手势（Gesture）

```json
{
  "type": "request.gesture.start",
  "name": "smile",
  "intensity": 1.0,
  "duration": 1.0,
  "monitor": false
}
```

| Python | `request_gesture_start(name, intensity, duration, wait=...)` |

**响应**：`response.gesture.start`、`response.gesture.end`（`monitor: true` 时）。

手势 `name` 以机器人/文档枚举为准，非任意骨骼动画。

---

### 3.7 用户检测（Users）

```json
{"type": "request.users.once"}
```

```json
{"type": "request.users.start"}
```

```json
{"type": "request.users.stop"}
```

| Python | `request_users_once()`（Sync/Async）；连续监测仅 Async：`request_users_start/stop` |

**响应**：`response.users.data` → `users[]`（常按距离排序，含 `camera` 等字段供点击 attend）。

---

### 3.8 麦克风 / 扬声器原始流（Audio I/O）

```json
{
  "type": "request.audio.start",
  "sample_rate": 16000,
  "microphone": true,
  "speaker": false
}
```

```json
{"type": "request.audio.stop"}
```

**响应**：`response.audio.data`

```json
{
  "type": "response.audio.data",
  "microphone": "<Base64 PCM>",
  "speaker": "<Base64 PCM，可选>"
}
```

| Python | `request_audio_start(sample_rate, microphone, speaker)`、`request_audio_stop()` + handler |

> **本 lab**：`microphone` 为 **Furhat 机载麦** 的 PCM 流；Python 只做转发/转码到 Gemini，**不从 PC 采集音频**。播音同理：Gemini → `speak.audio.*` → Furhat 喇叭。

**Gemini**：将上述 `microphone` 块按 Live API 要求编码；下行用 3.1 流式 speak。

---

### 3.9 相机（Camera，图像处理入口）

```json
{"type": "request.camera.start"}
```

```json
{"type": "request.camera.once"}
```

```json
{"type": "request.camera.stop"}
```

**响应**：`response.camera.data`

```json
{
  "type": "response.camera.data",
  "image": "<Base64 JPEG>"
}
```

| Python | Async：`request_camera_start/stop` + handler；Sync：仅 `request_camera_once()` 单帧 |

**Gemini 二期**：用 **机器人相机** 帧（非 PC 摄像头）；在开口或定时将 `image` 送 Gemini（见 `openai_realtime_vision.py`）。

---

### 3.10 LED

```json
{"type": "request.led.set", "color": "#ff5500"}
```

**单灯珠**（官方文档支持；`furhat_realtime_api` 0.1.3 未封装，需原生 JSON）

```json
{
  "type": "request.led.set.individual",
  "leds": {"0": "#ff0000", "1": "#00ff00"}
}
```

| Python | `request_led_set(color)` |

---

## 4. Python `furhat_realtime_api` 方法总表

| 类别 | AsyncFurhatClient | FurhatClient（同步） | 备注 |
|------|-------------------|----------------------|------|
| 连接 | `connect`, `disconnect`, `add_handler`, `set_logging_level` | 同左（阻塞包装） | 流式必须用 Async |
| Speak | `request_speak_text/audio/*`, `request_speak_stop` | 有；`speak_text` 默认 wait | |
| Listen | `request_listen_*` | `listen_start` 返回文本 | |
| Voice | `request_voice_*` | 有 | |
| Attend | `request_attend_user/location` | 有 | |
| Face | `request_face_*` | 有 | |
| Gesture | `request_gesture_start` | 有 | |
| Users | `once/start/stop` | 仅 `once` | |
| Audio | `request_audio_start/stop` | **无** | Gemini 用 Async |
| Camera | `start/stop/once` | 仅 `once` | 视频流用 Async |
| LED | `request_led_set` | 有 | |

事件类型常量见包内 `Events`（与 `type` 字符串一致）。

---

## 5. 本 lab 不使用的路径（仅供区分）

| 方式 | 本 lab |
|------|--------|
| Kotlin Skill + `furhat.say` / `listen` / 状态机 | **不用** |
| 在机器人上打包运行对话逻辑 | **不用**；逻辑全在**本地 Python** |
| PC 麦克风/摄像头参与对话 | **不用**；传感与播音在 **Furhat** |
| `telepresence/`（浏览器麦、`SendAudioManager`） | **不用** |
| PC 跑 Python + Gemini API Key + 仅 Furhat WebSocket | **正在使用** |

文档其余章节出现的 `request.speak.text` 等，即你在 Python 里（经库或裸 WS）要发的 Furhat 事件，**与 Kotlin API 无一一对应需求**。

---

## 6. Gemini 实时对话：分阶段集成清单

### 阶段 A — 纯语音（最小可用）

- [ ] Furhat：`connect` → `request_attend_user()` →（可选）`request_voice_config`
- [ ] 循环：`request_audio.start` ↔ Gemini 会话
- [ ] 模型输出：`speak.audio.start/data/end`，`lipsync: true`
- [ ] 状态：`response.speak.end` 后再开麦；打断时 `request_speak_stop` + `request_audio_stop`

### 阶段 B — 视觉上下文

- [ ] `request_camera.start`，handler 更新 `latest_jpeg_b64`
- [ ] 在用户开始说话或每 N 秒向 Gemini 附一张图（注意 token/带宽）
- [ ] 可选：`request_users_once` + 按 `user_id` attend

### 阶段 C — 表现力与临场

- [ ] `request_gesture_start` 与情感联动（由 Gemini 工具调用或规则触发）
- [ ] `request_face.params` 做轻量表情（节流）
- [ ] `request_led_set` 表状态（思考中/倾听中）
- [ ] 可选：在 PC 上对 **Furhat 发来的 JPEG** 做 OpenCV 等处理，再把结果图或特征送 Gemini（仍不启用本机摄像头）

### 阶段 D — 非实时能力（可选）

- 若只需**文本轮次**而非 Live PCM：可用 `request_listen_start` + Gemini `generateContent`，下行 `request_speak_text`（实现简单、延迟较高）。

---

## 7. 常见问题与边界

| 问题 | 建议 |
|------|------|
| 机器人前台仍在跑自带 Skill | 在 Furhat 界面停掉或保证 **仅 Realtime** 在 speak/listen（与本 lab「无 Skill」一致） |
| `attend` 与 `face.headpose` 同时用 | 默认用 `request.attend.user` 即可；仅在做手动头控时 `attend.nobody` |
| 误用 PC 麦调试 | 开发时若要纯测 Gemini，可临时 PC 麦；**部署形态**应只走 `request.audio.*` |
| 外观「动态写入」 | Runtime 可改 `face_id`（预装）、`face.params`；**不能**经 WS 上传全新 3D 脸型 |
| 公网连接 | 必须 `request.auth`；PCM/JPEG 带宽敏感 |
| 未封装事件 | `request.attend.nobody`、`request.led.set.individual` → `async_client.send_event({...})` |

---

## 8. 本仓库文件索引

| 文件 | 用途 |
|------|------|
| `hello_world.py` | 同步：音色、脸型列表、TTS、用户快照、LED |
| `openai_simple.py` | 同步：`speak_text` + `listen_start` 文本环 |
| `openai_async.py` | 异步：事件驱动听说法 |
| `openai_realtime.py` | **PCM 双向桥**（Gemini 首选参考） |
| `openai_realtime_vision.py` | 机器人 PCM + **机器人相机 JPEG** 入模（**推荐**视觉参考） |
| `telepresence/` | 含 **PC 麦** 推流；与本 lab 边界不符，可参考 WS 事件名 |

---

## 9. 最小 Gemini 桥接伪代码（结构示意）

```python
async def on_furhat_audio(data):
  # data["microphone"]：Furhat WebSocket 上的机器人麦 PCM，不是 PC 麦克风
  if user_turn:
    await gemini_session.send_audio(data["microphone"])

async def on_gemini_audio_chunk(pcm_b64):
  if not speaking:
    await furhat.request_speak_audio_start(sample_rate=24000, lipsync=True)
    speaking = True
  await furhat.request_speak_audio_data(pcm_b64)

async def on_furhat_speak_end(_):
  global user_turn, speaking
  speaking = False
  user_turn = True
  await furhat.request_audio_start(sample_rate=24000, microphone=True, speaker=False)

async def on_camera(data):
  global latest_image
  latest_image = data.get("image")
```

**本地入口脚本示意**（无 Skill 打包）：

```bash
cd python
# .env 中 GEMINI_API_KEY=...
python gemini_realtime.py --host=192.168.x.x   # 你自写的桥，结构克隆 openai_realtime.py
```

具体 Gemini 字段以 [Gemini API 文档](https://ai.google.dev/gemini-api/docs) 为准；**Furhat 侧始终只有 Realtime 的 `request.*` / `response.*`**，与用 OpenAI 还是 Gemini 无关。

---

*文档版本：PC = Python 网桥 + 云端 Gemini；传感/执行仅在 Furhat；不用本机麦/摄像头。与 `furhat-realtime-api` 0.1.3 及 `openai_realtime*.py` 对齐。*
