"""Read a window of a tiled GeoTIFF, local or over HTTP, without loading it all.

The PGDA LOLA products are cloud-optimised GeoTIFFs (pgda.gsfc.nasa.gov/products/90:
"All TIF files are cloud-optimized GeoTiffs"; product 81 is tiled the same way).
The 5 m south-pole mosaic is 3.3 GB, so the campaign terrain windows are cut out
of it with HTTP range requests and only the tiles a window touches are fetched.

    with open_geotiff(url_or_path) as g:
        z = g.window(x0, x1, y0, y1)   # metres, polar stereographic
        g.x_left, g.y_top, g.px       # geometry from the GeoTIFF tags

Rows of the returned array run south (as stored). Pixel [r, c] has its centre
at x = x_left + (c + .5) px, y = y_top - (r + .5) px (PixelIsArea tiepoint,
checked against data/lola/README.txt "pixel-registered").
"""
from __future__ import annotations

import io
import urllib.request

import numpy as np
import tifffile


class HttpFile(io.RawIOBase):
    """Seekable read-only file over HTTP range requests, with a block cache."""

    BLOCK = 1 << 20

    def __init__(self, url: str):
        self.url = url
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])
        self.pos = 0
        self.cache: dict[int, bytes] = {}
        self.fetched = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def tell(self):
        return self.pos

    def _block(self, b):
        if b not in self.cache:
            lo = b * self.BLOCK
            hi = min(lo + self.BLOCK, self.size) - 1
            req = urllib.request.Request(self.url, headers={"Range": f"bytes={lo}-{hi}"})
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=120) as r:
                        data = r.read()
                    break
                except OSError:
                    if attempt == 3:
                        raise
            self.cache[b] = data
            self.fetched += len(data)
        return self.cache[b]

    def readinto(self, buf):
        n = min(len(buf), self.size - self.pos)
        if n <= 0:
            return 0
        out = bytearray()
        p = self.pos
        while len(out) < n:
            b, o = divmod(p, self.BLOCK)
            chunk = self._block(b)[o:o + n - len(out)]
            out += chunk
            p += len(chunk)
        buf[:n] = out
        self.pos += n
        return n

    def read_ranges(self, spans):
        """Fetch many (offset, length) spans, merging neighbours into few requests."""
        need = sorted({b for off, ln in spans for b in range(off // self.BLOCK, (off + ln - 1) // self.BLOCK + 1)}
                      - set(self.cache))
        # merge consecutive blocks into one request
        runs, start, prev = [], None, None
        for b in need:
            if start is None:
                start = prev = b
            elif b == prev + 1 and b - start < 32:
                prev = b
            else:
                runs.append((start, prev)); start = prev = b
        if start is not None:
            runs.append((start, prev))
        for b0, b1 in runs:
            lo = b0 * self.BLOCK
            hi = min((b1 + 1) * self.BLOCK, self.size) - 1
            req = urllib.request.Request(self.url, headers={"Range": f"bytes={lo}-{hi}"})
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=300) as r:
                        data = r.read()
                    break
                except OSError:
                    if attempt == 3:
                        raise
            self.fetched += len(data)
            for b in range(b0, b1 + 1):
                self.cache[b] = data[(b - b0) * self.BLOCK:(b - b0 + 1) * self.BLOCK]
        out = []
        for off, ln in spans:
            self.seek(off)
            out.append(self.read(ln))
        return out


class GeoTiff:
    def __init__(self, src: str, level: int = 0):
        self.src = src
        self.fh = HttpFile(src) if src.startswith("http") else open(src, "rb")
        self.tif = tifffile.TiffFile(self.fh)
        geo = self.tif.geotiff_metadata
        tp, sc = geo["ModelTiepoint"], geo["ModelPixelScale"]
        self.page = self.tif.series[0].levels[level].pages[0] if level else self.tif.pages[0]
        self.px = float(sc[0]) * (2 ** level)
        self.x_left, self.y_top = float(tp[3]), float(tp[4])
        self.shape = self.page.shape
        self.geo = geo

    def close(self):
        self.tif.close()
        self.fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def rc(self, x, y):
        """Fractional (row, col) of point (x, y), centre-of-pixel convention."""
        return (self.y_top - y) / self.px - 0.5, (x - self.x_left) / self.px - 0.5

    def window(self, x0, x1, y0, y1):
        """Pixels whose centres fall in [x0, x1) x (y0, y1], rows running south.

        Returns (array, x_centre_of_col0, y_centre_of_row0)."""
        c0 = int(np.ceil((x0 - self.x_left) / self.px - 0.5))
        c1 = int(np.ceil((x1 - self.x_left) / self.px - 0.5))
        r0 = int(np.ceil((self.y_top - y1) / self.px - 0.5))
        r1 = int(np.ceil((self.y_top - y0) / self.px - 0.5))
        return self.read(r0, r1, c0, c1), self.x_left + (c0 + .5) * self.px, self.y_top - (r0 + .5) * self.px

    def read(self, r0, r1, c0, c1):
        p = self.page
        H, W = p.shape[:2]
        th, tw = p.tilelength, p.tilewidth
        ntc = (W + tw - 1) // tw
        out = np.full((r1 - r0, c1 - c0), np.nan, np.float32)
        tr0, tr1 = max(r0, 0) // th, (min(r1, H) - 1) // th
        tc0, tc1 = max(c0, 0) // tw, (min(c1, W) - 1) // tw
        idx = [tr * ntc + tc for tr in range(tr0, tr1 + 1) for tc in range(tc0, tc1 + 1)]
        spans = [(p.dataoffsets[i], p.databytecounts[i]) for i in idx]
        if isinstance(self.fh, HttpFile):
            blobs = self.fh.read_ranges(spans)
        else:
            blobs = []
            for off, ln in spans:
                self.fh.seek(off); blobs.append(self.fh.read(ln))
        for i, data in zip(idx, blobs):
            seg, _, _ = p.decode(data, i)
            seg = np.asarray(seg).reshape(th, tw)
            tr, tc = divmod(i, ntc)
            gr0, gc0 = tr * th, tc * tw
            a0, a1 = max(gr0, r0), min(gr0 + th, r1, H)
            b0, b1 = max(gc0, c0), min(gc0 + tw, c1, W)
            if a1 > a0 and b1 > b0:
                out[a0 - r0:a1 - r0, b0 - c0:b1 - c0] = seg[a0 - gr0:a1 - gr0, b0 - gc0:b1 - gc0]
        return out


def open_geotiff(src: str, level: int = 0) -> GeoTiff:
    return GeoTiff(src, level)
