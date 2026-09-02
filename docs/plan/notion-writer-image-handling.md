# Notion Writer 图片链路说明

## 当前已支持

- 读取 Notion Page 时，图片 Block 会转换为 Writer IR 的 `image` Block；caption 转为 IR 文本，原始
  `image.file` 或 `image.external` payload 保存在 `provider_payload.raw_block` 中。
- 对刚读取的同一篇 Notion 文档执行全文覆盖时，如果图片没有 `media_asset` 引用，但仍保留有效的
  Notion 原生 `file` URL，`NotionFS` 会先下载图片，再通过 Notion File Upload API 上传，并使用新的
  `file_upload.id` 创建 image Block，不再把临时签名 URL 当作 `external` 图片复用。
- 对稳定、公开可访问的 `external` 图片 URL，Writer 会继续直接生成 `image.external` Block。
- 新增图片可以通过本地 `media_asset` 写入：Adapter 把本地文件信息作为私有元数据交给 `NotionFS`，
  后者完成单段或多段上传，并在发出 Block 请求前移除私有字段。
- 全文覆盖仍沿用 `NotionFS.replace_doc_blocks()` 的安全顺序：先创建并验证新 Block，再删除旧 Block；
  创建失败时会清理本次已创建的根 Block，原文档保持不变。

这条路径用于“Notion Page -> IR -> Block -> 覆盖原 Page”的稳定 roundtrip。Notion 托管图片会产生一次
下载和一次重新上传。

## 当前不支持

- 将 Notion 托管图片可靠地保存为长期可重放的 IR。Notion 返回的 `file.url` 是临时签名地址，过期后
  无法再下载；当前下载发生在写回阶段，并未在最初读取时持久化为 `MediaAsset`。
- 已经失效或此前已经变成空占位的图片无法自动恢复，因为其原始二进制和有效 URL 已经不存在。
- 跨 Workspace 上传是否成功受 OAuth 连接权限和目标 Workspace 文件限制约束。
- 对图片执行局部更新或移动；Notion Writer 的 `patch_to_operation()` 尚未实现。

新图片必须提供一个可用的本地 `media_asset`；缺少本地文件时 Writer 会在发请求前拒绝操作。

## 后续完整方案

如果需要让 IR 脱离 Notion 临时 URL 后仍可长期重放，后续还需要把下载时机前移到读取阶段，生成并持久化
`MediaAsset`，把 asset ID 回填到对应 IR 图片 Block。
