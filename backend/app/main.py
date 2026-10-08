import asyncio
import importlib
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import init_db, close_db, check_db_ready
from app.core.redis import redis_client
from app.shared.middleware.rate_limiter import RateLimitMiddleware
from app.shared.middleware.csrf import CSRFMiddleware

settings = get_settings()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ROUTE_DEFINITIONS = [
    ("app.api.auth", True, {"prefix": "/api/v1/auth", "tags": ["auth"]}),
    ("app.api.v1.api_v4_rl", False, {}),
    ("app.api.v1.api_v4_rag", False, {}),
    ("app.api.v1.api_v5_langgraph", True, {}),
    ("app.api.v1.api_multimodal_production", False, {}),
    ("app.api.v1.api_video_repurposing", False, {}),
    ("app.api.v1.api_ml_training", False, {}),
    ("app.api.v1.api_ml_feedback", False, {}),
    ("app.api.v1.api_data_import", False, {}),
    ("app.api.v1.api_dashboard", False, {}),
    ("app.api.v1.api_outcomes", True, {}),
    ("app.api.v1.api_trend_radar", False, {}),
    ("app.api.v1.api_prompts", False, {}),
    ("app.shared.websocket.routes", False, {}),
]


async def _register_router(module_path: str, required: bool = False, **include_kwargs) -> bool:
    """在启动期注册路由，支持模块导入超时与降级。"""
    timeout_sec = settings.STARTUP_INIT_TIMEOUT_SEC if required else settings.STARTUP_ROUTER_IMPORT_TIMEOUT_SEC
    try:
        module = await asyncio.wait_for(
            asyncio.to_thread(importlib.import_module, module_path),
            timeout=timeout_sec,
        )
        app.include_router(module.router, **include_kwargs)
        app.state.startup_state["routers_loaded"].append(module_path)
        logger.info(f"✅ router loaded: {module_path}")
        return True
    except Exception as exc:
        if required or not settings.STARTUP_ALLOW_PARTIAL_ROUTERS:
            raise
        app.state.startup_state["routers_skipped"].append(module_path)
        logger.warning(
            f"⚠️  router skipped: {module_path}, "
            f"reason={type(exc).__name__}: {exc}"
        )
        return False


async def _register_all_routers():
    for module_path, required, include_kwargs in ROUTE_DEFINITIONS:
        await _register_router(module_path, required=required, **include_kwargs)
    logger.info("✅ V1 API 路由已注册（含热点雷达）")


async def _safe_optional_init(name: str, init_fn, timeout_sec: int):
    """可选组件初始化保护：超时/异常不阻塞主服务。"""
    try:
        await asyncio.wait_for(init_fn(), timeout=timeout_sec)
        logger.info(f"✅ {name} 初始化完成")
        return True
    except asyncio.TimeoutError:
        logger.warning(f"⚠️  {name} 初始化超时({timeout_sec}s)，已跳过")
        return False
    except Exception as exc:
        logger.warning(f"⚠️  {name} 初始化失败: {exc}")
        return False


def _start_background_optional_init(
    app: FastAPI,
    key: str,
    display_name: str,
    init_fn,
):
    async def _runner():
        app.state.startup_state["optional_inits"][key] = "running"
        ok = await _safe_optional_init(display_name, init_fn, settings.STARTUP_OPTIONAL_TIMEOUT_SEC)
        app.state.startup_state["optional_inits"][key] = bool(ok)

    task = asyncio.create_task(_runner())
    app.state.startup_tasks.append(task)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动
    logger.info("🚀 Growth Flywheel 2.5 启动中...")
    app.state.startup_state["routers_loaded"] = []
    app.state.startup_state["routers_skipped"] = []

    # 注册核心模型到 Base.metadata
    importlib.import_module("app.models")
    importlib.import_module("app.ml.training.schemas")
    importlib.import_module("app.prompting.models")
    importlib.import_module("app.engine.rag.graph_store")
    # 初始化数据库（硬依赖）
    await asyncio.wait_for(init_db(), timeout=settings.STARTUP_INIT_TIMEOUT_SEC)
    logger.info("✅ 数据库初始化完成")

    # 连接 Redis（硬依赖）
    await asyncio.wait_for(redis_client.connect(), timeout=settings.STARTUP_INIT_TIMEOUT_SEC)
    logger.info("✅ Redis 连接成功")
    app.state.startup_state["core_ready"] = True

    # 注册业务路由（硬依赖先、可选路由可降级）
    await _register_all_routers()

    # 初始化系统升级组件（可选）
    async def _init_system_upgrade():
        from app.core.system_upgrade import get_integration
        integration = get_integration()
        status = integration.get_status()

        enabled_features = [k for k, v in status['features'].items() if v]
        if enabled_features:
            logger.info(f"✅ 系统升级功能已启用: {', '.join(enabled_features)}")
        else:
            logger.info("ℹ️  系统升级功能未启用（可通过配置文件启用）")
    app.state.startup_state["optional_inits"]["system_upgrade"] = "disabled"
    app.state.startup_state["optional_inits"]["online_learning"] = "disabled"
    app.state.startup_state["optional_inits"]["mlops"] = "disabled"

    if settings.STARTUP_ENABLE_SYSTEM_UPGRADE:
        if settings.STARTUP_DEFER_OPTIONAL_INIT:
            _start_background_optional_init(app, "system_upgrade", "系统升级组件", _init_system_upgrade)
        else:
            app.state.startup_state["optional_inits"]["system_upgrade"] = await _safe_optional_init(
                "系统升级组件",
                _init_system_upgrade,
                settings.STARTUP_OPTIONAL_TIMEOUT_SEC,
            )

    # 启动在线学习循环（可选）
    async def _init_online_learning():
        from app.ml.rl.online_learning_loop import get_learning_loop
        _ = get_learning_loop()
        # 默认不自动启动，需要通过 API 手动启动
        logger.info("✅ 在线学习循环已初始化（需手动启动）")
    if settings.STARTUP_ENABLE_ONLINE_LEARNING:
        if settings.STARTUP_DEFER_OPTIONAL_INIT:
            _start_background_optional_init(app, "online_learning", "在线学习循环", _init_online_learning)
        else:
            app.state.startup_state["optional_inits"]["online_learning"] = await _safe_optional_init(
                "在线学习循环",
                _init_online_learning,
                settings.STARTUP_OPTIONAL_TIMEOUT_SEC,
            )

    # 初始化 MLOps 组件
    async def _init_mlops():
        from app.mlops import get_mlflow_tracker, get_cache_manager, get_performance_monitor

        # 初始化 MLflow
        _ = get_mlflow_tracker()

        # 初始化缓存管理器
        cache_manager = get_cache_manager()
        await cache_manager.start_cleanup_task(interval=300)  # 每5分钟清理一次

        # 初始化性能监控
        _ = get_performance_monitor()
    if settings.STARTUP_ENABLE_MLOPS:
        if settings.STARTUP_DEFER_OPTIONAL_INIT:
            _start_background_optional_init(app, "mlops", "MLOps 组件", _init_mlops)
        else:
            app.state.startup_state["optional_inits"]["mlops"] = await _safe_optional_init(
                "MLOps 组件",
                _init_mlops,
                settings.STARTUP_OPTIONAL_TIMEOUT_SEC,
            )

    logger.info("🎉 Growth Flywheel 2.5 启动完成！")
    app.state.startup_state["started"] = True
    app.state.startup_state["started_at"] = int(time.time())

    yield

    # 关闭
    logger.info("👋 Growth Flywheel 2.5 关闭中...")

    # 停止在线学习循环
    try:
        from app.ml.rl.online_learning_loop import get_learning_loop
        learning_loop = get_learning_loop()
        if learning_loop.is_running:
            await learning_loop.stop()
            logger.info("✅ 在线学习循环已停止")
    except Exception as e:
        logger.warning(f"⚠️  停止在线学习循环失败: {e}")

    # 停止 MLOps 组件
    try:
        from app.mlops import get_cache_manager
        cache_manager = get_cache_manager()
        await cache_manager.stop_cleanup_task()
        logger.info("✅ MLOps 组件已停止")
    except Exception as e:
        logger.warning(f"⚠️  停止 MLOps 组件失败: {e}")

    await close_db()
    await redis_client.close()
    for task in getattr(app.state, "startup_tasks", []):
        if not task.done():
            task.cancel()
    app.state.startup_state["started"] = False
    app.state.startup_state["core_ready"] = False
    logger.info("✅ 资源清理完成")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="AI 驱动的内容策略进化引擎",
    lifespan=lifespan,
)
app.state.startup_state = {
    "started": False,
    "core_ready": False,
    "optional_inits": {},
    "routers_loaded": [],
    "routers_skipped": [],
    "started_at": None,
}
app.state.startup_tasks = []

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# CSRF 保护
app.add_middleware(
    CSRFMiddleware,
    enabled=settings.ENVIRONMENT == "production"
)

# API 速率限制
app.add_middleware(
    RateLimitMiddleware,
    requests_per_minute=60,
    requests_per_hour=1000,
    burst_size=10,
    enabled=settings.ENVIRONMENT == "production"
)


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "online",
        "environment": settings.ENVIRONMENT,
    }


@app.get("/health")
async def health_check():
    """兼容旧探针：返回存活状态。"""
    return {
        "status": "alive",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


@app.get("/health/live")
async def liveness_check():
    """进程存活探针。"""
    return {
        "status": "alive",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


async def _build_readiness_payload() -> tuple[dict, int]:
    startup_state = getattr(app.state, "startup_state", {}) or {}
    db_ready = await check_db_ready(timeout_sec=settings.HEALTHCHECK_TIMEOUT_SEC)
    redis_ready = await redis_client.ping()
    core_ready = bool(startup_state.get("core_ready"))
    routers_skipped = startup_state.get("routers_skipped", [])

    ready = core_ready and db_ready and redis_ready
    payload = {
        "status": "ready" if ready else "not_ready",
        "core": {
            "startup": core_ready,
            "database": db_ready,
            "redis": redis_ready,
        },
        "startup": {
            "started": bool(startup_state.get("started")),
            "started_at": startup_state.get("started_at"),
            "optional_inits": startup_state.get("optional_inits", {}),
            "routers_loaded": startup_state.get("routers_loaded", []),
            "routers_skipped": routers_skipped,
        },
        "degraded": bool(routers_skipped),
    }
    return payload, (200 if ready else 503)


@app.get("/health/ready")
async def readiness_check():
    """就绪探针：综合启动状态 + DB + Redis。"""
    payload, status_code = await _build_readiness_payload()
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/system/status")
async def system_status():
    """系统状态（包含升级功能状态）"""
    try:
        from app.core.system_upgrade import get_integration
        integration = get_integration()
        status = integration.get_status()

        return {
            "status": "online",
            "version": status['version'],
            "features": status['features'],
            "components": status['components']
        }
    except Exception as e:
        return {
            "status": "online",
            "version": settings.VERSION,
            "error": str(e)
        }


# Prometheus 指标暴露
try:
    from prometheus_client import make_asgi_app, Counter, Histogram
    
    REQUEST_COUNT = Counter(
        "http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"]
    )
    REQUEST_LATENCY = Histogram(
        "http_request_duration_seconds", "HTTP request latency", ["endpoint"]
    )
    RL_SYNC_COUNT = Counter(
        "rl_sync_total", "RL sync operations", ["status"]
    )
    CONTENT_GENERATED = Counter(
        "content_generated_total", "Content generation count", ["platform"]
    )
    
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    @app.middleware("http")
    async def prometheus_middleware(request: Request, call_next):
        # 避免把 /metrics 自己计入延迟统计
        endpoint = request.url.path or "unknown"
        if endpoint == "/metrics":
            return await call_next(request)

        started = time.perf_counter()
        status_code = "500"
        try:
            response = await call_next(request)
            status_code = str(response.status_code)
            return response
        finally:
            duration = max(time.perf_counter() - started, 0.0)
            REQUEST_COUNT.labels(request.method, endpoint, status_code).inc()
            REQUEST_LATENCY.labels(endpoint).observe(duration)

    logger.info("✅ Prometheus /metrics 端点已暴露")
except ImportError:
    logger.warning("⚠️ prometheus-client not installed, /metrics disabled")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
    )
