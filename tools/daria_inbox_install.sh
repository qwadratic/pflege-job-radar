#!/bin/sh
# One-time install, run by Ivan: sudo sh /home/claude/repo/pflege-board/tools/daria_inbox_install.sh
#
# TASK-345.10. Ivan, 2026-09-28, granted the claude user one root command without a password:
# /usr/local/sbin/daria-inbox (tools/daria_inbox.py), which prints the mail of daria.s@pflege-connect.work. Root runs
# only the root-owned copy installed here, never the repo's file. Rerun after a change to tools/daria_inbox.py.
# Remove: sudo rm /etc/sudoers.d/daria-inbox /usr/local/sbin/daria-inbox
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
src=$(dirname "$(readlink -f "$0")")
rm -rf /usr/local/lib/daria-inbox        # the first version's copy of email_dump_graph.py; the command is self-contained now
install -o root -g root -m 755 "$src/daria_inbox.py" /usr/local/sbin/daria-inbox
# the sudo rule goes in only after the command works: token, Graph and the .env checks, messages from the last minute
/usr/local/sbin/daria-inbox --since "$(date -d '1 minute ago' -Is)" > /dev/null
echo "daria-inbox works"
rule=$(mktemp)
cat > "$rule" <<'EOF'
# TASK-345.10, Ivan 2026-09-28: the claude user reads daria.s@pflege-connect.work through this one root-owned command.
claude ALL=(root) NOPASSWD: /usr/local/sbin/daria-inbox
EOF
visudo -cqf "$rule"
install -o root -g root -m 440 "$rule" /etc/sudoers.d/daria-inbox
rm -f "$rule"
echo "installed /usr/local/sbin/daria-inbox and /etc/sudoers.d/daria-inbox"
