"""Ordered parallel map for the LLM calls (about 5 at a time). Results come back in input order, so outputs do not depend on timing.
On the first exception (for example the spend limit) pending work is cancelled and the exception is re-raised after in-flight calls finish."""
from concurrent.futures import ThreadPoolExecutor

WORKERS = 5


def pmap(fn, items, workers=WORKERS, on_done=None):
    items = list(items)
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fn, x) for x in items]
        try:
            for i, f in enumerate(futures):
                results[i] = f.result()
                if on_done:
                    on_done(i + 1, len(items))
        except BaseException:
            for f in futures:
                f.cancel()
            raise
    return results
