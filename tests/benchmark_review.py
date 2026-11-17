"""Optional isolated benchmark; measurements are not CI timing assertions."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from test_regressions import Regressions
from app.assets import StaticAssets


def run():
    results = {}
    for mode in ('unique_seconds', 'same_timestamp'):
        fixture = Regressions(); fixture.setUp()
        try:
            start = datetime(2026, 11, 1, tzinfo=timezone.utc)
            rows = [fixture.entry(occurred_at=(start + timedelta(seconds=i if mode == 'unique_seconds' else 0)).isoformat(), cause=f'benchmark {i}') for i in range(5000)]
            timings = []
            for expected in (5000, 0):
                before = time.perf_counter()
                response = fixture.request('POST', '/api/import', {'entries': rows})
                timings.append(round(time.perf_counter() - before, 4))
                assert response.status == 200 and response.payload['imported'] == expected, response.payload
            results[mode] = {'import_seconds': timings[0], 'duplicate_seconds': timings[1]}
        finally:
            fixture.doCleanups()
    root = Path(__file__).resolve().parents[1] / 'frontend'
    assets = StaticAssets(root)
    plain = assets.representation(root / 'js/app.js')[0]
    encoded = assets.representation(root / 'js/app.js', True)[0]
    results['app_js_bytes'] = {'identity': len(plain), 'gzip': len(encoded)}
    assets.representation(root / 'sw.js')
    before = time.perf_counter()
    for _ in range(20): assets.representation(root / 'sw.js')
    results['sw_cached_representation_ms'] = round((time.perf_counter() - before) * 1000 / 20, 3)
    print(json.dumps(results, indent=2))


if __name__ == '__main__': run()
