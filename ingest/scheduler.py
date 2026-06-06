"""
APScheduler setup for automated daily data pulls.

Schedules:
- Daily prices at 6am
- Daily supply snapshots at 6:15am
- Weekly Scryfall bulk refresh on Sunday at 2am
"""

from apscheduler.schedulers.background import BackgroundScheduler


def setup_scheduler():
    """Initialize and start the background scheduler."""
    scheduler = BackgroundScheduler()
    
    # Prices: daily at 6am
    # scheduler.add_job(pull_daily_prices, 'cron', hour=6)
    
    # Supply snapshots: daily at 6:15am
    # scheduler.add_job(pull_supply_snapshots, 'cron', hour=6, minute=15)
    
    # Scryfall bulk: weekly Sunday at 2am
    # scheduler.add_job(refresh_scryfall_bulk, 'cron', day_of_week='sun', hour=2)
    
    # scheduler.start()
    return scheduler
