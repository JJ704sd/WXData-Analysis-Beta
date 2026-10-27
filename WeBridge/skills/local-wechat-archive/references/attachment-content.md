# 附件正文与状态

格式、字段、提取限制及缺口说明统一见 [附件正文解析说明](../../../docs/local-chat/附件正文解析说明.md)。本文件用于Skill按需定位说明；以程序的attachmentContent及固定汇报为准。


v47可在内容补齐设置选择原件目录、启用扫描PDF/图片OCR、按文件SHA256提供已知密码。优先按attachmentContent.fileStatus判断原件；extractionStatus及ocr/requiresReview表示正文范围，OCR不得当准确全文。password_rejected和integrity_error须保留原件并报告，禁止回显密码文件内容。补充目录只核查本机用户指定文件，未缓存且无副本的文件须先从客户端取得。全批180秒预算已取消；单文件时间和大小限制仍会明确报告。

群聊 v48 的 ocrEnabled 覆盖图片附件、混合PDF扫描内容及实际引用的 Office 内嵌图片；聊天原图仍由 ocrImages 控制。多页TIFF逐页识别，未完成页保留缺口。格式优先依据已校验文件签名；sections[].ocr 与分析材料 ocrSections 提供行、词框和识别位图像素坐标，embeddedImages 给出文档部分/工作表/幻灯片位置。不要把行位置当作可靠表格结构；混合PDF的原生与OCR文字不能重复计为业务事件。

## v49：原件补齐与预览正文

默认取消个微媒体兜底20000文件/2GiB配额；普通文件流式校验摘要并复用未变化结果。双端桌面导出不再因普通原件超过100MiB而拒绝归档，正文20MiB和解码图片容器100MiB上限分别保留。开启聊天图片OCR后，已取得的预览也可识别；sourceQuality=preview、fileStatus=preview_only、extractionStatus=partial、requiresReview明确标注，不能计为原图或全文。

完整性概览中点击“按文件补原件”，打开原件补齐优先清单.csv。按原件MD5合并重复引用、附件优先，保留群/消息定位；无摘要不按同名归并。统计是当前导出范围，多个群的逐群去重数不能直接当跨群唯一文件数。未缓存且无副本的原件仍需客户端取得；下载地址存在不保证可用。
