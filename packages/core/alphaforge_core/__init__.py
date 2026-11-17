"""AlphaForge 共享领域层。

只包含：枚举常量、纯函数指标算法、绩效指标、Pydantic DTO、标的元数据。
禁止依赖 api / engine / web —— 保证 core 可被任何进程独立复用与测试。
"""

__version__ = "0.6.0"
