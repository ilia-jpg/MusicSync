import datetime
import json
import logging
import math
import multiprocessing as mp
import queue
import time
import warnings
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infrastructure.database import DatabaseManager

logger = logging.getLogger(__name__)

ANALYZER_VERSION = "librosa-ebu-r128-v9-vocal-presence"
NORMALIZATION_METHOD = "ebu_r128"
TARGET_LUFS = -14.0
KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR_KEY_TEMPLATE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_KEY_TEMPLATE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
KEY_CONFIDENCE_THRESHOLD = 0.40
FEATURE_GROUP_VERSIONS: dict[str, str] = {
    "loudness": ANALYZER_VERSION,
    "rhythm": ANALYZER_VERSION,
    "dynamics": ANALYZER_VERSION,
    "tone": ANALYZER_VERSION,
    "harmony": ANALYZER_VERSION,
    "vocals": ANALYZER_VERSION,
}
FEATURE_GROUP_VERSION_COLUMNS = {
    group: f"{group}_version"
    for group in FEATURE_GROUP_VERSIONS
}
FEATURE_GROUP_COLUMNS: dict[str, tuple[str, ...]] = {
    "loudness": (
        "normalization_method",
        "target_lufs",
        "integrated_loudness_lufs",
        "normalization_gain_db",
        "normalized_peak",
        "would_clip",
    ),
    "rhythm": (
        "tempo_bpm",
        "tempo_raw_bpm",
        "tempo_alt_bpm",
        "tempo_variability",
        "onset_density",
    ),
    "dynamics": ("energy_mean", "energy_p90"),
    "tone": ("spectral_brightness", "spectral_flatness"),
    "harmony": ("key_root", "key_scale", "key_confidence", "harmonic_complexity"),
    "vocals": ("instrumentalness",),
}
AUDIO_FEATURE_VALUE_COLUMNS = tuple(
    dict.fromkeys(
        column
        for columns in FEATURE_GROUP_COLUMNS.values()
        for column in columns
    )
)


@dataclass(frozen=True)
class AudioAnalysisWorkerProfile:
    profile_id: str
    label: str
    processes: int
    threads_per_process: int

    @property
    def total_workers(self) -> int:
        return self.processes * self.threads_per_process

    @property
    def display(self) -> str:
        process_label = "process" if self.processes == 1 else "processes"
        thread_label = "thread" if self.threads_per_process == 1 else "threads"
        return f"{self.label} ({self.processes} {process_label} x {self.threads_per_process} {thread_label})"


AUDIO_ANALYSIS_WORKER_PROFILES: dict[str, AudioAnalysisWorkerProfile] = {
    "1p1t": AudioAnalysisWorkerProfile("1p1t", "Serial", 1, 1),
    "1p2t": AudioAnalysisWorkerProfile("1p2t", "Single process tiny", 1, 2),
    "1p4t": AudioAnalysisWorkerProfile("1p4t", "Single process light", 1, 4),
    "1p6t": AudioAnalysisWorkerProfile("1p6t", "Single process medium", 1, 6),
    "1p8t": AudioAnalysisWorkerProfile("1p8t", "Single process standard", 1, 8),
    "1p10t": AudioAnalysisWorkerProfile("1p10t", "Single process high", 1, 10),
    "1p12t": AudioAnalysisWorkerProfile("1p12t", "Single process max", 1, 12),
    "1p16t": AudioAnalysisWorkerProfile("1p16t", "Single process experimental", 1, 16),
    "1p20t": AudioAnalysisWorkerProfile("1p20t", "Single process legacy max", 1, 20),
    "2p1t": AudioAnalysisWorkerProfile("2p1t", "Dual process tiny", 2, 1),
    "2p2t": AudioAnalysisWorkerProfile("2p2t", "Dual process light", 2, 2),
    "2p3t": AudioAnalysisWorkerProfile("2p3t", "Dual process moderate", 2, 3),
    "2p4t": AudioAnalysisWorkerProfile("2p4t", "Dual process balanced", 2, 4),
    "2p6t": AudioAnalysisWorkerProfile("2p6t", "Dual process high", 2, 6),
    "2p8t": AudioAnalysisWorkerProfile("2p8t", "Dual process aggressive", 2, 8),
    "3p2t": AudioAnalysisWorkerProfile("3p2t", "Triple process light", 3, 2),
    "3p3t": AudioAnalysisWorkerProfile("3p3t", "Triple process balanced", 3, 3),
    "3p4t": AudioAnalysisWorkerProfile("3p4t", "Triple process high", 3, 4),
    "4p1t": AudioAnalysisWorkerProfile("4p1t", "Quad process tiny", 4, 1),
    "4p2t": AudioAnalysisWorkerProfile("4p2t", "Quad process light", 4, 2),
    "4p3t": AudioAnalysisWorkerProfile("4p3t", "Quad process moderate", 4, 3),
    "4p4t": AudioAnalysisWorkerProfile("4p4t", "Quad process high memory", 4, 4),
    "4p6t": AudioAnalysisWorkerProfile("4p6t", "Quad process extreme", 4, 6),
    "5p1t": AudioAnalysisWorkerProfile("5p1t", "Five process single-thread", 5, 1),
    "5p2t": AudioAnalysisWorkerProfile("5p2t", "Five process light", 5, 2),
    "6p1t": AudioAnalysisWorkerProfile("6p1t", "Six process single-thread", 6, 1),
    "6p2t": AudioAnalysisWorkerProfile("6p2t", "Six process light", 6, 2),
    "6p3t": AudioAnalysisWorkerProfile("6p3t", "Six process moderate", 6, 3),
    "8p1t": AudioAnalysisWorkerProfile("8p1t", "Eight process single-thread", 8, 1),
    "8p2t": AudioAnalysisWorkerProfile("8p2t", "Eight process light", 8, 2),
    "8p3t": AudioAnalysisWorkerProfile("8p3t", "Eight process high memory", 8, 3),
}
DEFAULT_AUDIO_ANALYSIS_WORKER_PROFILE = "1p8t"

FEATURE_PRESENTATION: dict[str, dict[str, Any]] = {
    "tempo_bpm": {
        "name": "Felt tempo",
        "unit": " BPM",
        "places": 1,
        "labels": ["Slow tempo", "Relaxed tempo", "Medium tempo", "Upbeat tempo", "Fast tempo"],
        "thresholds": [75.0, 95.0, 115.0, 140.0],
    },
    "tempo_raw_bpm": {
        "name": "Detected tempo",
        "unit": " BPM",
        "places": 1,
        "labels": ["Slow tempo", "Relaxed tempo", "Medium tempo", "Upbeat tempo", "Fast tempo"],
        "thresholds": [75.0, 95.0, 115.0, 140.0],
    },
    "tempo_alt_bpm": {
        "name": "Alternate tempo",
        "unit": " BPM",
        "places": 1,
        "labels": ["Slow tempo", "Relaxed tempo", "Medium tempo", "Upbeat tempo", "Fast tempo"],
        "thresholds": [75.0, 95.0, 115.0, 140.0],
    },
    "tempo_variability": {
        "name": "Tempo stability",
        "unit": "",
        "places": 2,
        "labels": ["Steady tempo", "Flexible tempo", "Unstable tempo"],
    },
    "energy_mean": {
        "name": "Intensity",
        "unit": "",
        "places": 4,
        "labels": ["Thin intensity", "Light intensity", "Moderate intensity", "Strong intensity", "Full intensity"],
        "thresholds": [0.150, 0.175, 0.195, 0.215],
    },
    "onset_density": {
        "name": "Activity",
        "unit": "/s",
        "places": 2,
        "labels": ["Sparse activity", "Light activity", "Moderate activity", "Busy activity", "Dense activity"],
        "thresholds": [1.5, 3.0, 5.0, 7.0],
    },
    "energy_p90": {
        "name": "Upper energy",
        "unit": "",
        "places": 4,
        "labels": ["Very low upper", "Low upper", "Moderate upper", "High upper", "Intense upper"],
    },
    "spectral_brightness": {
        "name": "Tone",
        "unit": " Hz",
        "places": 0,
        "labels": ["Dark tone", "Warm tone", "Balanced tone", "Bright tone", "Sharp tone"],
        "thresholds": [1500.0, 2500.0, 3500.0, 5000.0],
    },
    "spectral_flatness": {
        "name": "Texture",
        "unit": "",
        "places": 5,
        "labels": ["Clean texture", "Light texture", "Rough texture", "Gritty texture", "Noisy texture"],
        "thresholds": [0.001, 0.005, 0.020, 0.080],
    },
    "instrumentalness": {
        "name": "Vocals",
        "unit": "",
        "places": 2,
        "labels": ["Vocal-led", "Mixed vocals", "Instrumental"],
        "thresholds": [0.35, 0.75],
    },
    "key_confidence": {
        "name": "Key certainty",
        "unit": "",
        "places": 2,
        "labels": ["Unclear key", "Likely key", "Clear key"],
    },
    "harmonic_complexity": {
        "name": "Harmony",
        "unit": "",
        "places": 2,
        "labels": ["Simple harmony", "Familiar harmony", "Colorful harmony", "Rich harmony", "Complex harmony"],
    },
}

IMPACT_PRESENTATION: dict[str, Any] = {
    "name": "Dynamics",
    "unit": "",
    "places": 2,
    "labels": ["Flat dynamics", "Steady dynamics", "Balanced dynamics", "Punchy dynamics", "Explosive dynamics"],
    "thresholds": [1.25, 1.5, 1.8, 2.2],
}


@dataclass
class AudioFeatureProfile:
    song_id: str
    analysis_status: str
    analysis_error: str | None = None
    analyzed_at: str | None = None
    analyzer_version: str | None = None
    loudness_version: str | None = None
    rhythm_version: str | None = None
    dynamics_version: str | None = None
    tone_version: str | None = None
    harmony_version: str | None = None
    vocals_version: str | None = None
    tempo_bpm: float | None = None
    tempo_raw_bpm: float | None = None
    tempo_alt_bpm: float | None = None
    tempo_variability: float | None = None
    onset_density: float | None = None
    energy_mean: float | None = None
    energy_p90: float | None = None
    spectral_brightness: float | None = None
    spectral_flatness: float | None = None
    instrumentalness: float | None = None
    key_root: str | None = None
    key_scale: str | None = None
    key_confidence: float | None = None
    harmonic_complexity: float | None = None
    normalization_method: str | None = None
    target_lufs: float | None = None
    integrated_loudness_lufs: float | None = None
    normalization_gain_db: float | None = None
    normalized_peak: float | None = None
    would_clip: bool = False
    source_filepath: str | None = None
    source_file_mtime: float | None = None
    source_file_size: int | None = None


@dataclass
class FeatureCalibration:
    values_by_field: dict[str, list[float]]

    @property
    def count(self) -> int:
        counts = [len(values) for values in self.values_by_field.values()]
        return max(counts) if counts else 0


@dataclass
class AudioMetricSummary:
    count: int
    min: float | None = None
    p10: float | None = None
    p25: float | None = None
    median: float | None = None
    p75: float | None = None
    p90: float | None = None
    max: float | None = None


@dataclass
class AudioAnalysisSummary:
    analyzed_count: int
    failed_count: int
    missing_stale_count: int
    metrics: dict[str, AudioMetricSummary]
    key_scale_counts: dict[str, int]


def audio_analysis_profile_options() -> list[dict[str, str]]:
    return [
        {"value": profile.profile_id, "label": profile.display}
        for profile in AUDIO_ANALYSIS_WORKER_PROFILES.values()
    ]


def _analyze_row_in_process(analyzer: "AudioAnalysisManager", row: dict) -> dict[str, Any]:
    started_at = time.perf_counter()
    try:
        return {
            "ok": True,
            "row": row,
            "profile": analyzer._profile_for_row(row),
            "elapsed_seconds": time.perf_counter() - started_at,
        }
    except Exception as exc:
        return {
            "ok": False,
            "row": row,
            "error": str(exc),
            "elapsed_seconds": time.perf_counter() - started_at,
        }


def _audio_analysis_process_worker(worker_id: int, input_queue, output_queue, threads_per_process: int) -> None:
    analyzer = AudioAnalysisManager(db=None, config=None)
    futures = {}
    stopping = False

    def submit_row(executor: ThreadPoolExecutor, row: dict) -> None:
        futures[executor.submit(_analyze_row_in_process, analyzer, row)] = row

    try:
        with ThreadPoolExecutor(max_workers=threads_per_process, thread_name_prefix="AudioAnalysisChild") as executor:
            while True:
                while not stopping and len(futures) < threads_per_process:
                    try:
                        row = input_queue.get_nowait()
                    except queue.Empty:
                        break
                    if row is None:
                        stopping = True
                        break
                    submit_row(executor, row)

                if not futures:
                    if stopping:
                        break
                    try:
                        row = input_queue.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    if row is None:
                        break
                    submit_row(executor, row)
                    continue

                done, _not_done = wait(futures, timeout=0.25, return_when=FIRST_COMPLETED)
                for future in done:
                    futures.pop(future, None)
                    result = future.result()
                    result["worker_id"] = worker_id
                    output_queue.put(result)
    except BaseException as exc:
        output_queue.put({"worker_failed": True, "error": str(exc)})
        return

    output_queue.put({"worker_done": True})


class AudioAnalysisManager:
    def __init__(self, db: DatabaseManager, config=None):
        self.db = db
        self.config = config

    def count_missing_features(self) -> int:
        return len(self._eligible_missing_feature_rows())

    def count_downloaded_features(self) -> int:
        return len(self._eligible_downloaded_feature_rows())

    def analyze_missing_features(self, job_context=None) -> dict:
        rows = self._eligible_missing_feature_rows()
        return self._analyze_rows(rows, job_context=job_context)

    def analyze_downloaded_features(self, job_context=None) -> dict:
        rows = self._eligible_downloaded_feature_rows()
        return self._analyze_rows(rows, job_context=job_context)

    def analyze_song_ids(self, song_ids: list[str], job_context=None) -> dict:
        rows = self._downloaded_rows_for_song_ids(song_ids)
        return self._analyze_rows(rows, job_context=job_context)

    def _analyze_rows(self, rows: list[dict], job_context=None, max_workers: int | None = None) -> dict:
        job_started_at = time.perf_counter()
        total = len(rows)
        complete = 0
        failed = 0
        track_timings: list[tuple[str, float]] = []
        worker_profile = self._worker_profile(max_workers)
        if job_context:
            job_context.update_progress(0, total)
            job_context.update_progress_message(f"Analyzing audio features with {worker_profile.display}")

        if worker_profile.total_workers <= 1 or total <= 1:
            result = self._analyze_rows_serial(rows, job_context=job_context, track_timings=track_timings)
            self._record_worker_profile_benchmark(
                worker_profile,
                result,
                track_timings,
                time.perf_counter() - job_started_at,
                job_context=job_context,
            )
            return result
        if worker_profile.processes > 1:
            result = self._analyze_rows_multiprocess(rows, worker_profile, job_context=job_context, track_timings=track_timings)
            self._record_worker_profile_benchmark(
                worker_profile,
                result,
                track_timings,
                time.perf_counter() - job_started_at,
                job_context=job_context,
            )
            return result

        pending_rows = iter(rows)
        futures = {}
        with ThreadPoolExecutor(max_workers=worker_profile.threads_per_process, thread_name_prefix="AudioAnalysis") as executor:
            while len(futures) < worker_profile.threads_per_process:
                try:
                    row = next(pending_rows)
                except StopIteration:
                    break
                futures[executor.submit(self._profile_for_row, row)] = (row, time.perf_counter())

            completed = 0
            while futures:
                if job_context and job_context.is_cancelled:
                    for future in futures:
                        future.cancel()
                    break
                if job_context:
                    job_context.wait_if_paused()
                done, _not_done = wait(futures, timeout=0.25, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    row, started_at = futures.pop(future)
                    track_timings.append(("main", time.perf_counter() - started_at))
                    completed += 1
                    if job_context:
                        job_context.update_current_item(f"{row.get('artist')} - {row.get('title')}")
                    try:
                        profile = future.result()
                        self._upsert_profile(profile)
                        complete += 1
                    except Exception as exc:
                        failed += 1
                        logger.exception("Audio analysis failed for song_id=%s path=%s", row.get("song_id"), row.get("filepath"))
                        profile = self._failed_profile(row["song_id"], str(exc), path=Path(row["filepath"]))
                        self._upsert_profile(profile)
                    if job_context:
                        job_context.update_progress(completed, total)

                    try:
                        next_row = next(pending_rows)
                    except StopIteration:
                        continue
                    futures[executor.submit(self._profile_for_row, next_row)] = (next_row, time.perf_counter())

        if job_context:
            job_context.update_progress_message(f"Audio analysis complete: {complete} complete, {failed} failed")
        result = {"total": total, "complete": complete, "failed": failed}
        self._record_worker_profile_benchmark(
            worker_profile,
            result,
            track_timings,
            time.perf_counter() - job_started_at,
            job_context=job_context,
        )
        return result

    def _analyze_rows_multiprocess(
        self,
        rows: list[dict],
        worker_profile: AudioAnalysisWorkerProfile,
        job_context=None,
        track_timings: list[tuple[str, float]] | None = None,
    ) -> dict:
        total = len(rows)
        complete = 0
        failed = 0
        completed = 0
        ctx = mp.get_context("spawn")
        input_queue = ctx.Queue()
        output_queue = ctx.Queue()
        processes = [
            ctx.Process(
                target=_audio_analysis_process_worker,
                args=(index, input_queue, output_queue, worker_profile.threads_per_process),
                daemon=True,
            )
            for index in range(worker_profile.processes)
        ]

        for process in processes:
            process.start()
        for row in rows:
            input_queue.put(row)
        for _process in processes:
            input_queue.put(None)

        workers_done = 0
        try:
            while completed < total and workers_done < len(processes):
                if job_context and job_context.is_cancelled:
                    break
                if job_context:
                    job_context.wait_if_paused()
                try:
                    result = output_queue.get(timeout=0.25)
                except queue.Empty:
                    if all(not process.is_alive() for process in processes):
                        break
                    continue

                if result.get("worker_done"):
                    workers_done += 1
                    continue
                if result.get("worker_failed"):
                    workers_done += 1
                    failed += 1
                    logger.error("Audio analysis worker process failed: %s", result.get("error"))
                    continue

                row = result["row"]
                if track_timings is not None:
                    track_timings.append((f"process-{result.get('worker_id', '?')}", float(result.get("elapsed_seconds") or 0.0)))
                completed += 1
                if job_context:
                    job_context.update_current_item(f"{row.get('artist')} - {row.get('title')}")
                if result.get("ok"):
                    self._upsert_profile(result["profile"])
                    complete += 1
                else:
                    failed += 1
                    logger.error(
                        "Audio analysis failed for song_id=%s path=%s: %s",
                        row.get("song_id"),
                        row.get("filepath"),
                        result.get("error"),
                    )
                    profile = self._failed_profile(
                        row["song_id"],
                        result.get("error") or "Audio analysis failed.",
                        path=Path(row["filepath"]),
                    )
                    self._upsert_profile(profile)
                if job_context:
                    job_context.update_progress(completed, total)
        finally:
            for process in processes:
                if process.is_alive() and job_context and job_context.is_cancelled:
                    process.terminate()
                process.join(timeout=2.0)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2.0)
            input_queue.close()
            output_queue.close()

        if job_context:
            job_context.update_progress_message(f"Audio analysis complete: {complete} complete, {failed} failed")
        return {"total": total, "complete": complete, "failed": failed}

    def _analyze_rows_serial(self, rows: list[dict], job_context=None, track_timings: list[tuple[str, float]] | None = None) -> dict:
        total = len(rows)
        complete = 0
        failed = 0

        for index, row in enumerate(rows, 1):
            if job_context and job_context.is_cancelled:
                break
            if job_context:
                job_context.wait_if_paused()
                job_context.update_progress(index - 1, total)
                job_context.update_current_item(f"{row.get('artist')} - {row.get('title')}")
            started_at = time.perf_counter()
            try:
                self.analyze_song(row["song_id"], job_context=job_context, update_job_progress=False)
                complete += 1
            except Exception:
                failed += 1
                logger.warning(
                    "Continuing batch audio analysis after failed track song_id=%s title=%r artist=%r",
                    row["song_id"],
                    row.get("title"),
                    row.get("artist"),
                )
            if track_timings is not None:
                track_timings.append(("main", time.perf_counter() - started_at))
            if job_context:
                job_context.update_progress(index, total)

        if job_context:
            job_context.update_progress_message(f"Audio analysis complete: {complete} complete, {failed} failed")
        return {"total": total, "complete": complete, "failed": failed}

    def analyze_song(self, song_id: str, job_context=None, update_job_progress: bool = True) -> AudioFeatureProfile:
        song = self._get_song_file(song_id)
        if not song:
            profile = self._failed_profile(song_id, "Track must be downloaded before analysis.")
            self._upsert_profile(profile)
            raise RuntimeError(profile.analysis_error)

        title = song.get("title") or "Unknown"
        artist = song.get("artist") or "Unknown Artist"
        path = Path(song["filepath"])
        if job_context:
            if update_job_progress:
                job_context.update_progress(0, 1)
            job_context.update_current_item(f"{artist} - {title}")
            job_context.update_progress_message("Loading audio")
            job_context.wait_if_paused()
        if job_context and job_context.is_cancelled:
            return self.get_song_features(song_id) or self._failed_profile(song_id, "Analysis cancelled.")

        try:
            profile = self._profile_for_row(song, job_context=job_context)
            if job_context and job_context.is_cancelled:
                return profile
            self._upsert_profile(profile)
            if job_context:
                if update_job_progress:
                    job_context.update_progress(1, 1)
                job_context.update_progress_message("Analysis complete")
            return profile
        except Exception as exc:
            logger.exception("Audio analysis failed for song_id=%s path=%s", song_id, path)
            profile = self._failed_profile(song_id, str(exc), path=path)
            self._upsert_profile(profile)
            if job_context:
                if update_job_progress:
                    job_context.update_progress(1, 1)
                job_context.update_progress_message("Analysis failed")
            raise RuntimeError(f"Audio analysis failed: {exc}") from exc

    def _profile_for_row(self, row: dict, job_context=None) -> AudioFeatureProfile:
        song_id = row["song_id"]
        path = Path(row["filepath"])
        if not path.exists():
            raise FileNotFoundError(f"Downloaded file is missing: {path}")
        groups = self._groups_to_analyze(row)
        features = self._existing_feature_values(row)
        features.update(self._extract_features(path, feature_groups=groups, job_context=job_context))
        group_versions = self._existing_group_versions(row)
        for group in groups:
            group_versions[FEATURE_GROUP_VERSION_COLUMNS[group]] = FEATURE_GROUP_VERSIONS[group]
        stat = path.stat()
        return AudioFeatureProfile(
            song_id=song_id,
            analysis_status="COMPLETE",
            analyzed_at=self._now(),
            analyzer_version=ANALYZER_VERSION,
            **group_versions,
            source_filepath=str(path),
            source_file_mtime=stat.st_mtime,
            source_file_size=stat.st_size,
            **features,
        )

    def _worker_profile(self, override: int | None = None) -> AudioAnalysisWorkerProfile:
        if override is not None:
            value = max(1, min(20, int(override)))
            return AudioAnalysisWorkerProfile(f"1p{value}t", "Custom", 1, value)
        raw_value = None
        if self.config is not None:
            raw_value = self.config.get("audio_analysis.worker_profile", None)
            if raw_value is None:
                raw_value = self.config.get("audio_analysis.max_workers", None)
        if raw_value in AUDIO_ANALYSIS_WORKER_PROFILES:
            return AUDIO_ANALYSIS_WORKER_PROFILES[str(raw_value)]
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = AUDIO_ANALYSIS_WORKER_PROFILES[DEFAULT_AUDIO_ANALYSIS_WORKER_PROFILE].threads_per_process
        value = max(1, min(20, value))
        legacy_id = f"1p{value}t"
        return AUDIO_ANALYSIS_WORKER_PROFILES.get(
            legacy_id,
            AudioAnalysisWorkerProfile(legacy_id, "Legacy", 1, value),
        )

    def _record_worker_profile_benchmark(
        self,
        worker_profile: AudioAnalysisWorkerProfile,
        result: dict,
        track_timings: list[tuple[str, float]],
        job_elapsed_seconds: float,
        job_context=None,
    ) -> None:
        included: list[float] = []
        skipped_workers: set[str] = set()
        for worker_id, elapsed_seconds in track_timings:
            if worker_id not in skipped_workers:
                skipped_workers.add(worker_id)
                continue
            if elapsed_seconds > 0.0 and math.isfinite(elapsed_seconds):
                included.append(float(elapsed_seconds))

        status = "cancelled" if job_context and job_context.is_cancelled else "completed"
        log_path = Path("logs") / "audio_analysis_worker_profiles.json"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            if log_path.exists():
                try:
                    data = json.loads(log_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    data = {}
            else:
                data = {}

            profiles = data.setdefault("profiles", {})
            processed_tracks = int(result.get("complete") or 0) + int(result.get("failed") or 0)
            job_elapsed_seconds = max(0.0, float(job_elapsed_seconds))
            job_elapsed_minutes = job_elapsed_seconds / 60.0 if job_elapsed_seconds > 0.0 else 0.0
            effective_tracks_per_minute = (
                processed_tracks / job_elapsed_minutes
                if job_elapsed_minutes > 0.0
                else None
            )
            successful_tracks_per_minute = (
                int(result.get("complete") or 0) / job_elapsed_minutes
                if job_elapsed_minutes > 0.0
                else None
            )
            entry = profiles.setdefault(
                worker_profile.profile_id,
                {
                    "profile_id": worker_profile.profile_id,
                    "label": worker_profile.label,
                    "processes": worker_profile.processes,
                    "threads_per_process": worker_profile.threads_per_process,
                    "jobs": 0,
                    "completed_jobs": 0,
                    "cancelled_jobs": 0,
                    "timed_tracks": 0,
                    "total_timed_seconds": 0.0,
                    "average_seconds_per_track": None,
                    "processed_tracks": 0,
                    "successful_tracks": 0,
                    "total_job_elapsed_seconds": 0.0,
                    "effective_tracks_per_minute": None,
                    "successful_tracks_per_minute": None,
                    "recent_runs": [],
                },
            )
            entry.update({
                "label": worker_profile.label,
                "processes": worker_profile.processes,
                "threads_per_process": worker_profile.threads_per_process,
            })
            entry["jobs"] = int(entry.get("jobs") or 0) + 1
            if status == "cancelled":
                entry["cancelled_jobs"] = int(entry.get("cancelled_jobs") or 0) + 1
            else:
                entry["completed_jobs"] = int(entry.get("completed_jobs") or 0) + 1

            timed_seconds = sum(included)
            timed_tracks = len(included)
            entry["timed_tracks"] = int(entry.get("timed_tracks") or 0) + timed_tracks
            entry["total_timed_seconds"] = float(entry.get("total_timed_seconds") or 0.0) + timed_seconds
            if entry["timed_tracks"]:
                entry["average_seconds_per_track"] = entry["total_timed_seconds"] / entry["timed_tracks"]
            entry["processed_tracks"] = int(entry.get("processed_tracks") or 0) + processed_tracks
            entry["successful_tracks"] = int(entry.get("successful_tracks") or 0) + int(result.get("complete") or 0)
            entry["total_job_elapsed_seconds"] = float(entry.get("total_job_elapsed_seconds") or 0.0) + job_elapsed_seconds
            total_elapsed_minutes = entry["total_job_elapsed_seconds"] / 60.0 if entry["total_job_elapsed_seconds"] > 0.0 else 0.0
            if total_elapsed_minutes > 0.0:
                entry["effective_tracks_per_minute"] = entry["processed_tracks"] / total_elapsed_minutes
                entry["successful_tracks_per_minute"] = entry["successful_tracks"] / total_elapsed_minutes

            run = {
                "finished_at": self._now(),
                "status": status,
                "total": int(result.get("total") or 0),
                "complete": int(result.get("complete") or 0),
                "failed": int(result.get("failed") or 0),
                "processed_tracks": processed_tracks,
                "job_elapsed_seconds": job_elapsed_seconds,
                "effective_tracks_per_minute": effective_tracks_per_minute,
                "successful_tracks_per_minute": successful_tracks_per_minute,
                "raw_timed_tracks": len(track_timings),
                "warmup_skipped_tracks": len(skipped_workers),
                "timed_tracks": timed_tracks,
                "average_seconds_per_track": (timed_seconds / timed_tracks) if timed_tracks else None,
            }
            recent_runs = list(entry.get("recent_runs") or [])
            recent_runs.append(run)
            entry["recent_runs"] = recent_runs[-20:]
            entry["last_run"] = run

            data["updated_at"] = self._now()
            data["notes"] = (
                "average_seconds_per_track excludes the first completed track per worker process to avoid library startup overhead. "
                "effective_tracks_per_minute is (complete + failed) / wall-clock minutes."
            )
            log_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            logger.debug("Failed to update audio analysis worker profile benchmark log.", exc_info=True)

    def _groups_to_analyze(self, row: dict) -> set[str]:
        if row.get("_force_all_feature_groups") or row.get("analysis_status") != "COMPLETE":
            return set(FEATURE_GROUP_VERSIONS)
        stale_groups = {
            group
            for group, version in FEATURE_GROUP_VERSIONS.items()
            if row.get(FEATURE_GROUP_VERSION_COLUMNS[group]) != version
        }
        return stale_groups or set(FEATURE_GROUP_VERSIONS)

    def _existing_feature_values(self, row: dict) -> dict[str, Any]:
        return {
            column: row.get(column)
            for column in AUDIO_FEATURE_VALUE_COLUMNS
        }

    def _existing_group_versions(self, row: dict) -> dict[str, str | None]:
        return {
            column: row.get(column)
            for column in FEATURE_GROUP_VERSION_COLUMNS.values()
        }

    def _feature_select_columns(self, alias: str = "af") -> str:
        columns = [
            "analysis_status",
            "analysis_error",
            "analyzed_at",
            "analyzer_version",
            *FEATURE_GROUP_VERSION_COLUMNS.values(),
            *AUDIO_FEATURE_VALUE_COLUMNS,
        ]
        return ", ".join(f"{alias}.{column} AS {column}" for column in columns)

    def _column_ref(self, alias: str, column: str) -> str:
        return f"{alias}.{column}" if alias else column

    def _current_feature_clause(self, alias: str = "") -> str:
        checks = [
            f"{self._column_ref(alias, FEATURE_GROUP_VERSION_COLUMNS[group])} = ?"
            for group in FEATURE_GROUP_VERSIONS
        ]
        return f"{self._column_ref(alias, 'analysis_status')} = 'COMPLETE' AND " + " AND ".join(checks)

    def _stale_feature_clause(self, alias: str = "") -> str:
        checks = [
            (
                f"{self._column_ref(alias, FEATURE_GROUP_VERSION_COLUMNS[group])} IS NULL "
                f"OR {self._column_ref(alias, FEATURE_GROUP_VERSION_COLUMNS[group])} != ?"
            )
            for group in FEATURE_GROUP_VERSIONS
        ]
        return f"{self._column_ref(alias, 'analysis_status')} = 'COMPLETE' AND (" + " OR ".join(checks) + ")"

    def _feature_version_params(self) -> tuple[str, ...]:
        return tuple(FEATURE_GROUP_VERSIONS.values())

    def get_song_features(self, song_id: str) -> AudioFeatureProfile | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM song_audio_features WHERE song_id = ?", (song_id,))
            row = cursor.fetchone()
        return self._row_to_profile(dict(row)) if row else None

    def is_profile_current(self, profile: AudioFeatureProfile | None) -> bool:
        if not profile or profile.analysis_status != "COMPLETE":
            return False
        return all(
            getattr(profile, FEATURE_GROUP_VERSION_COLUMNS[group]) == version
            for group, version in FEATURE_GROUP_VERSIONS.items()
        )

    def get_feature_calibration(self) -> FeatureCalibration:
        fields = list(FEATURE_PRESENTATION)
        select_clause = ", ".join(fields)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT {select_clause}
                FROM song_audio_features
                WHERE {self._current_feature_clause()}
                """
                ,
                self._feature_version_params(),
            )
            rows = [dict(row) for row in cursor.fetchall()]

        values_by_field: dict[str, list[float]] = {}
        for field in fields:
            values = []
            for row in rows:
                value = row.get(field)
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    values.append(float(value))
            values_by_field[field] = sorted(values)
        values_by_field["impact"] = sorted(
            value
            for row in rows
            if (value := self._impact_value(row.get("energy_mean"), row.get("energy_p90"))) is not None
        )
        return FeatureCalibration(values_by_field)

    def get_analysis_summary(self) -> AudioAnalysisSummary:
        fields = list(FEATURE_PRESENTATION)
        select_clause = ", ".join(fields + ["key_scale"])
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT
                    SUM(CASE WHEN {self._current_feature_clause('af')} THEN 1 ELSE 0 END) AS analyzed_count,
                    SUM(CASE WHEN af.analysis_status = 'FAILED' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(
                        CASE
                            WHEN af.song_id IS NULL
                              OR af.analysis_status != 'COMPLETE'
                              OR {self._stale_feature_clause('af')}
                            THEN 1
                            ELSE 0
                        END
                    ) AS missing_stale_count
                FROM songs s
                JOIN files f ON s.song_id = f.song_id
                LEFT JOIN song_audio_features af ON s.song_id = af.song_id
                WHERE f.downloaded = 1
                  AND s.status != 'ARCHIVED'
                """,
                (*self._feature_version_params(), *self._feature_version_params()),
            )
            counts = dict(cursor.fetchone() or {})
            cursor.execute(
                f"""
                SELECT {select_clause}
                FROM song_audio_features
                WHERE {self._current_feature_clause()}
                """,
                self._feature_version_params(),
            )
            rows = [dict(row) for row in cursor.fetchall()]

        metrics: dict[str, AudioMetricSummary] = {}
        for field in fields:
            values = sorted(
                float(row[field])
                for row in rows
                if isinstance(row.get(field), (int, float)) and math.isfinite(float(row[field]))
            )
            metrics[field] = self._metric_summary(values)
        metrics["impact"] = self._metric_summary(
            sorted(
                value
                for row in rows
                if (value := self._impact_value(row.get("energy_mean"), row.get("energy_p90"))) is not None
            )
        )

        key_scale_counts = {"major": 0, "minor": 0, "fluid": 0}
        for row in rows:
            scale = str(row.get("key_scale") or "fluid").lower()
            if scale not in key_scale_counts:
                scale = "fluid"
            key_scale_counts[scale] += 1

        return AudioAnalysisSummary(
            analyzed_count=int(counts.get("analyzed_count") or 0),
            failed_count=int(counts.get("failed_count") or 0),
            missing_stale_count=int(counts.get("missing_stale_count") or 0),
            metrics=metrics,
            key_scale_counts=key_scale_counts,
        )

    def _metric_summary(self, values: list[float]) -> AudioMetricSummary:
        if not values:
            return AudioMetricSummary(count=0)
        return AudioMetricSummary(
            count=len(values),
            min=values[0],
            p10=self._quantile(values, 0.10),
            p25=self._quantile(values, 0.25),
            median=self._quantile(values, 0.50),
            p75=self._quantile(values, 0.75),
            p90=self._quantile(values, 0.90),
            max=values[-1],
        )

    def _quantile(self, values: list[float], percentile: float) -> float:
        if not values:
            return float("nan")
        if len(values) == 1:
            return values[0]
        position = (len(values) - 1) * percentile
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return values[lower]
        weight = position - lower
        return values[lower] * (1.0 - weight) + values[upper] * weight

    def humanize_profile(self, profile: AudioFeatureProfile, calibration: FeatureCalibration | None = None) -> dict[str, dict[str, Any]]:
        calibration = calibration or self.get_feature_calibration()
        result = {}
        for field, presentation in FEATURE_PRESENTATION.items():
            value = getattr(profile, field)
            if value is None:
                result[field] = {
                    "name": presentation["name"],
                    "value": None,
                    "raw": "Not available",
                    "score": None,
                    "label": "Not available",
                    "display": "Not available",
                }
                continue

            numeric = float(value)
            score = self._score_for_field(field, numeric, presentation, calibration)
            label = self._label_for_score(score, presentation["labels"])
            raw = self._format_numeric(numeric, int(presentation["places"]), presentation["unit"])
            result[field] = {
                "name": presentation["name"],
                "value": numeric,
                "raw": raw,
                "score": score,
                "label": label,
                "display": f"{label} - {raw}",
            }
        return result

    def humanize_impact(self, profile: AudioFeatureProfile, calibration: FeatureCalibration | None = None) -> dict[str, Any]:
        value = self._impact_value(profile.energy_mean, profile.energy_p90)
        if value is None:
            return {
                "name": IMPACT_PRESENTATION["name"],
                "value": None,
                "raw": "Not available",
                "score": None,
                "label": "Not available",
                "display": "Not available",
            }
        calibration = calibration or self.get_feature_calibration()
        score = self._score_for_field("impact", value, IMPACT_PRESENTATION, calibration)
        label = self._label_for_score(score, IMPACT_PRESENTATION["labels"])
        raw = self._format_numeric(value, int(IMPACT_PRESENTATION["places"]), IMPACT_PRESENTATION["unit"])
        return {
            "name": IMPACT_PRESENTATION["name"],
            "value": value,
            "raw": raw,
            "score": score,
            "label": label,
            "display": f"{label} - {raw}",
        }

    def _score_for_field(
        self,
        field: str,
        value: float,
        presentation: dict[str, Any],
        calibration: FeatureCalibration,
    ) -> int:
        thresholds = presentation.get("thresholds")
        if isinstance(thresholds, list) and thresholds:
            return self._threshold_score(value, thresholds)
        return self._percentile_score(value, calibration.values_by_field.get(field, []), len(presentation["labels"]))

    def _threshold_score(self, value: float, thresholds: list[float]) -> int:
        for index, threshold in enumerate(thresholds, 1):
            if value < threshold:
                return index
        return len(thresholds) + 1

    def _impact_value(self, energy_mean: Any, energy_p90: Any) -> float | None:
        if not isinstance(energy_mean, (int, float)) or not isinstance(energy_p90, (int, float)):
            return None
        energy_mean = float(energy_mean)
        energy_p90 = float(energy_p90)
        if energy_mean <= 0.0 or not math.isfinite(energy_mean) or not math.isfinite(energy_p90):
            return None
        return energy_p90 / energy_mean

    def _percentile_score(self, value: float, values: list[float], bucket_count: int) -> int:
        if not values:
            return max(1, min(bucket_count, math.ceil(bucket_count / 2)))
        below_or_equal = 0
        for item in values:
            if item <= value:
                below_or_equal += 1
            else:
                break
        percentile = below_or_equal / len(values)
        return max(1, min(bucket_count, int(math.ceil(percentile * bucket_count))))

    def _label_for_score(self, score: int, labels: list[str]) -> str:
        if not labels:
            return f"{score}/10"
        return labels[max(0, min(score - 1, len(labels) - 1))]

    def _format_numeric(self, value: float, places: int, unit: str) -> str:
        return f"{value:.{places}f}{unit}"

    def _extract_features(self, path: Path, feature_groups: set[str] | None = None, job_context=None) -> dict:
        feature_groups = feature_groups or set(FEATURE_GROUP_VERSIONS)
        try:
            import librosa
            import numpy as np
            import pyloudnorm as pyln
        except ImportError as exc:
            missing = getattr(exc, "name", None) or "audio analysis dependency"
            raise RuntimeError(
                f"Missing audio analysis dependency: {missing}. Install numpy, librosa, and pyloudnorm."
            ) from exc

        y, sr = librosa.load(path, sr=None, mono=True)
        if y is None or len(y) == 0:
            raise ValueError("Audio file decoded to an empty signal.")
        y = np.asarray(y, dtype=np.float64)
        if not np.any(np.isfinite(y)):
            raise ValueError("Audio signal contains no finite samples.")
        y = np.nan_to_num(y)

        if job_context:
            job_context.update_progress_message("Normalizing loudness")
            job_context.wait_if_paused()

        meter = pyln.Meter(sr)
        integrated_loudness = float(meter.integrated_loudness(y))
        if not math.isfinite(integrated_loudness):
            raise ValueError("Integrated loudness could not be measured.")
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Possible clipped samples in output.",
                category=UserWarning,
                module="pyloudnorm.normalize",
            )
            y_norm = pyln.normalize.loudness(y, integrated_loudness, TARGET_LUFS)
        y_norm = np.asarray(y_norm, dtype=np.float64)
        normalized_peak = float(np.max(np.abs(y_norm))) if len(y_norm) else 0.0

        if job_context:
            job_context.update_progress_message("Extracting features")
            job_context.wait_if_paused()

        features: dict[str, Any] = {}
        if "loudness" in feature_groups:
            features.update({
                "normalization_method": NORMALIZATION_METHOD,
                "target_lufs": TARGET_LUFS,
                "integrated_loudness_lufs": self._finite_or_none(integrated_loudness),
                "normalization_gain_db": self._finite_or_none(TARGET_LUFS - integrated_loudness),
                "normalized_peak": self._finite_or_none(normalized_peak),
                "would_clip": normalized_peak > 1.0,
            })

        spectral_brightness = None
        if "rhythm" in feature_groups or "tone" in feature_groups:
            centroid = librosa.feature.spectral_centroid(y=y_norm, sr=sr)[0]
            spectral_brightness = self._finite_or_none(float(np.mean(centroid)))

        if "rhythm" in feature_groups:
            onset_env = librosa.onset.onset_strength(y=y_norm, sr=sr)
            onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="frames")
            duration_seconds = max(float(len(y_norm)) / float(sr), 1e-9)
            onset_density = float(len(onset_frames)) / duration_seconds
            tempo_series = librosa.feature.tempo(onset_envelope=onset_env, sr=sr, aggregate=None)
            tempo_series = np.asarray(tempo_series, dtype=np.float64)
            tempo_series = tempo_series[np.isfinite(tempo_series)]
            tempo_raw_bpm = float(np.median(tempo_series)) if tempo_series.size else None
            tempo_variability = float(np.std(tempo_series)) if tempo_series.size else None
            tempo_bpm, tempo_alt_bpm = self._choose_tempo(
                tempo_raw_bpm,
                tempo_variability,
                spectral_brightness,
                onset_density,
            )
            features.update({
                "tempo_bpm": self._finite_or_none(tempo_bpm),
                "tempo_raw_bpm": self._finite_or_none(tempo_raw_bpm),
                "tempo_alt_bpm": self._finite_or_none(tempo_alt_bpm),
                "tempo_variability": self._finite_or_none(tempo_variability),
                "onset_density": self._finite_or_none(onset_density),
            })

        if "dynamics" in feature_groups:
            rms = librosa.feature.rms(y=y_norm)[0]
            features.update({
                "energy_mean": self._finite_or_none(float(np.mean(rms))),
                "energy_p90": self._finite_or_none(float(np.percentile(rms, 90))),
            })

        if "tone" in feature_groups:
            flatness = librosa.feature.spectral_flatness(y=y_norm)[0]
            features.update({
                "spectral_brightness": spectral_brightness,
                "spectral_flatness": self._finite_or_none(float(np.mean(flatness))),
            })

        if "vocals" in feature_groups:
            instrumentalness = self._estimate_instrumentalness(librosa, np, y_norm, sr)
            features["instrumentalness"] = self._finite_or_none(instrumentalness)

        if "harmony" in feature_groups:
            features.update(self._extract_key_features(librosa, np, y_norm, sr))

        return features

    def _estimate_instrumentalness(self, librosa, np, y, sr: int) -> float | None:
        try:
            y_harmonic, _y_percussive = librosa.effects.hpss(y)
            source = y_harmonic if len(y_harmonic) else y
            n_fft = 2048
            hop_length = 512
            spectrum = np.abs(librosa.stft(source, n_fft=n_fft, hop_length=hop_length)) ** 2
            if spectrum.size == 0:
                return None
            freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
            total_mask = (freqs >= 80.0) & (freqs <= 10000.0)
            vocal_mask = (freqs >= 120.0) & (freqs <= 4000.0)
            core_mask = (freqs >= 300.0) & (freqs <= 3400.0)
            total_energy = float(np.sum(spectrum[total_mask]))
            if total_energy <= 1e-12 or not math.isfinite(total_energy):
                return None
            vocal_ratio = float(np.sum(spectrum[vocal_mask])) / total_energy
            core_ratio = float(np.sum(spectrum[core_mask])) / total_energy
            harmonic_rms = float(np.sqrt(np.mean(np.square(source)))) if len(source) else 0.0
            full_rms = float(np.sqrt(np.mean(np.square(y)))) if len(y) else 0.0
            harmonic_share = harmonic_rms / max(full_rms, 1e-12)

            core_score = self._rescale01(core_ratio, 0.35, 0.70)
            vocal_score = self._rescale01(vocal_ratio, 0.50, 0.85)
            harmonic_score = self._rescale01(harmonic_share, 0.35, 0.80)
            vocal_presence = (0.45 * core_score) + (0.35 * vocal_score) + (0.20 * harmonic_score)
            return max(0.0, min(1.0, 1.0 - vocal_presence))
        except Exception:
            logger.debug("Instrumentalness estimate failed.", exc_info=True)
            return None

    def _rescale01(self, value: float, low: float, high: float) -> float:
        if high <= low or not math.isfinite(value):
            return 0.0
        return max(0.0, min(1.0, (float(value) - low) / (high - low)))

    def _choose_tempo(
        self,
        tempo_raw_bpm: float | None,
        tempo_variability: float | None,
        spectral_brightness: float | None,
        onset_density: float | None,
    ) -> tuple[float | None, float | None]:
        if tempo_raw_bpm is None or tempo_variability is None:
            return tempo_raw_bpm, None
        half_tempo = tempo_raw_bpm / 2.0 if tempo_raw_bpm >= 115.0 else None
        double_tempo = tempo_raw_bpm * 2.0 if tempo_raw_bpm <= 95.0 else None
        three_quarter_tempo = tempo_raw_bpm * 0.75 if tempo_raw_bpm >= 125.0 else None
        one_and_half_tempo = tempo_raw_bpm * 1.5 if 90.0 <= tempo_raw_bpm <= 95.0 else None

        density = onset_density if onset_density is not None else 2.0
        brightness = spectral_brightness if spectral_brightness is not None else 3000.0
        strong_double_time = (
            double_tempo is not None
            and tempo_raw_bpm <= 90.0
            and density >= 3.0
            and tempo_variability >= 18.0
        )
        if strong_double_time:
            return double_tempo, tempo_raw_bpm

        ternary_or_swing_pulse = (
            one_and_half_tempo is not None
            and density >= 3.0
            and density <= 3.8
            and tempo_variability >= 18.0
            and brightness <= 2600.0
        )
        if ternary_or_swing_pulse:
            return one_and_half_tempo, tempo_raw_bpm

        strongly_slow_pulse = (
            half_tempo is not None
            and 115.0 <= tempo_raw_bpm <= 135.0
            and density <= 1.6
            and (tempo_variability >= 18.0 or brightness <= 2400.0)
        )
        high_tempo_half_time = (
            half_tempo is not None
            and tempo_raw_bpm >= 140.0
            and density <= 2.6
        )
        if strongly_slow_pulse or high_tempo_half_time:
            return half_tempo, tempo_raw_bpm
        high_activity_subdivision = (
            three_quarter_tempo is not None
            and 140.0 <= tempo_raw_bpm <= 155.0
            and density >= 3.5
            and tempo_variability >= 18.0
        )
        if high_activity_subdivision:
            return three_quarter_tempo, tempo_raw_bpm
        if double_tempo is not None:
            return tempo_raw_bpm, double_tempo
        if half_tempo is not None:
            return tempo_raw_bpm, half_tempo
        return tempo_raw_bpm, None

    def _extract_key_features(self, librosa, np, y, sr) -> dict:
        hop_length = 2048
        try:
            y_harmonic, _y_percussive = librosa.effects.hpss(y)
            chroma_source = y_harmonic if len(y_harmonic) else y
        except Exception:
            logger.debug("Falling back to full signal for chroma extraction.", exc_info=True)
            chroma_source = y
        chroma = librosa.feature.chroma_cqt(y=chroma_source, sr=sr, hop_length=hop_length)
        profile = np.mean(chroma, axis=1)
        if not np.any(np.isfinite(profile)) or float(np.sum(profile)) <= 0:
            return {
                "key_root": None,
                "key_scale": "fluid",
                "key_confidence": None,
                "harmonic_complexity": None,
            }

        best_root = None
        best_scale = None
        best_score = -1.0
        for scale, template in (("major", MAJOR_KEY_TEMPLATE), ("minor", MINOR_KEY_TEMPLATE)):
            template_array = np.asarray(template, dtype=np.float64)
            for root_index in range(12):
                score = self._pearson_correlation(np, profile, np.roll(template_array, root_index))
                if score is not None and score > best_score:
                    best_score = score
                    best_root = KEY_NAMES[root_index]
                    best_scale = scale

        confidence = max(0.0, min(1.0, float(best_score)))
        harmonic_complexity = self._harmonic_complexity(np, chroma, sr, hop_length)
        key_scale = best_scale if confidence >= KEY_CONFIDENCE_THRESHOLD else "fluid"
        key_root = best_root if key_scale != "fluid" else None
        return {
            "key_root": key_root,
            "key_scale": key_scale,
            "key_confidence": self._finite_or_none(confidence),
            "harmonic_complexity": self._finite_or_none(harmonic_complexity),
        }

    def _harmonic_complexity(self, np, chroma, sr: int, hop_length: int) -> float | None:
        chroma = np.asarray(chroma, dtype=np.float64)
        if chroma.ndim != 2 or chroma.shape[0] != 12 or chroma.shape[1] == 0:
            return None
        chroma = np.nan_to_num(chroma, nan=0.0, posinf=0.0, neginf=0.0)
        chroma = np.maximum(chroma, 0.0)
        chroma = self._smooth_chroma(np, chroma, sr, hop_length)
        frame_energy = np.sum(chroma, axis=0)
        active = frame_energy > 1e-9
        if not np.any(active):
            return None

        active_chroma = chroma[:, active]
        active_energy = frame_energy[active]
        probabilities = active_chroma / np.maximum(active_energy, 1e-12)
        entropy_by_frame = -np.sum(
            np.where(probabilities > 0.0, probabilities * np.log2(np.maximum(probabilities, 1e-12)), 0.0),
            axis=0,
        ) / math.log2(12.0)
        richness = float(np.median(entropy_by_frame))
        return max(0.0, min(1.0, richness))

    def _smooth_chroma(self, np, chroma, sr: int, hop_length: int):
        frames_per_second = float(sr) / float(hop_length or 1)
        window_frames = max(3, int(round(frames_per_second * 2.0)))
        if window_frames % 2 == 0:
            window_frames += 1
        if chroma.shape[1] < window_frames:
            return chroma
        kernel = np.ones(window_frames, dtype=np.float64) / float(window_frames)
        return np.vstack([
            np.convolve(chroma_bin, kernel, mode="same")
            for chroma_bin in chroma
        ])

    def _pearson_correlation(self, np, left, right) -> float | None:
        left = np.asarray(left, dtype=np.float64)
        right = np.asarray(right, dtype=np.float64)
        if float(np.std(left)) == 0.0 or float(np.std(right)) == 0.0:
            return None
        value = float(np.corrcoef(left, right)[0, 1])
        return value if math.isfinite(value) else None

    def _get_song_file(self, song_id: str) -> dict | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT s.song_id, s.title, s.artist, f.filepath
                FROM songs s
                JOIN files f ON s.song_id = f.song_id
                WHERE s.song_id = ?
                  AND f.downloaded = 1
                """,
                (song_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def _eligible_missing_feature_rows(self) -> list[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT s.song_id, s.title, s.artist, f.filepath, {self._feature_select_columns('af')}
                FROM songs s
                JOIN files f ON s.song_id = f.song_id
                LEFT JOIN song_audio_features af ON s.song_id = af.song_id
                WHERE f.downloaded = 1
                  AND s.status != 'ARCHIVED'
                  AND (
                      af.song_id IS NULL
                      OR af.analysis_status != 'COMPLETE'
                      OR {self._stale_feature_clause('af')}
                  )
                ORDER BY s.artist, s.title
                """
                ,
                self._feature_version_params(),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        return [row for row in rows if row.get("filepath") and Path(row["filepath"]).exists()]

    def _eligible_downloaded_feature_rows(self) -> list[dict]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT s.song_id, s.title, s.artist, f.filepath, {self._feature_select_columns('af')}
                FROM songs s
                JOIN files f ON s.song_id = f.song_id
                LEFT JOIN song_audio_features af ON s.song_id = af.song_id
                WHERE f.downloaded = 1
                  AND s.status != 'ARCHIVED'
                ORDER BY s.artist, s.title
                """
            )
            rows = [dict(row) for row in cursor.fetchall()]
        for row in rows:
            row["_force_all_feature_groups"] = True
        return [row for row in rows if row.get("filepath") and Path(row["filepath"]).exists()]

    def _downloaded_rows_for_song_ids(self, song_ids: list[str]) -> list[dict]:
        unique_song_ids = [song_id for song_id in dict.fromkeys(song_ids) if song_id]
        if not unique_song_ids:
            return []
        placeholders = ", ".join("?" for _ in unique_song_ids)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT s.song_id, s.title, s.artist, f.filepath, {self._feature_select_columns('af')}
                FROM songs s
                JOIN files f ON s.song_id = f.song_id
                LEFT JOIN song_audio_features af ON s.song_id = af.song_id
                WHERE s.song_id IN ({placeholders})
                  AND f.downloaded = 1
                  AND s.status != 'ARCHIVED'
                ORDER BY s.artist, s.title
                """,
                unique_song_ids,
            )
            rows = [dict(row) for row in cursor.fetchall()]
        return [row for row in rows if row.get("filepath") and Path(row["filepath"]).exists()]

    def _failed_profile(self, song_id: str, error: str, path: Path | None = None) -> AudioFeatureProfile:
        stat = path.stat() if path and path.exists() else None
        return AudioFeatureProfile(
            song_id=song_id,
            analysis_status="FAILED",
            analysis_error=error,
            analyzed_at=self._now(),
            analyzer_version=ANALYZER_VERSION,
            loudness_version=None,
            rhythm_version=None,
            dynamics_version=None,
            tone_version=None,
            harmony_version=None,
            vocals_version=None,
            normalization_method=NORMALIZATION_METHOD,
            target_lufs=TARGET_LUFS,
            source_filepath=str(path) if path else None,
            source_file_mtime=stat.st_mtime if stat else None,
            source_file_size=stat.st_size if stat else None,
        )

    def _upsert_profile(self, profile: AudioFeatureProfile) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO song_audio_features (
                    song_id, analysis_status, analysis_error, analyzed_at, analyzer_version,
                    loudness_version, rhythm_version, dynamics_version, tone_version, harmony_version, vocals_version,
                    tempo_bpm, tempo_raw_bpm, tempo_alt_bpm, tempo_variability, onset_density,
                    energy_mean, energy_p90,
                    spectral_brightness, spectral_flatness, instrumentalness,
                    key_root, key_scale, key_confidence, harmonic_complexity,
                    normalization_method, target_lufs, integrated_loudness_lufs,
                    normalization_gain_db, normalized_peak, would_clip,
                    source_filepath, source_file_mtime, source_file_size
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(song_id) DO UPDATE SET
                    analysis_status = excluded.analysis_status,
                    analysis_error = excluded.analysis_error,
                    analyzed_at = excluded.analyzed_at,
                    analyzer_version = excluded.analyzer_version,
                    loudness_version = excluded.loudness_version,
                    rhythm_version = excluded.rhythm_version,
                    dynamics_version = excluded.dynamics_version,
                    tone_version = excluded.tone_version,
                    harmony_version = excluded.harmony_version,
                    vocals_version = excluded.vocals_version,
                    tempo_bpm = excluded.tempo_bpm,
                    tempo_raw_bpm = excluded.tempo_raw_bpm,
                    tempo_alt_bpm = excluded.tempo_alt_bpm,
                    tempo_variability = excluded.tempo_variability,
                    onset_density = excluded.onset_density,
                    energy_mean = excluded.energy_mean,
                    energy_p90 = excluded.energy_p90,
                    spectral_brightness = excluded.spectral_brightness,
                    spectral_flatness = excluded.spectral_flatness,
                    instrumentalness = excluded.instrumentalness,
                    key_root = excluded.key_root,
                    key_scale = excluded.key_scale,
                    key_confidence = excluded.key_confidence,
                    harmonic_complexity = excluded.harmonic_complexity,
                    normalization_method = excluded.normalization_method,
                    target_lufs = excluded.target_lufs,
                    integrated_loudness_lufs = excluded.integrated_loudness_lufs,
                    normalization_gain_db = excluded.normalization_gain_db,
                    normalized_peak = excluded.normalized_peak,
                    would_clip = excluded.would_clip,
                    source_filepath = excluded.source_filepath,
                    source_file_mtime = excluded.source_file_mtime,
                    source_file_size = excluded.source_file_size
                """,
                (
                    profile.song_id,
                    profile.analysis_status,
                    profile.analysis_error,
                    profile.analyzed_at,
                    profile.analyzer_version,
                    profile.loudness_version,
                    profile.rhythm_version,
                    profile.dynamics_version,
                    profile.tone_version,
                    profile.harmony_version,
                    profile.vocals_version,
                    profile.tempo_bpm,
                    profile.tempo_raw_bpm,
                    profile.tempo_alt_bpm,
                    profile.tempo_variability,
                    profile.onset_density,
                    profile.energy_mean,
                    profile.energy_p90,
                    profile.spectral_brightness,
                    profile.spectral_flatness,
                    profile.instrumentalness,
                    profile.key_root,
                    profile.key_scale,
                    profile.key_confidence,
                    profile.harmonic_complexity,
                    profile.normalization_method,
                    profile.target_lufs,
                    profile.integrated_loudness_lufs,
                    profile.normalization_gain_db,
                    profile.normalized_peak,
                    1 if profile.would_clip else 0,
                    profile.source_filepath,
                    profile.source_file_mtime,
                    profile.source_file_size,
                ),
            )
            conn.commit()

    def _row_to_profile(self, row: dict) -> AudioFeatureProfile:
        return AudioFeatureProfile(
            song_id=row["song_id"],
            analysis_status=row["analysis_status"],
            analysis_error=row["analysis_error"],
            analyzed_at=row["analyzed_at"],
            analyzer_version=row["analyzer_version"],
            loudness_version=row.get("loudness_version"),
            rhythm_version=row.get("rhythm_version"),
            dynamics_version=row.get("dynamics_version"),
            tone_version=row.get("tone_version"),
            harmony_version=row.get("harmony_version"),
            vocals_version=row.get("vocals_version"),
            tempo_bpm=row["tempo_bpm"],
            tempo_raw_bpm=row["tempo_raw_bpm"],
            tempo_alt_bpm=row["tempo_alt_bpm"],
            tempo_variability=row["tempo_variability"],
            onset_density=row["onset_density"],
            energy_mean=row["energy_mean"],
            energy_p90=row["energy_p90"],
            spectral_brightness=row["spectral_brightness"],
            spectral_flatness=row["spectral_flatness"],
            instrumentalness=row["instrumentalness"],
            key_root=row["key_root"],
            key_scale=row["key_scale"],
            key_confidence=row["key_confidence"],
            harmonic_complexity=row["harmonic_complexity"],
            normalization_method=row["normalization_method"],
            target_lufs=row["target_lufs"],
            integrated_loudness_lufs=row["integrated_loudness_lufs"],
            normalization_gain_db=row["normalization_gain_db"],
            normalized_peak=row["normalized_peak"],
            would_clip=bool(row["would_clip"]),
            source_filepath=row["source_filepath"],
            source_file_mtime=row["source_file_mtime"],
            source_file_size=row["source_file_size"],
        )

    def _now(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _finite_or_none(self, value: float | None) -> float | None:
        if value is None:
            return None
        return value if math.isfinite(value) else None
