"""Run two cooperative stages and stop both when either cannot continue."""

from concurrent.futures import ThreadPoolExecutor, as_completed


def run_parallel(first, second, control):
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline") as workers:
        futures = [workers.submit(first), workers.submit(second)]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            control.request(control.status)
            raise
