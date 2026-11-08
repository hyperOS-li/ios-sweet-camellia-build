#!/usr/bin/env python3
"""Download only the kernel member from a GitHub Actions artifact zip.

The artifact zip is ~60MB (mostly kdk-libs) but the kernel is ~17MB.  This
tool resolves the artifact's signed URL and reads the zip over HTTP range
requests, extracting only the kernel file.
"""
import http.client
import io
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile

REPO = 'hyperOS-li/ios-sweet-camellia'
TOKEN = os.environ.get('GH_TOKEN', '')


def api_json(url):
    req = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + TOKEN,
        'User-Agent': 'dsh-fetch',
        'Accept': 'application/vnd.github+json',
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


class HTTPRangeFile(io.RawIOBase):
    """Read-only seekable file backed by HTTP Range requests."""

    def __init__(self, url, size, chunk=1 << 20):
        self.url = url
        self.size = size
        self.pos = 0
        self.chunk = chunk
        self.buf = b''
        self.buf_off = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        if whence == 0:
            self.pos = off
        elif whence == 1:
            self.pos += off
        elif whence == 2:
            self.pos = self.size + off
        else:
            raise ValueError('bad whence')
        return self.pos

    def _fill(self, need):
        start = self.pos
        end = min(self.size, start + max(self.chunk, need)) - 1
        last = None
        for attempt in range(8):
            try:
                req = urllib.request.Request(self.url, headers={
                    'Range': 'bytes=%d-%d' % (start, end),
                    'User-Agent': 'dsh-fetch',
                })
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                if not data:
                    raise IOError('empty range response at %d' % start)
                self.buf = data
                self.buf_off = start
                return
            except (urllib.error.URLError, http.client.HTTPException,
                    ConnectionResetError, TimeoutError, OSError) as exc:
                last = exc
                time.sleep(2 + attempt * 2)
        raise last

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n <= 0 or self.pos >= self.size:
            return b''
        out = []
        need = n
        while need > 0 and self.pos < self.size:
            if not (self.buf_off <= self.pos < self.buf_off + len(self.buf)):
                self._fill(need)
            i = self.pos - self.buf_off
            take = min(need, len(self.buf) - i)
            out.append(self.buf[i:i + take])
            self.pos += take
            need -= take
        return b''.join(out)


def resolve_signed_url(zip_url):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(zip_url, headers={
        'Authorization': 'Bearer ' + TOKEN,
        'User-Agent': 'dsh-fetch',
    })
    try:
        with opener.open(req, timeout=60) as r:
            return r.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            loc = exc.headers.get('Location')
            if loc:
                return loc
        raise


def main(run_id, outdir, repo=REPO):
    arts = api_json('https://api.github.com/repos/%s/actions/runs/%s/artifacts'
                    % (repo, run_id))
    cand = [a for a in arts.get('artifacts', []) if a.get('name') == 'xnu-kernel']
    if not cand:
        print('no xnu-kernel artifact')
        return 1
    art = cand[0]
    size = art['size_in_bytes']
    print('artifact id=%s size=%d' % (art['id'], size))
    zip_url = 'https://api.github.com/repos/%s/actions/artifacts/%s/zip' % (repo, art['id'])
    signed = resolve_signed_url(zip_url)
    print('signed url resolved')
    f = HTTPRangeFile(signed, size)
    zf = zipfile.ZipFile(f)
    names = zf.namelist()
    member = None
    exact = [n for n in names if n.rsplit('/', 1)[-1] == 'kernel.development.camellia']
    if exact:
        member = exact[0]
    else:
        for n in names:
            base = n.rsplit('/', 1)[-1]
            if (base.startswith('kernel.') and
                    not base.endswith(('.link', '.a', '.dSYM')) and 'SHA' not in base):
                member = n
                break
    if member is None:
        print('kernel member not found in %r' % names)
        return 1
    print('extracting %s' % member)
    os.makedirs(outdir, exist_ok=True)
    dst = os.path.join(outdir, 'kernel.development.camellia')
    with zf.open(member) as src, open(dst, 'wb') as out:
        shutil.copyfileobj(src, out, 1 << 20)
    print('wrote %s (%d bytes)' % (dst, os.path.getsize(dst)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2],
        sys.argv[3] if len(sys.argv) > 3 else REPO))
