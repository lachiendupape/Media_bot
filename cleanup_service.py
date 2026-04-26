"""Standalone cleanup service entry point.

Run as a separate container alongside media-bot using the same image::

    command: ["python", "cleanup_service.py"]

The service wakes once per day at ``CLEANUP_SCHEDULE_HOUR`` (UTC) and calls
``cleanup_pass()`` which:

1. Reads all pending cleanup_tracking rows that are due for a re-check.
2. Queries Tautulli for watch history across **all** users.
3. Applies the deletion policy:

   - Age ≥ ``CLEANUP_CHECK_INTERVAL_DAYS`` days **and** only the requester
     watched it → delete immediately.
   - Age ≥ ``CLEANUP_MAX_AGE_DAYS`` days → hard-delete regardless of who
     watched.
   - Otherwise → skip (re-check on next cycle).

4. Deletions remove files from disk via Radarr/Sonarr and write a
   ``deletion_notifications`` row so the user sees a notice on their next chat.
"""

import logging
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

import config
import tautulli_usage
from api.radarr import RadarrAPI
from api.sonarr import SonarrAPI
from cleanup import CleanupDB

logging.basicConfig(
    stream=sys.stdout,
    level=config.LOG_LEVEL,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger(__name__)

_db = CleanupDB()


# ---------------------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------------------

def _age_days(added_date_str: str) -> int:
    """Return how many full days have passed since ``added_date_str`` (YYYY-MM-DD)."""
    try:
        added = datetime.fromisoformat(added_date_str).replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    delta = datetime.now(timezone.utc) - added
    return max(0, delta.days)


def _requester_watched(watchers: dict[str, int], requester_username: str) -> bool:
    """True if the requester has at least CLEANUP_MIN_WATCHED_EPISODES plays."""
    count = watchers.get(requester_username, 0)
    return count >= config.CLEANUP_MIN_WATCHED_EPISODES


def _others_watched(watchers: dict[str, int], requester_username: str) -> bool:
    """True if any user *other than* the requester has watched the content."""
    for uname, count in watchers.items():
        if uname.lower() != requester_username.lower() and count >= config.CLEANUP_MIN_WATCHED_EPISODES:
            return True
    return False


# ---------------------------------------------------------------------------
# Deletion helpers
# ---------------------------------------------------------------------------

def _delete_movie(arr_id: int, title: str) -> bool:
    try:
        radarr = RadarrAPI()
        ok = radarr.delete_movie(arr_id, delete_files=True)
        if ok:
            log.info("cleanup.deleted_movie title=%r arr_id=%s", title, arr_id)
        else:
            log.warning("cleanup.delete_movie_failed title=%r arr_id=%s", title, arr_id)
        return ok
    except Exception:
        log.exception("cleanup.delete_movie_error title=%r arr_id=%s", title, arr_id)
        return False


def _delete_series_season(arr_id: int, season_number: int, title: str) -> bool:
    """Delete episode files for a specific season.  If all tracked seasons for
    this series are subsequently deleted, remove the series record too."""
    try:
        sonarr = SonarrAPI()
        files = sonarr.get_episode_files(arr_id, season_number=season_number)
        if files is None:
            log.warning(
                "cleanup.get_episode_files_failed title=%r arr_id=%s season=%s",
                title, arr_id, season_number,
            )
            return False

        if files:
            file_ids = [f['id'] for f in files if isinstance(f, dict) and f.get('id')]
            if file_ids:
                ok = sonarr.delete_episode_files_bulk(file_ids)
                if not ok:
                    log.warning(
                        "cleanup.bulk_delete_failed title=%r arr_id=%s season=%s",
                        title, arr_id, season_number,
                    )
                    return False

        # Unmonitor the season to prevent infinite re-download loops
        sonarr.unmonitor_season(arr_id, season_number)

        log.info(
            "cleanup.deleted_season title=%r arr_id=%s season=%s files=%s",
            title, arr_id, season_number, len(files) if files else 0,
        )

        # Check whether any tracked seasons remain pending; if not, remove the series
        remaining = _db.get_pending_series_seasons(arr_id)
        # Filter out the season we just deleted (it hasn't been marked yet)
        still_tracked = [r for r in remaining if r['season_number'] != season_number]
        if not still_tracked:
            sonarr.delete_series(arr_id, delete_files=False)
            log.info("cleanup.deleted_series title=%r arr_id=%s (all seasons cleaned)", title, arr_id)

        return True
    except Exception:
        log.exception(
            "cleanup.delete_season_error title=%r arr_id=%s season=%s",
            title, arr_id, season_number,
        )
        return False


# ---------------------------------------------------------------------------
# Core cleanup pass
# ---------------------------------------------------------------------------

def cleanup_pass() -> None:
    """Run one full cleanup evaluation cycle."""
    if not config.CLEANUP_ENABLED:
        log.debug("cleanup.pass_skipped: CLEANUP_ENABLED=false")
        return

    log.info("cleanup.pass_start")
    due_rows = _db.get_pending_checks()
    log.info("cleanup.pass_rows_due count=%d", len(due_rows))

    for row in due_rows:
        row_id = row['id']
        media_type = row['media_type']
        arr_id = row['arr_id']
        title = row['title']
        requester_username = row['requester_username']
        requester_plex_id = row['requester_plex_id']
        season_number = row.get('season_number')
        added_date = row['added_date']

        age = _age_days(added_date)
        max_age = max(1, config.CLEANUP_MAX_AGE_DAYS)
        interval = max(1, config.CLEANUP_CHECK_INTERVAL_DAYS)

        # Fetch watch data
        is_tv = (media_type == 'series_season')
        try:
            watchers = tautulli_usage.get_all_watchers_for_title(
                title,
                season_number=season_number if is_tv else None,
                days=max_age + interval,
            )
        except Exception:
            log.exception("cleanup.tautulli_error title=%r — skipping row %s", title, row_id)
            continue

        requester_watched = _requester_watched(watchers, requester_username)
        others_watched = _others_watched(watchers, requester_username)

        # ----- Deletion policy -----
        should_delete = False
        reason = ""

        if age >= max_age:
            should_delete = True
            reason = f"reached max age of {max_age} days"
        elif age >= interval and requester_watched and not others_watched:
            should_delete = True
            reason = f"only requester watched it and content is {age} days old"

        log.debug(
            "cleanup.evaluated title=%r age=%dd requester_watched=%s others_watched=%s delete=%s",
            title, age, requester_watched, others_watched, should_delete,
        )

        if should_delete:
            if is_tv:
                ok = _delete_series_season(arr_id, season_number, title)
            else:
                ok = _delete_movie(arr_id, title)

            if ok:
                _db.mark_deleted(row_id)
                season_str = f" Season {season_number}" if is_tv else ""
                _db.queue_deletion_notification(
                    requester_plex_id,
                    f"\U0001f5d1\ufe0f **{title}{season_str}** has been automatically removed "
                    f"from the library ({reason}).",
                )
        else:
            _db.mark_checked(row_id)

    log.info("cleanup.pass_end")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    log.info(
        "cleanup_service starting: schedule=%02d:00 UTC interval=%dd max_age=%dd",
        config.CLEANUP_SCHEDULE_HOUR,
        config.CLEANUP_CHECK_INTERVAL_DAYS,
        config.CLEANUP_MAX_AGE_DAYS,
    )

    if not config.CLEANUP_ENABLED:
        log.warning("CLEANUP_ENABLED is false — service will run but cleanup_pass() is a no-op.")

    scheduler = BlockingScheduler(timezone='UTC')
    scheduler.add_job(
        cleanup_pass,
        trigger='cron',
        hour=config.CLEANUP_SCHEDULE_HOUR,
        minute=0,
        id='cleanup_pass',
        name='Watch-based media cleanup',
        misfire_grace_time=3600,
    )

    # Also run once at startup so operators can verify it works on first deploy
    log.info("cleanup_service: running initial pass at startup")
    try:
        cleanup_pass()
    except Exception:
        log.exception("cleanup_service: startup pass failed — scheduler will continue")

    log.info("cleanup_service: scheduler started")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("cleanup_service: shutting down")
