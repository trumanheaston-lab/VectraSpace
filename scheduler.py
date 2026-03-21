"""
VectraSpace v11 — scheduler.py
Headless pipeline runner for Windows Task Scheduler / cron.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from config import Config
from database import fetch_covariance_cache
from pipeline import run_pipeline

log = logging.getLogger("VectraSpace")
LOCKFILE = Path("vectraspace.lock")


def _acquire_lock() -> bool:
    if LOCKFILE.exists():
        return False
    try:
        LOCKFILE.write_text(str(os.getpid()))
        return True
    except Exception:
        return False


def _release_lock():
    try:
        LOCKFILE.unlink(missing_ok=True)
    except Exception:
        pass


def run_headless(cfg: Config):
    fh = logging.handlers.RotatingFileHandler(
        "vectraspace_scheduled.log", maxBytes=5 * 1024 * 1024, backupCount=3
    )
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger("VectraSpace").addHandler(fh)

    if not _acquire_lock():
        log.warning("Lockfile exists — another run is active. Exiting.")
        sys.exit(1)

    try:
        log.info("Starting headless scheduled run...")
        cov_cache = fetch_covariance_cache(cfg)
        result    = run_pipeline(cfg, covariance_cache=cov_cache,
                                 run_mode="scheduled", user_id=None)
        conj   = result["conjunctions"]
        tracks = result["tracks"]
        log.info(f"Headless run complete — {len(tracks)} sats, {len(conj)} conjunctions")
        sys.exit(0)
    except Exception as e:
        log.error(f"Headless run failed: {e}")
        sys.exit(2)
    finally:
        _release_lock()


def generate_task_xml(python_exe: str, script_path: str,
                      interval_hours: int = 6,
                      output_path: str = "VectraSpace_Task.xml") -> str:
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>VectraSpace v11 Scheduled Run</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition><Interval>PT{interval_hours}H</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary><Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit><Enabled>true</Enabled></Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>main.py --headless</Arguments>
      <WorkingDirectory>{Path(script_path).parent}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""
    Path(output_path).write_text(xml, encoding="utf-16")
    log.info(f"Task Scheduler XML written to {output_path}")
    return xml
