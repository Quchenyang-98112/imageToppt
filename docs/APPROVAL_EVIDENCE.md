# 最终发布证据

候选 PPTX 不是交付物。候选编译并经过 PowerPoint 渲染后，由复核器为每页提交：

```json
{
  "pages": [
    {
      "pageId": "page-001",
      "regionReviewPassed": true,
      "assetProvenanceAccepted": true,
      "editabilityManifest": {
        "sourceImageHash": "服务端 job.json 中该页的 sha256",
        "visiblePictures": [],
        "expectedOcrObjects": 40,
        "visibleOcrObjects": 40,
        "moveTests": [{"elementId": "page-001-object-1", "originDifferenceFromBgClean": 0}],
        "deleteTests": [{"elementId": "page-001-object-1", "passed": true}],
        "foregroundOnlyRenderPassed": true,
        "stableObjectNamesPassed": true,
        "componentGroupingPassed": true
      }
    }
  ]
}
```

这些值必须来自实际复核，不能为了发布而填充 `true`。服务端会重新运行
`tools/qa/audit_editability_manifest.py`，核对源图 hash 和 OCR 对象数，并检查
Qwen-VL 复核、BG_CLEAN 审计、区域拆解及素材 provenance。任一页失败时任务仍为
`needs_review`，但仍可以下载候选 PPTX；只有通过证据门禁后才会生成正式发布稿。
