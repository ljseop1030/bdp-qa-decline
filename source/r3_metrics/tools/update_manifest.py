# -*- coding: utf-8 -*-
"""Regenerate hashes for source/config/reference files; Python 2.7 compatible."""
from __future__ import print_function
import hashlib
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDE = set(('results', 'batch_logs', 'previous-results', '__pycache__', '.git', '.venv'))
EXTENSIONS = ('.py', '.sh', '.json', '.md', '.sql', '.hql', '.txt', '.conf')


def main():
    entries = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE)
        for name in sorted(files):
            if name == 'SHA256SUMS.txt' or not (name.endswith(EXTENSIONS) or name in ('.gitignore', '.gitattributes')):
                continue
            path = os.path.join(base, name)
            with open(path, 'rb') as f:
                content = f.read()
            relative = os.path.relpath(path, ROOT).replace(os.sep, '/')
            entries.append((relative, hashlib.sha256(content).hexdigest()))
    with open(os.path.join(ROOT, 'SHA256SUMS.txt'), 'wb') as f:
        f.write(''.join('%s  %s\n' % (digest, name) for name, digest in sorted(entries)).encode('utf-8'))
    print('Wrote %d source/reference checksums' % len(entries))


if __name__ == '__main__':
    main()
