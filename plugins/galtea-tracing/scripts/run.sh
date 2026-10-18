#!/bin/sh
# Runs galtea_cc.py with the first Python 3.9+ it finds: python3, python, then the Windows py launcher.
# Claude Code on Windows runs hooks through Git Bash, where python3 is often missing or the Store stub.
# With no usable Python it exits 0: a hook never fails Claude Code.
here=$(dirname "$0")

for c in python3 python "py -3"; do
  command -v "${c%% *}" >/dev/null 2>&1 || continue
  # a Mac without developer tools would open an install dialog for /usr/bin/python3
  if [ "$c" = python3 ] && [ "$(uname -s 2>/dev/null)" = Darwin ] && [ "$(command -v python3)" = /usr/bin/python3 ] \
     && ! xcode-select -p >/dev/null 2>&1; then
    continue
  fi
  $c -c 'import sys; sys.exit(sys.version_info < (3, 9))' </dev/null >/dev/null 2>&1 || continue
  PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 $c "$here/galtea_cc.py" "$@"
  exit 0
done

# A hook prints nothing; a command tells the user why it did nothing.
[ "$1" = hook ] || echo "Galtea tracing needs Python 3.9 or later (tried python3, python, py -3)."
exit 0
