# skill-merge：严格高保真图片转可编辑 PPTX

这是与源项目隔离的批处理版本。系统不再把“原图 + 新对象”叠加导出，而是固定采用：

`原图 → qwen3.5-ocr + qwen3-vl-plus → 语义区域/素材路由 → BG_CLEAN → 原生对象重建 → PowerPoint QA → PPTX`

干净底图只保留背景装饰（如丝带、光效、城市剪影）；文字、线条、卡片、框图和图标均由可编辑对象单独重建。因此把“2026”改为“2027”后，导出的 PPTX 不会保留旧的“2026”。

识别到但无法可靠矢量化的图标、徽标和复杂小素材会从原图按坐标切成小图片对象，而不是丢弃或改用字体符号；用户仍可在画布和 PowerPoint 中移动、缩放或删除这些切片。

## 本地运行

```bash
pnpm install
pnpm dev -- --port 3105
```

在 `.env.local` 配置 DashScope/Qwen 密钥。该文件按用户授权从源项目复制，但被 Git 忽略，不能提交或发送到浏览器。

用浏览器打开 `http://localhost:3105`，一次选择多张 PNG/JPG 原图即可建立批处理任务。任务目录默认为 `data/skill-merge/jobs/<job-id>`。

先运行 PowerPoint Worker 检查：

```powershell
powershell -ExecutionPolicy Bypass -File tools/verify_powerpoint_worker.ps1
```

PowerPoint 渲染脚本：

```powershell
powershell -ExecutionPolicy Bypass -File tools/powerpoint_render.ps1 -InputPptx <input.pptx> -OutputDir <render-dir>
```

当前严格任务在 OCR、结构和 BG_CLEAN 候选完成后会进入 `needs_review`。质量门禁会记录逐页分数和耗时，但不会阻止用户导出已经生成的候选 PPTX；只有补齐素材、渲染和移动/删除门禁后，才会把它标记为正式发布稿。

候选编译完成后，后端会在 `final/candidate-editable.pptx` 留下候选稿并调用本机 PowerPoint 渲染。最终发布必须由复核器向
`POST /api/jobs/<job-id>/approve` 提交每页 `editabilityManifest`、区域复核和素材 provenance 证据；服务端会重新运行
`tools/qa/audit_editability_manifest.py`，再检查 Qwen-VL 视觉评分与 BG_CLEAN 审计。缺证据或任一页失败时，任务仍保持
`needs_review`，但下载接口仍允许导出候选稿；候选稿和正式发布稿通过响应头 `x-skill-merge-artifact` 区分。

本机 Worker 约束：PowerPoint 必须安装在 Worker 机器上；`tools/verify_powerpoint_worker.ps1` 会检查安装路径和版本。
渲染脚本使用独立桌面 PowerPoint 进程、禁用宏、单任务串行处理和超时回收。部署到内网时，把同一应用运行在这台 Windows
Worker 上即可，浏览器和 API 客户端不需要安装 PowerPoint。

## 示例

内置的党建报告示例使用已经生成好的干净底图，并完整重建标题、摘要、六个模块、线条及页脚。它可直接编辑和导出，用于验证不重影的输出链路。

## 限制

上传的新图片必须先成功得到干净底图才能导出；若底图净化失败，应用会阻止导出，而不是退回到会产生重影的旧方案。
