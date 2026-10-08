from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, validator
from typing import Optional, List
from functools import lru_cache


class Settings(BaseSettings):
    """应用配置"""

    # 应用基础配置
    PROJECT_NAME: str = "Growth Flywheel 2.5"
    VERSION: str = "2.5.2"  # 奖励系统 V2 升级
    ENVIRONMENT: str = Field(default="development", env="ENVIRONMENT")
    DEBUG: bool = Field(default=True, env="DEBUG")

    # 数据库配置
    DATABASE_URL: str = Field(default="postgresql://growth_user:password@localhost:5432/growth_flywheel", env="DATABASE_URL")
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10

    # Redis 配置
    REDIS_URL: str = Field(default="redis://localhost:6379/0", env="REDIS_URL")
    REDIS_CACHE_TTL: int = 3600  # 1小时

    # MinIO 配置
    MINIO_ENDPOINT: str = Field(default="localhost:9000", env="MINIO_ENDPOINT")
    MINIO_ACCESS_KEY: str = Field(..., env="MINIO_ACCESS_KEY")
    MINIO_SECRET_KEY: str = Field(..., env="MINIO_SECRET_KEY")
    MINIO_BUCKET: str = "growth-flywheel"
    MINIO_SECURE: bool = False

    # Qdrant 配置
    QDRANT_URL: str = Field(default="http://localhost:6333", env="QDRANT_URL")
    QDRANT_COLLECTION: str = "reference_pool"
    QDRANT_VECTOR_SIZE: int = 1536  # Match OpenAI text-embedding-3-small

    # JWT 配置 - 生产环境必须从环境变量读取
    JWT_SECRET: str = Field(
        default="local-dev-jwt-secret-key-please-change-in-production-2026",
        env="JWT_SECRET",
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24小时
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS 配置 - 从环境变量读取，逗号分隔
    CORS_ORIGINS: str = Field(default="http://localhost:3000,http://localhost:5173", env="CORS_ORIGINS")

    # LLM 配置
    ANTHROPIC_API_KEY: Optional[str] = Field(default=None, env="ANTHROPIC_API_KEY")
    OPENAI_API_KEY: Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    # DeepSeek Configuration
    DEEPSEEK_API_KEY: Optional[str] = Field(default=None, env="DEEPSEEK_API_KEY")
    DEEPSEEK_API_BASE: str = Field(default="https://api.deepseek.com", env="DEEPSEEK_API_BASE")
    DEEPSEEK_MODEL: str = Field(default="deepseek-chat", env="DEEPSEEK_MODEL")
    QWEN_API_KEY: Optional[str] = Field(default=None, env="QWEN_API_KEY")
    DOTS_API_BASE: str = Field(default="http://localhost:8000", env="DOTS_API_BASE")
    DOTS_API_KEY: Optional[str] = Field(default=None, env="DOTS_API_KEY")

    # Multimodal content production providers
    RUNWAYML_API_SECRET: Optional[str] = Field(default=None, env="RUNWAYML_API_SECRET")
    RUNWAYML_API_BASE: str = Field(
        default="https://api.dev.runwayml.com",
        env="RUNWAYML_API_BASE",
    )
    RUNWAYML_API_VERSION: str = Field(default="2024-11-06", env="RUNWAYML_API_VERSION")
    RUNWAYML_MODEL: str = Field(default="gen4.5", env="RUNWAYML_MODEL")
    RUNWAYML_RATIO: str = Field(default="720:1280", env="RUNWAYML_RATIO")

    ELEVENLABS_API_KEY: Optional[str] = Field(default=None, env="ELEVENLABS_API_KEY")
    ELEVENLABS_API_BASE: str = Field(
        default="https://api.elevenlabs.io",
        env="ELEVENLABS_API_BASE",
    )
    ELEVENLABS_VOICE_ID: Optional[str] = Field(default=None, env="ELEVENLABS_VOICE_ID")
    ELEVENLABS_MODEL_ID: str = Field(
        default="eleven_multilingual_v2",
        env="ELEVENLABS_MODEL_ID",
    )

    MULTIMODAL_ARTIFACT_DIR: str = Field(
        default="backend/runtime/multimodal",
        env="MULTIMODAL_ARTIFACT_DIR",
    )
    MULTIMODAL_DURABLE_STORAGE_ENABLED: bool = Field(
        default=False,
        env="MULTIMODAL_DURABLE_STORAGE_ENABLED",
    )
    MULTIMODAL_ARTIFACT_BUCKET: str = Field(
        default="videos",
        env="MULTIMODAL_ARTIFACT_BUCKET",
    )
    MULTIMODAL_LLM_PROVIDER: str = Field(
        default="claude",
        env="MULTIMODAL_LLM_PROVIDER",
    )
    MULTIMODAL_LLM_MODEL: Optional[str] = Field(
        default=None,
        env="MULTIMODAL_LLM_MODEL",
    )
    MULTIMODAL_JUDGE_ENABLED: bool = Field(
        default=False,
        env="MULTIMODAL_JUDGE_ENABLED",
    )
    MULTIMODAL_JUDGE_MODEL: str = Field(
        default="gpt-5.6-luna",
        env="MULTIMODAL_JUDGE_MODEL",
    )
    MULTIMODAL_JUDGE_SAMPLE_COUNT: int = Field(
        default=4,
        env="MULTIMODAL_JUDGE_SAMPLE_COUNT",
    )
    MULTIMODAL_SUBTITLES_ENABLED: bool = Field(
        default=True,
        env="MULTIMODAL_SUBTITLES_ENABLED",
    )
    MULTIMODAL_BRAND_TEXT: str = Field(
        default="",
        env="MULTIMODAL_BRAND_TEXT",
    )
    MULTIMODAL_BRAND_FONT: str = Field(
        default="DejaVu Sans",
        env="MULTIMODAL_BRAND_FONT",
    )
    MULTIMODAL_BRAND_FONT_SIZE: int = Field(
        default=28,
        env="MULTIMODAL_BRAND_FONT_SIZE",
    )
    FFMPEG_BIN: str = Field(default="ffmpeg", env="FFMPEG_BIN")
    FFPROBE_BIN: str = Field(default="ffprobe", env="FFPROBE_BIN")

    # Long-video repurposing (separate from generated-content production)
    GROQ_API_KEY: Optional[str] = Field(default=None, env="GROQ_API_KEY")
    GROQ_ASR_MODEL: str = Field(default="whisper-large-v3-turbo", env="GROQ_ASR_MODEL")
    GROQ_ASR_CONCURRENCY: int = Field(default=3, ge=1, le=6)
    VIDEO_REPURPOSING_LLM_CONCURRENCY: int = Field(default=2, ge=1, le=4)
    VIDEO_REPURPOSING_MAX_UPLOAD_BYTES: int = Field(
        default=3 * 1024**3, ge=1
    )

    # TikTok Content Posting / Display API
    TIKTOK_CONTENT_POSTING_ENABLED: bool = Field(
        default=False,
        env="TIKTOK_CONTENT_POSTING_ENABLED",
    )
    TIKTOK_ACCESS_TOKEN: Optional[str] = Field(
        default=None,
        env="TIKTOK_ACCESS_TOKEN",
    )
    TIKTOK_API_BASE: str = Field(
        default="https://open.tiktokapis.com",
        env="TIKTOK_API_BASE",
    )
    TIKTOK_PRIVACY_LEVEL: str = Field(
        default="SELF_ONLY",
        env="TIKTOK_PRIVACY_LEVEL",
    )

    # Cohere Rerank（替代 LLM 做重排序，成本降 100 倍）
    COHERE_API_KEY: Optional[str] = Field(default=None, env="COHERE_API_KEY")

    # 智能成本路由：按任务复杂度选择模型
    # cheap=DeepSeek, balanced=Haiku, premium=Sonnet
    COST_ROUTE_CHEAP_PROVIDER: str = "deepseek"
    COST_ROUTE_BALANCED_PROVIDER: str = "claude"
    COST_ROUTE_BALANCED_MODEL: str = "haiku-4.5"
    COST_ROUTE_PREMIUM_PROVIDER: str = "claude"
    COST_ROUTE_PREMIUM_MODEL: str = "sonnet-4.5"

    # 系统升级配置
    SYSTEM_UPGRADE_CONFIG_PATH: str = "backend/config/system_upgrade.yaml"
    ENABLE_HYBRID_REWARD: bool = False
    ENABLE_TREND_QUALITY_FILTER: bool = False
    ENABLE_DIVERSITY_EXPERIENCE_POOL: bool = False
    ENABLE_MODEL_ROUTER: bool = False
    ENABLE_GENERATION_TRACER: bool = False
    ENABLE_HIERARCHICAL_ACTION_SPACE: bool = False

    # LangSmith 配置
    LANGCHAIN_TRACING_V2: bool = True
    LANGCHAIN_API_KEY: Optional[str] = Field(default=None, env="LANGCHAIN_API_KEY")
    LANGCHAIN_PROJECT: str = "growth-flywheel-2.5"

    # GRPO 配置
    GRPO_GROUP_SIZE: int = 8
    GRPO_EPSILON: float = 0.1
    GRPO_LEARNING_RATE: float = 1e-4
    GRPO_ENTROPY_COEF: float = 0.01

    # 性能配置
    CACHE_L1_MAX_SIZE: int = 1000
    CACHE_L2_TTL_SECONDS: int = 3600
    PARALLEL_EXECUTOR_MAX_WORKERS: int = 10
    FAST_PATH_TIMEOUT_SECONDS: int = 2
    NORMAL_PATH_TIMEOUT_SECONDS: int = 10

    # Semantic Cache Settings
    SEMANTIC_CACHE_ENABLED: bool = True
    SEMANTIC_CACHE_SIMILARITY_THRESHOLD: float = 0.92
    SEMANTIC_CACHE_TTL_HOURS: int = 24
    SEMANTIC_CACHE_COLLECTION_NAME: str = "semantic_cache"

    MAX_WORKERS: int = 4
    REQUEST_TIMEOUT: int = 300  # 5分钟

    # 监控告警
    ALERT_SLACK_WEBHOOK_URL: Optional[str] = Field(default=None, env="ALERT_SLACK_WEBHOOK_URL")
    ALERT_PAGERDUTY_ROUTING_KEY: Optional[str] = Field(default=None, env="ALERT_PAGERDUTY_ROUTING_KEY")
    ALERT_MIN_LEVEL: str = Field(default="WARNING", env="ALERT_MIN_LEVEL")

    # Prompt 版本化
    PROMPT_AB_TEST_RATIO: float = Field(default=0.1, env="PROMPT_AB_TEST_RATIO")

    # XHS 爬虫配置（生产级）
    XHS_SCRAPER_MODE: str = Field(default="playwright", env="XHS_SCRAPER_MODE")  # creator_api | playwright
    XHS_CREATOR_API_BASE: Optional[str] = Field(default=None, env="XHS_CREATOR_API_BASE")
    XHS_CREATOR_API_TOKEN: Optional[str] = Field(default=None, env="XHS_CREATOR_API_TOKEN")
    XHS_SELECTORS_CONFIG_PATH: str = Field(
        default="backend/config/crawlers/xhs_selectors.json",
        env="XHS_SELECTORS_CONFIG_PATH",
    )
    XHS_STORAGE_STATE_DIR: str = Field(default="backend/runtime/xhs_sessions", env="XHS_STORAGE_STATE_DIR")
    XHS_SCRAPER_HEADLESS: bool = Field(default=True, env="XHS_SCRAPER_HEADLESS")
    XHS_SCRAPER_MAX_RETRIES: int = Field(default=3, env="XHS_SCRAPER_MAX_RETRIES")
    XHS_SCRAPER_TIMEOUT_MS: int = Field(default=20000, env="XHS_SCRAPER_TIMEOUT_MS")
    XHS_SCRAPER_SNAPSHOT_DIR: str = Field(
        default="backend/runtime/scraper_snapshots",
        env="XHS_SCRAPER_SNAPSHOT_DIR",
    )
    XHS_CRAWL_ENABLED: bool = Field(default=False, env="XHS_CRAWL_ENABLED")
    XHS_CRAWL_ACCEPT_POLICY: bool = Field(default=False, env="XHS_CRAWL_ACCEPT_POLICY")
    XHS_ALLOWED_DOMAINS: str = Field(
        default="www.xiaohongshu.com,xiaohongshu.com",
        env="XHS_ALLOWED_DOMAINS",
    )

    # 启动稳定性
    STARTUP_INIT_TIMEOUT_SEC: int = Field(default=30, env="STARTUP_INIT_TIMEOUT_SEC")
    STARTUP_OPTIONAL_TIMEOUT_SEC: int = Field(default=8, env="STARTUP_OPTIONAL_TIMEOUT_SEC")
    STARTUP_ALLOW_PARTIAL_ROUTERS: bool = Field(default=True, env="STARTUP_ALLOW_PARTIAL_ROUTERS")
    STARTUP_ROUTER_IMPORT_TIMEOUT_SEC: int = Field(default=6, env="STARTUP_ROUTER_IMPORT_TIMEOUT_SEC")
    STARTUP_DEFER_OPTIONAL_INIT: bool = Field(default=True, env="STARTUP_DEFER_OPTIONAL_INIT")
    STARTUP_ENABLE_SYSTEM_UPGRADE: bool = Field(default=False, env="STARTUP_ENABLE_SYSTEM_UPGRADE")
    STARTUP_ENABLE_ONLINE_LEARNING: bool = Field(default=False, env="STARTUP_ENABLE_ONLINE_LEARNING")
    STARTUP_ENABLE_MLOPS: bool = Field(default=False, env="STARTUP_ENABLE_MLOPS")
    HEALTHCHECK_TIMEOUT_SEC: int = Field(default=2, env="HEALTHCHECK_TIMEOUT_SEC")

    @validator('JWT_SECRET')
    def validate_jwt_secret(cls, v, values):
        """验证 JWT 密钥安全性"""
        if values.get('ENVIRONMENT') == 'production':
            if v == "your-super-secret-jwt-key-change-in-production":
                raise ValueError(
                    "JWT_SECRET must be changed in production! "
                    "Set a secure random key via environment variable."
                )
            if len(v) < 32:
                raise ValueError("JWT_SECRET must be at least 32 characters in production")
        return v

    @validator('CORS_ORIGINS')
    def validate_cors_origins(cls, v, values):
        """验证 CORS 配置"""
        if values.get('ENVIRONMENT') == 'production':
            if "*" in v:
                raise ValueError(
                    "CORS_ORIGINS cannot contain '*' in production! "
                    "Specify exact allowed origins."
                )
        return v

    def get_cors_origins_list(self) -> List[str]:
        """获取 CORS 来源列表"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    def get_xhs_allowed_domains(self) -> List[str]:
        """获取小红书爬虫域名白名单。"""
        return [d.strip().lower() for d in self.XHS_ALLOWED_DOMAINS.split(",") if d.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()
