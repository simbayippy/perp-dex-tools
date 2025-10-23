"""
Funding Rate Service - FastAPI Application

Main application entry point for the funding rate arbitrage service.
Provides REST API endpoints for accessing funding rates, opportunities, and analytics.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database.connection import database
from database.migration_manager import run_startup_migrations
from funding_rate_service.core.mappers import dex_mapper, symbol_mapper
from funding_rate_service.core.fee_calculator import fee_calculator
from funding_rate_service.core.opportunity_finder import OpportunityFinder
from funding_rate_service.core.historical_analyzer import HistoricalAnalyzer
from funding_rate_service.core.dependencies import services
from funding_rate_service.api.routes import funding_rates, opportunities, dexes, health, tasks
from funding_rate_service.utils.logger import logger


# API version
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events
    
    Startup:
    - Connect to database
    - Load mappers
    - Initialize business logic components
    
    Shutdown:
    - Disconnect from database
    """
    # Startup
    logger.info("Starting Funding Rate Service...")
    
    try:
        # Connect to database
        logger.info("Connecting to database...")
        await database.connect()
        logger.info("✅ Database connected")
        
        # Run database migrations
        logger.info("Checking database migrations...")
        migration_success = await run_startup_migrations(database)
        if not migration_success:
            raise Exception("Database migration failed")
        logger.info("✅ Database migrations completed")
        
        # Load mappers
        logger.info("Loading mappers...")
        await dex_mapper.load_from_db(database)
        await symbol_mapper.load_from_db(database)
        logger.info(f"✅ Loaded {len(dex_mapper)} DEXs and {len(symbol_mapper)} symbols")
        
        # Initialize business logic components
        logger.info("Initializing business logic components...")
        
        # Opportunity finder
        opportunity_finder = OpportunityFinder(
            database=database,
            fee_calculator=fee_calculator,
            dex_mapper=dex_mapper,
            symbol_mapper=symbol_mapper
        )
        services.set_opportunity_finder(opportunity_finder)
        logger.info("✅ Opportunity finder initialized")
        
        # Historical analyzer
        historical_analyzer = HistoricalAnalyzer(
            database=database,
            dex_mapper=dex_mapper,
            symbol_mapper=symbol_mapper
        )
        services.set_historical_analyzer(historical_analyzer)
        logger.info("✅ Historical analyzer initialized")
        
        logger.info("🚀 Funding Rate Service API started successfully!")
        
        yield
        
    except Exception as e:
        logger.error(f"Failed to start service: {e}", exc_info=True)
        raise
    
    finally:
        # Shutdown
        logger.info("Shutting down Funding Rate Service API...")
        await database.disconnect()
        logger.info("✅ Database disconnected")
        logger.info("👋 Funding Rate Service API stopped")


# Create FastAPI app
app = FastAPI(
    title="Funding Rate Service",
    description="Funding rate arbitrage opportunities across multiple DEXs",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=f"{API_PREFIX}/docs",
    redoc_url=f"{API_PREFIX}/redoc",
    openapi_url=f"{API_PREFIX}/openapi.json"
)


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": str(exc)
        }
    )


# Include routers
app.include_router(
    funding_rates.router,
    prefix=API_PREFIX,
    tags=["Funding Rates"]
)

app.include_router(
    opportunities.router,
    prefix=API_PREFIX,
    tags=["Opportunities"]
)

app.include_router(
    dexes.router,
    prefix=API_PREFIX,
    tags=["DEXs"]
)

app.include_router(
    health.router,
    prefix=API_PREFIX,
    tags=["Health"]
)

app.include_router(
    tasks.router,
    prefix=API_PREFIX,
    tags=["Background Tasks"]
)


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Funding Rate Service",
        "version": "1.0.0",
        "status": "running",
        "docs": f"{API_PREFIX}/docs"
    }


# Health check (simple, non-database)
@app.get("/ping")
async def ping():
    """Simple ping endpoint"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    
    # Run with: python -m funding_rate_service.main
    uvicorn.run(
        "funding_rate_service.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # Enable auto-reload for development
        log_level="info"
    )