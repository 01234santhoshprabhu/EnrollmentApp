import csv
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


BASE_DIR = Path(__file__).resolve().parent
COURSES_FILE = BASE_DIR / "courses.csv"
REPORT_FILE = BASE_DIR / "enrollment_report.csv"
SUMMARY_FILE = BASE_DIR / "summary.json"

API_URL = "https://onlinecourses.nptel.ac.in/e-learning/api/coursepreview"
TIMEZONE = ZoneInfo("Asia/Kolkata")
MAX_WORKERS = int(os.environ.get("ENROLLMENT_WORKERS", "16"))
SECOND_PASS_WORKERS = int(os.environ.get("ENROLLMENT_SECOND_PASS_WORKERS", "6"))
REQUEST_TIMEOUT = int(os.environ.get("ENROLLMENT_TIMEOUT", "8"))
RETRY_ATTEMPTS = int(os.environ.get("ENROLLMENT_RETRY_ATTEMPTS", "3"))


def now_text():
    return datetime.now(TIMEZONE).strftime("%d-%m-%Y %H:%M:%S")


def extract_course_id(value):
    text = str(value or "").strip()
    match = re.search(r"(noc\d+_[a-z]{2}\d+)", text, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def load_course_ids():
    if not COURSES_FILE.exists():
        raise FileNotFoundError(f"Missing courses file: {COURSES_FILE}")

    course_ids = []
    seen = set()
    with COURSES_FILE.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            return []

        field = "Course_URL" if "Course_URL" in reader.fieldnames else reader.fieldnames[0]
        for row in reader:
            course_id = extract_course_id(row.get(field))
            if course_id and course_id not in seen:
                seen.add(course_id)
                course_ids.append(course_id)
    return course_ids


def parse_count(payload):
    if not isinstance(payload, dict):
        return None

    nested_payload = payload.get("payload")
    if isinstance(nested_payload, str):
        try:
            nested_payload = json.loads(nested_payload)
        except json.JSONDecodeError:
            nested_payload = None
    if isinstance(nested_payload, dict):
        nested_count = parse_count(nested_payload)
        if nested_count is not None:
            return nested_count

    candidates = [
        payload.get("student_count"),
        payload.get("count"),
        payload.get("enrollment_count"),
        payload.get("enrolment_count"),
        payload.get("learners"),
        payload.get("registered_count"),
    ]

    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend(
            [
                data.get("count"),
                data.get("enrollment_count"),
                data.get("enrolment_count"),
                data.get("learners"),
                data.get("registered_count"),
            ]
        )

    for value in candidates:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            digits = re.sub(r"[^\d]", "", value)
            if digits:
                return int(digits)
    return None


def fetch_course(course_id):
    last_error = ""
    try:
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                response = requests.get(
                    API_URL,
                    params={"course_id": course_id},
                    timeout=REQUEST_TIMEOUT,
                    headers={
                        "User-Agent": "Mozilla/5.0 NPTEL enrollment dashboard updater",
                        "Accept": "application/json,text/plain,*/*",
                    },
                )
                payload = response.json()
                if response.status_code == 404 or payload.get("status") == 404:
                    return {
                        "course_id": course_id,
                        "learners_enrolled": "",
                        "status": "Removed / Unavailable",
                        "error": "",
                    }
                response.raise_for_status()
                count = parse_count(payload)
                if isinstance(count, int):
                    return {
                        "course_id": course_id,
                        "learners_enrolled": count,
                        "status": "OK",
                        "error": "",
                    }
                last_error = "API response did not include a numeric enrollment count"
            except Exception as exc:
                last_error = str(exc)
            if attempt < RETRY_ATTEMPTS:
                time.sleep(0.8 * attempt)
        return {
            "course_id": course_id,
            "learners_enrolled": "",
            "status": "Error",
            "error": last_error,
        }
    except requests.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", "")
        status = "Removed / Unavailable" if status_code in (400, 403, 404) else "HTTP error"
        return {
            "course_id": course_id,
            "learners_enrolled": "",
            "status": status,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "course_id": course_id,
            "learners_enrolled": "",
            "status": "Error",
            "error": str(exc),
        }


def run_batch(course_ids, max_workers):
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetch_course, course_id): course_id
            for course_id in course_ids
        }
        completed = 0
        for future in as_completed(future_map):
            completed += 1
            results.append(future.result())
            if completed == len(course_ids) or completed % 50 == 0:
                print(f"Completed {completed}/{len(course_ids)}", flush=True)
    return results


def fetch_all(course_ids):
    print(f"First pass workers: {MAX_WORKERS}", flush=True)
    results = run_batch(course_ids, MAX_WORKERS)
    retry_ids = [row["course_id"] for row in results if row["status"] == "Error"]
    if retry_ids:
        print(f"Second pass retry for {len(retry_ids)} temporary errors", flush=True)
        retry_results = run_batch(retry_ids, SECOND_PASS_WORKERS)
        retry_map = {row["course_id"]: row for row in retry_results}
        results = [retry_map.get(row["course_id"], row) for row in results]

    order = {course_id: index for index, course_id in enumerate(course_ids)}
    results.sort(key=lambda row: order.get(row["course_id"], 0))
    return results


def write_report(rows):
    with REPORT_FILE.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["course_id", "learners_enrolled", "status", "error"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows):
    numeric_rows = [
        row for row in rows if isinstance(row.get("learners_enrolled"), int)
    ]
    error_rows = [row for row in rows if row.get("status") != "OK"]
    top_courses = sorted(
        numeric_rows,
        key=lambda row: row["learners_enrolled"],
        reverse=True,
    )[:20]

    summary = {
        "updated_at": now_text(),
        "course_count": len(rows),
        "numeric_count": len(numeric_rows),
        "error_count": len(error_rows),
        "total_enrollment": sum(row["learners_enrolled"] for row in numeric_rows),
        "top_courses": top_courses,
        "errors": error_rows,
        "rows": rows,
    }
    with SUMMARY_FILE.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)


def main():
    course_ids = load_course_ids()
    if not course_ids:
        raise RuntimeError("No course IDs found in courses.csv")

    print(f"Starting enrollment refresh for {len(course_ids)} courses")
    rows = fetch_all(course_ids)
    write_report(rows)
    write_summary(rows)

    numeric_count = sum(1 for row in rows if row["status"] == "OK")
    total = sum(
        row["learners_enrolled"]
        for row in rows
        if isinstance(row.get("learners_enrolled"), int)
    )
    print(f"Updated {REPORT_FILE}")
    print(f"Updated {SUMMARY_FILE}")
    print(f"Numeric courses: {numeric_count}/{len(rows)}")
    print(f"Total enrollment: {total}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
