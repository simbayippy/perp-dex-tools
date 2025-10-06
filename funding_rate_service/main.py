"""
Main FastAPI application entry point
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from config import settings
from utils.logger import logger
from database.connection import database


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    logger.info("Starting Funding Rate Service...")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Log Level: {settings.log_level}")
    
    # Connect to database
    await database.connect()
    logger.info("Database connected")
    
    # TODO: Initialize mappers
    # TODO: Start background tasks
    
    yield
    
    # Shutdown
    logger.info("Shutting down Funding Rate Service...")
    await database.disconnect()
    logger.info("Database disconnected")


# Create FastAPI app
app = FastAPI(
    title="Funding Rate Service",
    description="API for funding rate data and arbitrage opportunities",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Funding Rate Service",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    # TODO: Add actual health checks
    return {
        "status": "healthy",
        "database": "connected",
        "cache": "connected" if settings.use_redis else "not_used"
    }


# Import and include routers
# TODO: Uncomment as we implement each router
# from api.routes import funding_rates, opportunities, dexes, history, health
# app.include_router(funding_rates.router, prefix="/api/v1", tags=["Funding Rates"])
# app.include_router(opportunities.router, prefix="/api/v1", tags=["Opportunities"])
# app.include_router(dexes.router, prefix="/api/v1", tags=["DEXes"])
# app.include_router(history.router, prefix="/api/v1", tags=["History"])
# app.include_router(health.router, prefix="/api/v1", tags=["Health"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.service_host,
        port=settings.service_port,
        reload=True if settings.environment == "development" else False,
        log_level=settings.log_level.lower()
    )

