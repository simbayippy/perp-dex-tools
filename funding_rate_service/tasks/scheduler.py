"""
Task Scheduler

Manages all background tasks using APScheduler.
Designed for 24/7 VPS operation with robust error handling and monitoring.
"""

import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import atexit

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED

from funding_rate_service.tasks.collection_task import CollectionTask
from funding_rate_service.tasks.opportunity_task import OpportunityTask
from funding_rate_service.tasks.cleanup_task import CleanupTask
from funding_rate_service.utils.logger import logger


class TaskScheduler:
    """
    Central scheduler for all background tasks
    
    Manages:
    - Funding rate collection (every 60 seconds)
    - Opportunity analysis (every 2 minutes)
    - Database cleanup (daily at 2 AM)
    
    Features:
    - Automatic error recovery
    - Task health monitoring
    - Graceful shutdown
    - Job execution tracking
    """
    
    def __init__(self):
        """Initialize task scheduler"""
        self.scheduler = AsyncIOScheduler(
            timezone='UTC',
            job_defaults={
                'coalesce': True,  # Combine multiple pending executions
                'max_instances': 1,  # Only one instance per job
                'misfire_grace_time': 30  # 30 seconds grace for missed jobs
            }
        )
        
        # Task instances
        self.collection_task = CollectionTask()
        self.opportunity_task = OpportunityTask()
        self.cleanup_task = CleanupTask()
        
        # Job execution tracking
        self.job_stats = {
            'collection_job': {'executions': 0, 'errors': 0, 'last_execution': None, 'last_error': None},
            'opportunity_job': {'executions': 0, 'errors': 0, 'last_execution': None, 'last_error': None},
            'cleanup_job': {'executions': 0, 'errors': 0, 'last_execution': None, 'last_error': None}
        }
        
        # Setup event listeners
        self.scheduler.add_listener(self._job_executed, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(self._job_error, EVENT_JOB_ERROR)
        self.scheduler.add_listener(self._job_missed, EVENT_JOB_MISSED)
        
        # Register cleanup on exit
        atexit.register(self.shutdown)
        
        logger.info("TaskScheduler initialized")
    
    async def start(self) -> None:
        """
        Start the scheduler and add all jobs
        """
        logger.info("Starting background task scheduler...")
        
        # Add jobs
        await self._add_jobs()
        
        # Start scheduler
        self.scheduler.start()
        
        logger.info("✅ Background task scheduler started successfully")
        logger.info("📋 Scheduled jobs:")
        logger.info("  • Funding rate collection: Every 60 seconds")
        logger.info("  • Opportunity analysis: Every 2 minutes")
        logger.info("  • Database cleanup: Daily at 2:00 AM UTC")
    
    async def _add_jobs(self) -> None:
        """Add all background jobs to the scheduler"""
        
        # 1. Funding Rate Collection Job (every 60 seconds)
        self.scheduler.add_job(
            func=self._run_collection_job,
            trigger=IntervalTrigger(seconds=60),
            id='collection_job',
            name='Funding Rate Collection',
            replace_existing=True
        )
        
        # 2. Opportunity Analysis Job (every 2 minutes)
        # Offset by 30 seconds to avoid collision with collection
        self.scheduler.add_job(
            func=self._run_opportunity_job,
            trigger=IntervalTrigger(seconds=120, start_date=datetime.utcnow() + timedelta(seconds=30)),
            id='opportunity_job',
            name='Opportunity Analysis',
            replace_existing=True
        )
        
        # 3. Database Cleanup Job (daily at 2:00 AM UTC)
        self.scheduler.add_job(
            func=self._run_cleanup_job,
            trigger=CronTrigger(hour=2, minute=0),
            id='cleanup_job',
            name='Database Cleanup',
            replace_existing=True
        )
        
        logger.info("All background jobs added to scheduler")
    
    async def _run_collection_job(self) -> None:
        """Execute funding rate collection job"""
        try:
            logger.debug("🔄 Running funding rate collection job...")
            result = await self.collection_task.run()
            
            if result['status'] == 'success':
                logger.debug(f"✅ Collection job completed: {result['result']['total_rates_collected']} rates")
            else:
                logger.warning(f"⚠️ Collection job failed: {result.get('error', 'Unknown error')}")
                
        except Exception as e:
            logger.error(f"❌ Collection job exception: {e}", exc_info=True)
    
    async def _run_opportunity_job(self) -> None:
        """Execute opportunity analysis job"""
        try:
            logger.debug("📈 Running opportunity analysis job...")
            result = await self.opportunity_task.run()
            
            if result['status'] == 'success':
                profitable = result['result'].get('profitable_opportunities', 0)
                logger.debug(f"✅ Opportunity job completed: {profitable} profitable opportunities")
            else:
                logger.warning(f"⚠️ Opportunity job failed: {result.get('error', 'Unknown error')}")
                
        except Exception as e:
            logger.error(f"❌ Opportunity job exception: {e}", exc_info=True)
    
    async def _run_cleanup_job(self) -> None:
        """Execute database cleanup job"""
        try:
            logger.info("🧹 Running database cleanup job...")
            result = await self.cleanup_task.run()
            
            if result['status'] == 'success':
                deleted = result['result'].get('total_records_deleted', 0)
                logger.info(f"✅ Cleanup job completed: {deleted} records deleted")
            else:
                logger.warning(f"⚠️ Cleanup job failed: {result.get('error', 'Unknown error')}")
                
        except Exception as e:
            logger.error(f"❌ Cleanup job exception: {e}", exc_info=True)
    
    def _job_executed(self, event) -> None:
        """Handle successful job execution"""
        job_id = event.job_id
        if job_id in self.job_stats:
            self.job_stats[job_id]['executions'] += 1
            self.job_stats[job_id]['last_execution'] = datetime.utcnow()
    
    def _job_error(self, event) -> None:
        """Handle job execution error"""
        job_id = event.job_id
        if job_id in self.job_stats:
            self.job_stats[job_id]['errors'] += 1
            self.job_stats[job_id]['last_error'] = datetime.utcnow()
        
        logger.error(f"Job {job_id} failed: {event.exception}")
    
    def _job_missed(self, event) -> None:
        """Handle missed job execution"""
        job_id = event.job_id
        logger.warning(f"Job {job_id} missed execution at {event.scheduled_run_time}")
    
    async def shutdown(self) -> None:
        """
        Gracefully shutdown the scheduler
        """
        logger.info("Shutting down task scheduler...")
        
        try:
            # Stop scheduler
            if self.scheduler.running:
                self.scheduler.shutdown(wait=True)
            
            # Close task resources
            await self.collection_task.close()
            
            logger.info("✅ Task scheduler shutdown complete")
            
        except Exception as e:
            logger.error(f"Error during scheduler shutdown: {e}")
    
    def get_scheduler_status(self) -> Dict[str, Any]:
        """
        Get scheduler status and statistics
        
        Returns:
            Dictionary with scheduler status
        """
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'name': job.name,
                'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                'trigger': str(job.trigger)
            })
        
        return {
            'running': self.scheduler.running,
            'jobs_count': len(jobs),
            'jobs': jobs,
            'job_statistics': {
                job_id: {
                    **stats,
                    'last_execution': stats['last_execution'].isoformat() if stats['last_execution'] else None,
                    'last_error': stats['last_error'].isoformat() if stats['last_error'] else None,
                    'success_rate': (
                        (stats['executions'] - stats['errors']) / stats['executions'] * 100
                        if stats['executions'] > 0 else 0
                    )
                }
                for job_id, stats in self.job_stats.items()
            }
        }
    
    def get_task_health(self) -> Dict[str, Any]:
        """
        Get health status of all tasks
        
        Returns:
            Dictionary with task health information
        """
        return {
            'collection_task': {
                'metrics': self.collection_task.get_metrics(),
                'is_healthy': self.collection_task.is_healthy(),
                'is_running': self.collection_task.is_running()
            },
            'opportunity_task': {
                'metrics': self.opportunity_task.get_metrics(),
                'is_healthy': self.opportunity_task.is_healthy(),
                'is_running': self.opportunity_task.is_running()
            },
            'cleanup_task': {
                'metrics': self.cleanup_task.get_metrics(),
                'is_healthy': self.cleanup_task.is_healthy(),
                'is_running': self.cleanup_task.is_running()
            }
        }
    
    async def force_run_job(self, job_id: str) -> Dict[str, Any]:
        """
        Force run a specific job immediately
        
        Args:
            job_id: ID of the job to run ('collection_job', 'opportunity_job', 'cleanup_job')
            
        Returns:
            Job execution result
        """
        logger.info(f"🔄 Force running job: {job_id}")
        
        if job_id == 'collection_job':
            return await self.collection_task.force_collection()
        elif job_id == 'opportunity_job':
            return await self.opportunity_task.force_analysis()
        elif job_id == 'cleanup_job':
            return await self.cleanup_task.force_cleanup()
        else:
            raise ValueError(f"Unknown job ID: {job_id}")
    
    def pause_job(self, job_id: str) -> None:
        """Pause a specific job"""
        self.scheduler.pause_job(job_id)
        logger.info(f"⏸️ Paused job: {job_id}")
    
    def resume_job(self, job_id: str) -> None:
        """Resume a specific job"""
        self.scheduler.resume_job(job_id)
        logger.info(f"▶️ Resumed job: {job_id}")
    
    def get_cached_opportunities(self, cache_type: str = 'best_overall') -> List[Dict[str, Any]]:
        """
        Get cached opportunities from opportunity task
        
        Args:
            cache_type: Type of cached opportunities
            
        Returns:
            List of cached opportunities
        """
        return self.opportunity_task.get_cached_opportunities(cache_type)
    
    def get_opportunity_cache_stats(self) -> Dict[str, Any]:
        """Get opportunity cache statistics"""
        return self.opportunity_task.get_cache_stats()
