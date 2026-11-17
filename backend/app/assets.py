"""Thread-safe static representations and content-derived service-worker versions."""
import gzip
import hashlib
import os
import stat
import threading
from pathlib import Path


def accepts_gzip(header):
    weights = {}
    for part in header.lower().split(','):
        name, *params = part.strip().split(';')
        quality = 1.0
        for param in params:
            key, _, value = param.strip().partition('=')
            if key == 'q':
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        weights[name] = quality
    return weights.get('gzip', weights.get('*', 0)) > 0


class StaticAssets:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._lock = threading.RLock()
        self._files = {}
        self._manifest = None
        self._version = ''

    def _asset_version(self):
        assets, folders = [], [self.root]
        while folders:
            with os.scandir(folders.pop()) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    reparse = bool(getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))
                    if entry.is_dir(follow_symlinks=False):
                        if not reparse:  # Do not recurse into Windows junctions.
                            folders.append(Path(entry.path))
                    elif not entry.name.startswith('__') and entry.is_file():
                        path = Path(entry.path)
                        if entry.is_symlink() or reparse:
                            if not path.resolve().is_relative_to(self.root):
                                continue
                            info = entry.stat()
                        assets.append((path.relative_to(self.root).as_posix(), path, info))
        assets.sort(key=lambda item: item[0])
        manifest = tuple((name, info.st_size, info.st_mtime_ns) for name, _, info in assets)
        if manifest != self._manifest:
            digest = hashlib.sha256()
            for name, path, _ in assets:
                digest.update(name.encode())
                digest.update(path.read_bytes())
            self._version = digest.hexdigest()[:16]
            self._manifest = manifest
        return self._version, max((row[2] / 1e9 for row in manifest), default=0)

    def representation(self, target, gzip_requested=False):
        target = target.resolve()
        target.relative_to(self.root)
        with self._lock:
            stat = target.stat()
            version, modified = self._asset_version() if target.name == 'sw.js' else ('', stat.st_mtime)
            signature = (stat.st_size, stat.st_mtime_ns, version)
            cached = self._files.get(target)
            if cached is None or cached[0] != signature:
                data = target.read_bytes()
                if version:
                    data = data.replace(b'__ASSET_HASH__', version.encode())
                encoded = gzip.compress(data, compresslevel=6, mtime=0) if target.suffix in {'.js', '.css', '.html', '.json', '.svg'} else None
                if len(self._files) >= 64:
                    self._files.clear()
                cached = (signature, data, encoded)
                self._files[target] = cached
            encoded = gzip_requested and cached[2] is not None
            data = cached[2] if encoded else cached[1]
            etag = '"' + hashlib.sha256(data).hexdigest() + '"'
            return data, etag, modified, bool(encoded)
