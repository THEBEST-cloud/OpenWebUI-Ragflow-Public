# OpenWebUI Pipeline for RAGFlow

这是一个用于 [Open WebUI](https://github.com/open-webui/open-webui) 的 Pipeline 插件，旨在将 [RAGFlow](https://github.com/infiniflow/ragflow) 的强大知识库检索与生成能力集成到 Open WebUI 的友好界面中。

本项目针对 RAGFlow 的知识库聊天助手进行了深度优化，提供了更稳定的流式响应处理和完善的日志记录。

## ✨ 特性与版本区别

本项目提供了两个版本的 Pipeline 文件，您可以根据需求选择使用：

1.  **`ragflow_pipeline.py` (标准版)**
    *   **适用场景**：纯文本对话，不需要展示文档引用来源。
    *   **特点**：输出简洁，专注于回答内容本身。

2.  **`ragflow_citation_pipeline.py` (引用版)**
    *   **适用场景**：需要展示回答依据，适合学术研究、文档问答等场景。
    *   **特点**：
        *   自动解析 RAGFlow 返回的引用数据。
        *   在回答末尾自动附加引用来源列表（包含文档名称等信息）。
        *   支持流式输出引用块，体验流畅。

## 🚀 快速开始

1.  确保您已经安装并运行了 Open WebUI 和 RAGFlow。
2.  将本仓库中的 `.py` 文件上传到 Open WebUI 的 Pipelines 目录（或通过 UI 上传）。
3.  在 Open WebUI 的 Pipeline 设置中，配置以下 **Valves** 参数：
    *   `RAGFLOW_API_BASE_URL`: 您的 RAGFlow API 地址 (例如 `http://192.168.1.100:9380/api/v1`)。
    *   `RAGFLOW_API_KEY`: 您的 RAGFlow API Key。

## 🙏 致谢

本项目参考并基于 [luyilong2015/open-webui-pipeline-for-ragflow](https://github.com/luyilong2015/open-webui-pipeline-for-ragflow) 开发。

**特别感谢原作者的开源贡献！**

与原版相比，本项目主要区别在于：
*   **针对性优化**：专门针对 RAGFlow 知识库聊天助手（Assistant）场景开发。
*   **引用支持**：新增了支持生成引用的版本 (`ragflow_citation_pipeline.py`)，可解析并展示 RAGFlow 的文档溯源信息。
*   **日志增强**：增加了详细的 `info` 级别日志，方便排查 API 连接和流式处理中的问题。
*   **脱敏处理**：代码结构经过调整，更易于安全配置敏感信息。

## 📄 License

MIT
