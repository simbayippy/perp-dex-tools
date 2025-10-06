"""
Database connection management
"""

from databases import Database
from config import settings


# Create database instance
database = Database(
    settings.database_url,
    min_size=settings.database_pool_min_size,
    max_size=settings.database_pool_max_size,
)


async def get_database():
    """Dependency for getting database connection"""
    return database

